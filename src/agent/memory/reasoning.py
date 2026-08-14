"""ReasoningBank — distill strategies from trajectories, success AND failure.

Failures are as informative as successes: a failed episode yields an "avoid"
strategy. Distilled reasoning is self-sourced (not external), so it enters as
active reasoning-tier memory. Distillation is idempotent per trace.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.models import EPISODE_DONE, Episode
from agent.logging import get_logger
from agent.memory.models import (
    MSTATUS_ACTIVE,
    SRC_SELF,
    TIER_REASONING,
    MemoryItem,
)
from agent.memory.retrieval import Embedder

log = get_logger("reasoning")

Distiller = Callable[[str], Awaitable[str]]

_SUCCESS_PROMPT = (
    "This episode SUCCEEDED. Extract ONE concise, reusable strategy (2-3 "
    "sentences) that made it work.\n\nSummary: {summary}\n\nTrajectory:\n{trajectory}"
)
_FAILURE_PROMPT = (
    "This episode FAILED. Extract ONE concise lesson (2-3 sentences) about what "
    "to AVOID next time.\n\nSummary: {summary}\n\nTrajectory:\n{trajectory}"
)


def build_trajectory(episode: Episode) -> str:
    lines: list[str] = []
    for step in episode.steps:
        mark = "ok" if step.ok else ("fail" if step.ok is False else "-")
        lines.append(f"[{step.idx}] {step.kind} ({mark}) in={step.input} out={step.output}")
    return "\n".join(lines) if lines else "(no steps)"


async def _existing(session: AsyncSession, trace_id: str | None) -> MemoryItem | None:
    if trace_id is None:
        return None
    stmt = select(MemoryItem).where(
        MemoryItem.trace_id == trace_id, MemoryItem.tier == TIER_REASONING
    )
    item: MemoryItem | None = await session.scalar(stmt)
    return item


async def distill_reasoning(
    session: AsyncSession,
    episode: Episode,
    distiller: Distiller,
    *,
    embedder: Embedder | None = None,
) -> MemoryItem:
    """Distill one strategy (or anti-pattern) from an episode. Idempotent per trace."""
    existing = await _existing(session, episode.trace_id)
    if existing is not None:
        return existing

    succeeded = episode.status == EPISODE_DONE
    template = _SUCCESS_PROMPT if succeeded else _FAILURE_PROMPT
    prompt = template.format(
        summary=episode.summary or "(none)", trajectory=build_trajectory(episode)
    )
    strategy = (await distiller(prompt)).strip()

    embedding: list[float] | None = None
    if embedder is not None:
        try:
            embedding = await embedder.embed(strategy)
        except Exception as exc:  # noqa: BLE001 — embedding is best-effort
            log.warning("reasoning_embed_failed", error=str(exc))

    tag = "STRATEGY" if succeeded else "AVOID"
    item = MemoryItem(
        tier=TIER_REASONING,
        content=f"[{tag}] {strategy}",
        embedding=embedding,
        source="reasoning-distill",
        source_kind=SRC_SELF,
        trace_id=episode.trace_id,
        status=MSTATUS_ACTIVE,
        fitness=0.0,
    )
    session.add(item)
    await session.flush()
    log.info("reasoning_distilled", trace_id=episode.trace_id, tag=tag)
    return item
