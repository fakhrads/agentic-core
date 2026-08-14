"""Fitness scoring + lifecycle predicates (spec §5).

    fitness = (success_count*2 + retrieval_count*0.5 + human_reward*3
               - contradiction_count*3) * decay(last_used_at)
    decay   = 0.5 ** (days_since_used / 30)

Pure functions — the curator (M7/M10) applies them; here they are just math so
they can be unit-tested without a database.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agent.db.base import utcnow
from agent.memory.models import (
    MSTATUS_ACTIVE,
    MSTATUS_RETIRED,
    MemoryItem,
)

RETIRE_FITNESS = 0.5
RETIRE_AGE_DAYS = 14
ARCHIVE_AGE_DAYS = 90


def as_aware(dt: datetime) -> datetime:
    """Coerce naive datetimes (e.g. sqlite reads) to UTC — DBs vary."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def decay(last_used_at: datetime | None, now: datetime | None = None) -> float:
    now = now or utcnow()
    if last_used_at is None:
        # Never used → treat age from now (no penalty yet).
        return 1.0
    days = (now - as_aware(last_used_at)).total_seconds() / 86_400
    return float(0.5 ** (days / 30))


def raw_score(
    success_count: int,
    retrieval_count: int,
    human_reward: float,
    contradiction_count: int,
) -> float:
    return (
        success_count * 2
        + retrieval_count * 0.5
        + human_reward * 3
        - contradiction_count * 3
    )


def compute_fitness(item: MemoryItem, now: datetime | None = None) -> float:
    base = raw_score(
        item.success_count,
        item.retrieval_count,
        item.human_reward,
        item.contradiction_count,
    )
    return base * decay(item.last_used_at, now)


def _age_days(created_at: datetime, now: datetime) -> float:
    return (now - as_aware(created_at)).total_seconds() / 86_400


def should_retire(item: MemoryItem, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (
        item.status == MSTATUS_ACTIVE
        and compute_fitness(item, now) < RETIRE_FITNESS
        and _age_days(item.created_at, now) > RETIRE_AGE_DAYS
    )


def should_archive(item: MemoryItem, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if item.status != MSTATUS_RETIRED:
        return False
    # Age since it went cold; approximated by last_used_at or created_at.
    reference = item.last_used_at or item.created_at
    return _age_days(reference, now) > ARCHIVE_AGE_DAYS
