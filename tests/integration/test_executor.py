"""Executor tool-loop tests (no network): scripted LLM + fake tools backend."""

import fakeredis.aioredis

from agent.autonomy.budget import BudgetManager
from agent.bus.streams import EventBus
from agent.channels.base import ChannelRegistry
from agent.llm.base import BudgetedLLM
from agent.llm.recorder import NullCostRecorder
from agent.loop.context import LoopContext
from agent.loop.executor import run_reply
from agent.tools.cache import ToolCache
from agent.tools.models import InvokeError, InvokeResult, ToolEntry
from tests.fakes import FakeToolsClient, ScriptedLLM, final_result, tool_call_result


def _entry(name: str, status: str = "active") -> ToolEntry:
    return ToolEntry(
        name=name,
        version=2,
        description=f"{name}",
        params_schema={"type": "object", "properties": {}},
        status=status,
        timeout_ms=1000,
    )


async def _ctx(
    script: list, tools: list[ToolEntry], results: dict[str, InvokeResult]
) -> tuple[LoopContext, FakeToolsClient]:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    budget = BudgetManager(
        redis, default_tokens=100_000, default_cost_usd=10.0, default_actions=1000
    )
    llm = BudgetedLLM(ScriptedLLM(script), budget, NullCostRecorder())
    client = FakeToolsClient(tools=tools, results=results)
    cache = ToolCache(client)
    await cache.refresh()
    ctx = LoopContext(
        dsn="",
        bus=EventBus(redis),
        budget=budget,
        llm=llm,
        channels=ChannelRegistry(),
        tools_client=client,  # type: ignore[arg-type]
        tool_cache=cache,
    )
    return ctx, client


async def test_tool_loop_invokes_then_answers() -> None:
    script = [
        tool_call_result("c1", "echo", {"x": 1}),
        final_result("done with tool"),
    ]
    ctx, client = await _ctx(
        script,
        tools=[_entry("echo")],
        results={"echo": InvokeResult(ok=True, output={"echoed": 1}, tool_version=2,
                                      sandboxed=True)},
    )

    outcome = await run_reply(ctx, "please echo", "trace-x")

    assert outcome.text == "done with tool"
    assert outcome.llm_calls == 2
    assert len(outcome.tools) == 1
    rec = outcome.tools[0]
    assert rec.name == "echo" and rec.ok is True
    assert rec.from_probation is False
    # The tool was actually invoked with trace propagated.
    assert client.invocations == [("echo", {"x": 1}, "trace-x")]
    # One budgeted action for the tool call.
    usage = await ctx.budget.get_usage()
    assert usage.actions == 1


async def test_probation_output_is_flagged() -> None:
    script = [tool_call_result("c1", "beta", {}), final_result("answer")]
    ctx, _client = await _ctx(
        script,
        tools=[_entry("beta", status="probation")],
        results={"beta": InvokeResult(ok=True, output={"v": 1})},
    )
    outcome = await run_reply(ctx, "use beta", "t")
    assert outcome.used_probation is True
    assert outcome.tools[0].from_probation is True


async def test_ok_false_is_fed_back_not_raised() -> None:
    script = [tool_call_result("c1", "echo", {}), final_result("handled the error")]
    ctx, _client = await _ctx(
        script,
        tools=[_entry("echo")],
        results={
            "echo": InvokeResult(
                ok=False, error=InvokeError(code="RUNTIME", message="boom")
            )
        },
    )
    outcome = await run_reply(ctx, "do it", "t")
    assert outcome.text == "handled the error"
    assert outcome.tools[0].ok is False
    assert "boom" in outcome.tools[0].tool_message


async def test_unknown_tool_is_reported_without_backend_call() -> None:
    script = [tool_call_result("c1", "ghost", {}), final_result("no such tool")]
    ctx, client = await _ctx(script, tools=[_entry("echo")], results={})
    outcome = await run_reply(ctx, "call ghost", "t")
    assert outcome.tools[0].ok is False
    assert "unknown" in (outcome.tools[0].error or "")
    # Backend was never asked to invoke the unknown tool.
    assert client.invocations == []
