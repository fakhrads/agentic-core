"""AgentLoop — end-to-end handling of one inbound message.

Flow: create episode → (AUTO gate) generate reply via budgeted LLM → (AUTO gate)
send reply → persist reply step + close episode. Audit events are published at
each stage. Cost/budget accounting happens inside the LLM wrapper + on send.

Sessions are opened sequentially (never nested) so the independent cost
recorder can write `llm_call` rows during the LLM call without lock contention.
"""

from __future__ import annotations

import time

from agent.autonomy.budget import BudgetExceeded
from agent.autonomy.tiers import gate
from agent.bus.events import STREAM_AUDIT, Event, EventType
from agent.channels.base import InboundMessage
from agent.db.base import session_scope
from agent.db.models import ARTEFACT_MEMORY, EPISODE_DONE, EPISODE_FAILED
from agent.db.repo import (
    add_step,
    create_episode,
    end_episode,
    get_episode_by_trace,
    record_artefact_use,
)
from agent.logging import get_logger
from agent.loop.context import LoopContext
from agent.loop.executor import ReplyOutcome, run_reply
from agent.loop.planner import plan
from agent.memory.retrieval import hybrid_search, mark_retrieved
from agent.playbook.context import build_context
from agent.playbook.store import PlaybookStore
from agent.tools.needs import record_tool_need
from agent.trace import set_episode_id, trace_context

log = get_logger("loop")

_BUDGET_MESSAGE = "Maaf, budget harian sudah tercapai. Coba lagi besok ya."
_ERROR_MESSAGE = "Maaf, ada kesalahan internal saat memproses pesanmu."


class AgentLoop:
    def __init__(self, ctx: LoopContext) -> None:
        self.ctx = ctx

    async def _audit(self, event_type: str, trace_id: str, message: str, **payload: object) -> None:
        await self.ctx.bus.publish(
            STREAM_AUDIT,
            Event(
                type=event_type,
                trace_id=trace_id,
                component="loop",
                message=message,
                payload=payload,
            ),
        )

    async def _retrieve_memory(self, episode_id: int, query: str) -> str:
        """Retrieve relevant active memory, record usage, and return a context
        block for the prompt. Best-effort: any failure returns "" and never
        breaks the episode (retrieval is an enhancement, not a dependency)."""
        if self.ctx.embedder is None:
            return ""
        try:
            vector = await self.ctx.embedder.embed(query)
            async with session_scope(self.ctx.dsn) as session:
                hits = await hybrid_search(session, vector, limit=self.ctx.retrieval_k)
                if not hits:
                    return ""
                await mark_retrieved(session, [item for item, _ in hits])
                for item, _score in hits:
                    await record_artefact_use(
                        session, episode_id=episode_id, kind=ARTEFACT_MEMORY, ref_id=item.id
                    )
                return "\n".join(f"- {item.content}" for item, _ in hits)
        except Exception as exc:  # noqa: BLE001 — retrieval must never wedge the loop
            log.warning("retrieval_failed", error=str(exc))
            return ""

    def _playbook_context(self) -> str:
        """Durable memory from the playbook files. Best-effort, like retrieval:
        an unreadable playbook degrades the reply, it never blocks it."""
        if self.ctx.playbook_dir is None:
            return ""
        try:
            return build_context(PlaybookStore(self.ctx.playbook_dir))
        except Exception as exc:  # noqa: BLE001 — context is an enhancement
            log.warning("playbook_context_failed", error=str(exc))
            return ""

    async def _send(self, inbound: InboundMessage, text: str) -> bool:
        """Send a reply through the AUTO gate. Returns True if it went out."""
        if not text.strip():
            # Belt-and-braces: an empty send reaches the user as silence, which
            # is indistinguishable from the agent being down. Callers already
            # substitute a fallback; this catches any path that doesn't.
            log.error("send_empty_reply_suppressed", channel=inbound.channel)
            return False
        decision = await gate("chat.send", drift_paused=self.ctx.drift_paused)
        if not decision.allowed:
            log.warning("send_blocked", reason=decision.reason)
            return False
        channel = self.ctx.channels.get(inbound.channel)
        await channel.send(inbound.chat_id, text)
        await self.ctx.budget.record_action(1)
        return True

    async def handle(self, inbound: InboundMessage, trace_id: str) -> None:
        with trace_context(trace_id):
            # Idempotency: a redelivered message must not create a second episode.
            async with session_scope(self.ctx.dsn) as session:
                existing = await get_episode_by_trace(session, trace_id)
                if existing is not None:
                    log.info("episode_skip_duplicate", trace_id=trace_id)
                    return
                episode = await create_episode(
                    session, trace_id=trace_id, source=inbound.channel
                )
                episode_id = episode.id
            set_episode_id(str(episode_id))
            await self._audit(EventType.EPISODE_STARTED, trace_id, "episode started")

            context_block = await self._retrieve_memory(episode_id, inbound.text)

            plan(inbound.text)  # kept for structure/audit; LLM-driven planning is inline.
            started = time.perf_counter()
            try:
                outcome = await run_reply(
                    self.ctx,
                    inbound.text,
                    trace_id,
                    context_block=context_block,
                    playbook_block=self._playbook_context(),
                )
            except BudgetExceeded as exc:
                await self._finish_failed(inbound, trace_id, reason=str(exc))
                return
            except Exception as exc:  # noqa: BLE001 — one bad message must not wedge loop
                log.error("reply_generation_failed", trace_id=trace_id, error=str(exc))
                await self._finish_failed(inbound, trace_id, reason=str(exc), llm_error=True)
                return

            duration_ms = int((time.perf_counter() - started) * 1000)
            await self._send(inbound, outcome.text)
            await self._persist_success(trace_id, inbound, outcome, duration_ms)

    async def _persist_success(
        self,
        trace_id: str,
        inbound: InboundMessage,
        outcome: ReplyOutcome,
        duration_ms: int,
    ) -> None:
        async with session_scope(self.ctx.dsn) as session:
            episode = await get_episode_by_trace(session, trace_id)
            if episode is not None:
                for rec in outcome.tools:
                    await add_step(
                        session,
                        episode,
                        kind="tool_call",
                        input={"tool": rec.name, "input": rec.input},
                        output={
                            "ok": rec.ok,
                            "output": rec.output,
                            "error": rec.error,
                            "tool_version": rec.tool_version,
                            "sandboxed": rec.sandboxed,
                            "from_probation": rec.from_probation,
                        },
                        ok=rec.ok,
                    )
                await add_step(
                    session,
                    episode,
                    kind="reply",
                    input={"text": inbound.text},
                    output={
                        "reply": outcome.text,
                        "tok_in": outcome.tok_in,
                        "tok_out": outcome.tok_out,
                        "cost_usd": outcome.cost_usd,
                        "llm_calls": outcome.llm_calls,
                        "tools_used": [r.name for r in outcome.tools],
                        "used_probation": outcome.used_probation,
                    },
                    duration_ms=duration_ms,
                    ok=True,
                )
            # A model call to a non-existent tool is a capability gap → record a
            # tool need for the night shift to forge (APPROVE-gated).
            for rec in outcome.tools:
                if rec.unknown_tool:
                    await record_tool_need(
                        session,
                        name=rec.name,
                        description=(
                            f"The agent called tool '{rec.name}' which does not exist. "
                            f"Design a tool that fulfils this call."
                        ),
                        args=rec.input,
                        trace_id=trace_id,
                    )
            await end_episode(
                session, trace_id, status=EPISODE_DONE, summary=outcome.text[:200]
            )
        for rec in outcome.tools:
            await self._audit(
                EventType.TOOL_CALL,
                trace_id,
                f"{rec.name} {'ok' if rec.ok else 'failed'}",
                tool=rec.name,
                ok=rec.ok,
                from_probation=rec.from_probation,
            )
        await self._audit(
            EventType.STEP_FINISHED,
            trace_id,
            "reply sent",
            tok_in=outcome.tok_in,
            tok_out=outcome.tok_out,
            cost_usd=outcome.cost_usd,
        )
        await self._audit(EventType.EPISODE_ENDED, trace_id, "episode done")

    async def _finish_failed(
        self,
        inbound: InboundMessage,
        trace_id: str,
        *,
        reason: str,
        llm_error: bool = False,
    ) -> None:
        message = _ERROR_MESSAGE if llm_error else _BUDGET_MESSAGE
        await self._send(inbound, message)
        async with session_scope(self.ctx.dsn) as session:
            episode = await get_episode_by_trace(session, trace_id)
            if episode is not None:
                await add_step(
                    session,
                    episode,
                    kind="reply",
                    input={"text": inbound.text},
                    output={"error": reason},
                    ok=False,
                )
            await end_episode(session, trace_id, status=EPISODE_FAILED, summary=reason[:200])
        await self._audit(EventType.ERROR, trace_id, "episode failed", reason=reason)
        await self._audit(EventType.EPISODE_ENDED, trace_id, "episode failed")
