"""Executor — the LLM ⇄ tools loop that produces a reply.

Runs a bounded function-calling loop: ask the model (with the usable tool defs);
if it requests tools, invoke them via the tools backend and feed results back;
repeat until the model answers or the iteration cap is hit.

Key contract behaviors:
- A tool's controlled failure (`ok:false`) is fed back to the model as a signal,
  never raised — the model can read the reason and adapt.
- Probation-tool output is flagged (`used_probation`) so memory won't promote it
  to fact (spec §2.1).
- Every tool invocation passes the AUTO gate and counts as a budgeted action.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.autonomy.tiers import gate
from agent.llm.base import ChatMessage, LLMResult, ToolCall
from agent.loop.context import LoopContext
from agent.tools.client import ToolTransportError


@dataclass(slots=True)
class ToolInvocationRecord:
    name: str
    input: dict[str, Any]
    ok: bool
    output: dict[str, Any] | None
    error: str | None
    tool_version: int | None
    sandboxed: bool | None
    from_probation: bool
    tool_message: str
    # True when the model called a tool that does not exist → a capability gap.
    unknown_tool: bool = False


@dataclass(slots=True)
class ReplyOutcome:
    text: str
    tok_in: int
    tok_out: int
    cost_usd: float
    llm_calls: int
    tools: list[ToolInvocationRecord] = field(default_factory=list)
    used_probation: bool = False


def _assistant_echo(result: LLMResult) -> ChatMessage:
    """Rebuild the assistant message (with tool_calls) to append to history."""
    if result.raw_message is not None and result.raw_message.get("tool_calls"):
        return ChatMessage(
            role="assistant",
            content=result.raw_message.get("content"),
            tool_calls=result.raw_message["tool_calls"],
        )
    tool_calls = [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
        }
        for tc in result.tool_calls
    ]
    return ChatMessage(role="assistant", content=result.text or None, tool_calls=tool_calls)


async def run_reply(
    ctx: LoopContext, user_text: str, trace_id: str, *, context_block: str = ""
) -> ReplyOutcome:
    system = ctx.system_prompt
    if context_block:
        system = f"{system}\n\nRelevant memory (may be imperfect):\n{context_block}"
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user_text),
    ]
    tool_defs = (
        ctx.tool_cache.function_defs()
        if ctx.tool_cache is not None and ctx.tools_client is not None
        else None
    )

    records: list[ToolInvocationRecord] = []
    tot_in = tot_out = llm_calls = 0
    cost = 0.0
    used_probation = False

    for _ in range(ctx.max_tool_iters):
        result = await ctx.llm.complete(
            messages, tools=tool_defs, max_tokens=ctx.max_reply_tokens
        )
        llm_calls += 1
        tot_in += result.tok_in
        tot_out += result.tok_out
        cost += result.cost_usd

        if not result.tool_calls:
            return ReplyOutcome(
                text=result.text,
                tok_in=tot_in,
                tok_out=tot_out,
                cost_usd=cost,
                llm_calls=llm_calls,
                tools=records,
                used_probation=used_probation,
            )

        messages.append(_assistant_echo(result))
        for tc in result.tool_calls:
            record = await _invoke_tool(ctx, tc, trace_id)
            records.append(record)
            used_probation = used_probation or record.from_probation
            messages.append(
                ChatMessage(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=record.tool_message,
                )
            )

    # Iteration cap hit — force a tool-free final answer.
    final = await ctx.llm.complete(messages, max_tokens=ctx.max_reply_tokens)
    llm_calls += 1
    tot_in += final.tok_in
    tot_out += final.tok_out
    cost += final.cost_usd
    return ReplyOutcome(
        text=final.text or "Maaf, aku belum bisa menyelesaikan permintaan itu.",
        tok_in=tot_in,
        tok_out=tot_out,
        cost_usd=cost,
        llm_calls=llm_calls,
        tools=records,
        used_probation=used_probation,
    )


async def _invoke_tool(ctx: LoopContext, tc: ToolCall, trace_id: str) -> ToolInvocationRecord:
    assert ctx.tool_cache is not None and ctx.tools_client is not None
    entry = ctx.tool_cache.get(tc.name)
    if entry is None or entry.is_disabled:
        msg = json.dumps({"error": {"code": "UNKNOWN_TOOL", "message": tc.name}})
        return ToolInvocationRecord(
            name=tc.name, input=tc.arguments, ok=False, output=None,
            error="unknown_or_disabled_tool", tool_version=None, sandboxed=None,
            from_probation=False, tool_message=msg,
            # A missing tool (not a disabled one) is a capability gap worth forging.
            unknown_tool=entry is None,
        )

    decision = await gate("tool.invoke", drift_paused=ctx.drift_paused)
    if not decision.allowed:
        msg = json.dumps({"error": {"code": "BLOCKED", "message": decision.reason}})
        return ToolInvocationRecord(
            name=tc.name, input=tc.arguments, ok=False, output=None,
            error=decision.reason, tool_version=None, sandboxed=None,
            from_probation=False, tool_message=msg,
        )
    await ctx.budget.record_action(1)

    try:
        res = await ctx.tools_client.invoke(
            tc.name, input=tc.arguments, trace_id=trace_id
        )
    except ToolTransportError as exc:
        msg = json.dumps({"error": {"code": "TRANSPORT", "message": exc.message}})
        return ToolInvocationRecord(
            name=tc.name, input=tc.arguments, ok=False, output=None,
            error=exc.message, tool_version=None, sandboxed=None,
            from_probation=ctx.tool_cache.is_probation(tc.name), tool_message=msg,
        )

    res.from_probation = ctx.tool_cache.is_probation(tc.name)
    return ToolInvocationRecord(
        name=tc.name,
        input=tc.arguments,
        ok=res.ok,
        output=res.output,
        error=(res.error.message if res.error else None),
        tool_version=res.tool_version,
        sandboxed=res.sandboxed,
        from_probation=res.from_probation,
        tool_message=res.as_tool_message(),
    )
