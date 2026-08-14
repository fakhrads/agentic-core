"""Dashboard snapshot — aggregate live state for `agent watch`.

Pure data gathering (no rendering, no print) so it can be unit-tested. Every
section is defensive: a failing source yields a null/empty value rather than
crashing the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import func, select

from agent.autonomy.budget import BudgetManager, BudgetSnapshot
from agent.bus.events import STREAM_AUDIT, STREAM_INBOUND, Event
from agent.bus.streams import EventBus
from agent.db.base import session_scope
from agent.db.models import (
    APPROVAL_PENDING as _PENDING,
)
from agent.db.models import (
    EPISODE_RUNNING,
    Approval,
    Episode,
    Step,
)
from agent.heartbeat import is_alive, started_at
from agent.memory.models import MSTATUS_ARCHIVED, MemoryItem
from agent.tools.forge import ACTION_TOOL_REGISTER


@dataclass(slots=True)
class ActiveEpisode:
    trace_id: str
    summary: str
    last_step: str


@dataclass(slots=True)
class RegressionHealth:
    passed: int
    total: int
    prev_passed: int | None
    prev_total: int | None
    dropped: int


@dataclass(slots=True)
class ApprovalRow:
    id: int
    summary: str


@dataclass(slots=True)
class Snapshot:
    running: bool = False
    uptime_s: float | None = None
    queue_len: int = 0
    budget: BudgetSnapshot | None = None
    active_episode: ActiveEpisode | None = None
    regression: RegressionHealth | None = None
    archive_count: int = 0
    resampled_today: int = 0
    events: list[Event] = field(default_factory=list)
    approvals: list[ApprovalRow] = field(default_factory=list)


def _approval_summary(a: Approval) -> str:
    if a.action_kind == ACTION_TOOL_REGISTER:
        sub = a.payload.get("submission", {}) if a.payload else {}
        return f"register tool \"{sub.get('name', '?')}\""
    return a.action_kind


async def gather_snapshot(
    dsn: str, redis: Redis[str], bus: EventBus, budget: BudgetManager
) -> Snapshot:
    snap = Snapshot()

    snap.running = await is_alive(redis)
    started = await started_at(redis)
    if started is not None:
        snap.uptime_s = (datetime.now(UTC) - started).total_seconds()
    snap.queue_len = await bus.xlen(STREAM_INBOUND)
    snap.budget = await budget.snapshot()
    snap.events = [m.event for m in await bus.history(STREAM_AUDIT, count=10)]

    async with session_scope(dsn) as session:
        # Active episode (most recent still running).
        ep = await session.scalar(
            select(Episode)
            .where(Episode.status == EPISODE_RUNNING)
            .order_by(Episode.started_at.desc())
            .limit(1)
        )
        if ep is not None:
            last = await session.scalar(
                select(Step)
                .where(Step.episode_id == ep.id)
                .order_by(Step.idx.desc())
                .limit(1)
            )
            snap.active_episode = ActiveEpisode(
                trace_id=ep.trace_id,
                summary=ep.summary or f"source={ep.source}",
                last_step=(f"{last.kind} (#{last.idx})" if last is not None else "starting"),
            )

        # Regression health (latest + prior).
        from agent.db.repo import list_regression_runs

        runs = await list_regression_runs(session, suite="regression", limit=2)
        if runs:
            latest = runs[0]
            prior = runs[1] if len(runs) > 1 else None
            snap.regression = RegressionHealth(
                passed=latest.passed,
                total=latest.total,
                prev_passed=prior.passed if prior else None,
                prev_total=prior.total if prior else None,
                dropped=int((latest.detail or {}).get("dropped", 0)),
            )

        # Archive size + resamples today.
        snap.archive_count = int(
            await session.scalar(
                select(func.count(MemoryItem.id)).where(
                    MemoryItem.status == MSTATUS_ARCHIVED
                )
            )
            or 0
        )
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        snap.resampled_today = int(
            await session.scalar(
                select(func.count(MemoryItem.id)).where(
                    MemoryItem.resampled_at.is_not(None),
                    MemoryItem.resampled_at >= midnight,
                )
            )
            or 0
        )

        # Pending approvals.
        pend = await session.scalars(
            select(Approval).where(Approval.status == _PENDING).order_by(Approval.id)
        )
        snap.approvals = [ApprovalRow(id=a.id, summary=_approval_summary(a)) for a in pend.all()]

    return snap
