"""Drift-pause state (spec §7/§10).

When the regression suite drops enough, the system enters drift-pause: tier
NOTIFY and APPROVE actions are held (chat keeps running). The flag lives in
Redis so every process (daemon, CLI) sees the same state.

M6 sets/reads the flag. Loop enforcement + `agent drift report` land in M10.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.base import utcnow
from agent.db.repo import list_change_events_since, list_regression_runs

_KEY = "agent:drift"

# Suspect ranking when a regression drops (spec §7): playbook edits are the most
# likely cause, then new skills, then new tools, then memory promotions.
_SUSPECT_ORDER = {"playbook": 0, "skill": 1, "tool": 2, "memory": 3}


@dataclass(slots=True)
class DriftStatus:
    paused: bool
    reason: str
    since: str | None


class DriftState:
    def __init__(self, redis: Redis[str]) -> None:
        self.redis = redis

    async def is_paused(self) -> bool:
        return (await self.redis.hget(_KEY, "paused")) == "1"

    async def set_paused(self, reason: str) -> None:
        await self.redis.hset(
            _KEY,
            mapping={"paused": "1", "reason": reason, "since": utcnow().isoformat()},
        )

    async def clear(self) -> None:
        await self.redis.delete(_KEY)

    async def status(self) -> DriftStatus:
        data = await self.redis.hgetall(_KEY)
        return DriftStatus(
            paused=data.get("paused") == "1",
            reason=data.get("reason", ""),
            since=data.get("since"),
        )


@dataclass(slots=True)
class Suspect:
    kind: str
    ref_id: str | None
    at: str


@dataclass(slots=True)
class DriftReport:
    have_comparison: bool
    latest_score: str = ""
    prior_score: str = ""
    dropped: int = 0
    newly_failing: list[str] = field(default_factory=list)
    suspects: list[Suspect] = field(default_factory=list)
    note: str = ""


async def drift_report(session: AsyncSession, *, suite: str = "regression") -> DriftReport:
    """Correlate the latest regression drop with change_events since the prior
    run, ranking suspects by likelihood (spec §7)."""
    runs = await list_regression_runs(session, suite=suite, limit=2)
    if len(runs) < 2:
        return DriftReport(have_comparison=False, note="need at least two runs")

    latest, prior = runs[0], runs[1]
    detail = latest.detail or {}
    dropped = int(detail.get("dropped", 0))
    newly = list(detail.get("newly_failing", []))

    events = await list_change_events_since(session, prior.at)
    ranked = sorted(events, key=lambda e: _SUSPECT_ORDER.get(e.kind, 9))
    suspects = [
        Suspect(kind=e.kind, ref_id=e.ref_id, at=e.at.isoformat() if e.at else "")
        for e in ranked
    ]

    note = "no drop since prior run" if dropped == 0 else "drop detected"
    return DriftReport(
        have_comparison=True,
        latest_score=f"{latest.passed}/{latest.total}",
        prior_score=f"{prior.passed}/{prior.total}",
        dropped=dropped,
        newly_failing=newly,
        suspects=suspects,
        note=note,
    )
