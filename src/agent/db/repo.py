"""Repository functions for episode/step.

Thin, explicit query helpers — no ORM magic leaking into callers. Everything
takes an AsyncSession so the caller owns the transaction boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent.db.base import utcnow
from agent.db.models import (
    EPISODE_FAILED,
    EPISODE_RUNNING,
    ChangeEvent,
    Episode,
    EpisodeArtefact,
    LLMCall,
    RegressionRun,
    Step,
)


async def create_episode(
    session: AsyncSession,
    *,
    trace_id: str,
    source: str,
    status: str = EPISODE_RUNNING,
) -> Episode:
    ep = Episode(trace_id=trace_id, source=source, status=status)
    session.add(ep)
    await session.flush()
    return ep


async def end_episode(
    session: AsyncSession,
    trace_id: str,
    *,
    status: str,
    summary: str | None = None,
) -> Episode | None:
    ep = await get_episode_by_trace(session, trace_id)
    if ep is None:
        return None
    ep.status = status
    ep.ended_at = utcnow()
    if summary is not None:
        ep.summary = summary
    await session.flush()
    return ep


async def add_step(
    session: AsyncSession,
    episode: Episode,
    *,
    kind: str,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    ok: bool | None = None,
) -> Step:
    # Next idx = current step count for this episode.
    count = await session.scalar(
        select(func.count(Step.id)).where(Step.episode_id == episode.id)
    )
    step = Step(
        episode_id=episode.id,
        idx=int(count or 0),
        kind=kind,
        input=input or {},
        output=output,
        duration_ms=duration_ms,
        ok=ok,
    )
    session.add(step)
    await session.flush()
    return step


async def get_episode_by_trace(
    session: AsyncSession, trace_id: str
) -> Episode | None:
    stmt = (
        select(Episode)
        .where(Episode.trace_id == trace_id)
        .options(selectinload(Episode.steps))
    )
    ep: Episode | None = await session.scalar(stmt)
    return ep


async def list_episodes(
    session: AsyncSession,
    *,
    today: bool = False,
    failed: bool = False,
    limit: int = 50,
) -> list[Episode]:
    stmt = select(Episode).order_by(Episode.started_at.desc()).limit(limit)
    if today:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        stmt = stmt.where(Episode.started_at >= midnight)
    if failed:
        stmt = stmt.where(Episode.status == EPISODE_FAILED)
    result = await session.scalars(stmt)
    return list(result.all())


async def record_llm_call(
    session: AsyncSession,
    *,
    trace_id: str | None,
    provider: str,
    model: str,
    tok_in: int,
    tok_out: int,
    cost_usd: float,
    ok: bool,
    error: str | None = None,
) -> LLMCall:
    call = LLMCall(
        trace_id=trace_id,
        provider=provider,
        model=model,
        tok_in=tok_in,
        tok_out=tok_out,
        cost_usd=cost_usd,
        ok=ok,
        error=error,
    )
    session.add(call)
    await session.flush()
    return call


async def record_regression_run(
    session: AsyncSession,
    *,
    suite: str,
    passed: int,
    total: int,
    detail: dict[str, Any],
) -> RegressionRun:
    run = RegressionRun(suite=suite, passed=passed, total=total, detail=detail)
    session.add(run)
    await session.flush()
    return run


async def list_regression_runs(
    session: AsyncSession, *, suite: str, limit: int = 20
) -> list[RegressionRun]:
    stmt = (
        select(RegressionRun)
        .where(RegressionRun.suite == suite)
        .order_by(RegressionRun.at.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def record_change_event(
    session: AsyncSession, *, kind: str, ref_id: str | None = None
) -> ChangeEvent:
    event = ChangeEvent(kind=kind, ref_id=ref_id)
    session.add(event)
    await session.flush()
    return event


async def list_change_events_since(
    session: AsyncSession, since: datetime, *, limit: int = 100
) -> list[ChangeEvent]:
    stmt = (
        select(ChangeEvent)
        .where(ChangeEvent.at >= since)
        .order_by(ChangeEvent.at.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def record_artefact_use(
    session: AsyncSession, *, episode_id: int, kind: str, ref_id: int
) -> EpisodeArtefact | None:
    """Record an artefact use, de-duplicated per (episode, kind, ref)."""
    existing = await session.scalar(
        select(EpisodeArtefact).where(
            EpisodeArtefact.episode_id == episode_id,
            EpisodeArtefact.kind == kind,
            EpisodeArtefact.ref_id == ref_id,
        )
    )
    if existing is not None:
        return existing
    art = EpisodeArtefact(episode_id=episode_id, kind=kind, ref_id=ref_id)
    session.add(art)
    await session.flush()
    return art


async def list_episode_artefacts(
    session: AsyncSession, episode_id: int
) -> list[EpisodeArtefact]:
    stmt = select(EpisodeArtefact).where(EpisodeArtefact.episode_id == episode_id)
    result = await session.scalars(stmt)
    return list(result.all())


async def artefact_usage_counts(session: AsyncSession) -> dict[tuple[str, int], int]:
    """Global usage count per (kind, ref_id) across all episodes."""
    rows = await session.execute(
        select(
            EpisodeArtefact.kind,
            EpisodeArtefact.ref_id,
            func.count(EpisodeArtefact.id),
        ).group_by(EpisodeArtefact.kind, EpisodeArtefact.ref_id)
    )
    return {(str(kind), int(ref)): int(count) for kind, ref, count in rows.all()}
