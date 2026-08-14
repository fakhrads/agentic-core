"""Curator — periodic fitness sweep + lifecycle transitions.

Recomputes fitness for every hot (non-archived) memory item and applies the
automatic transitions (spec §5): active→retired, retired→archived. It NEVER
deletes and NEVER auto-promotes quarantine (Prinsip 1 & 2 — quarantine promotion
is gated on distillation/verification in M9).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.base import utcnow
from agent.logging import get_logger
from agent.memory.fitness import as_aware, compute_fitness, should_archive, should_retire
from agent.memory.models import (
    MSTATUS_ACTIVE,
    MSTATUS_ARCHIVED,
    MSTATUS_RETIRED,
    MemoryItem,
)

log = get_logger("curator")

# A resampled item unused within this many days goes back to the archive (spec §5).
RESAMPLE_RETURN_DAYS = 14


@dataclass(slots=True)
class CuratorReport:
    scanned: int = 0
    fitness_updated: int = 0
    retired: int = 0
    archived: int = 0
    resample_returned: int = 0


async def sweep(session: AsyncSession, *, now: datetime | None = None) -> CuratorReport:
    now = now or utcnow()
    report = CuratorReport()

    stmt = select(MemoryItem).where(MemoryItem.status != MSTATUS_ARCHIVED)
    items = (await session.scalars(stmt)).all()

    for item in items:
        report.scanned += 1
        new_fit = compute_fitness(item, now)
        if item.fitness != new_fit:
            item.fitness = new_fit
            report.fitness_updated += 1

        # A resampled item unused within the window returns to the archive.
        resampled = item.resampled_at
        unused_since_resample = item.last_used_at is None or (
            resampled is not None and as_aware(item.last_used_at) <= as_aware(resampled)
        )
        if (
            item.status == MSTATUS_ACTIVE
            and resampled is not None
            and (now - as_aware(resampled)).days > RESAMPLE_RETURN_DAYS
            and unused_since_resample
        ):
            item.status = MSTATUS_ARCHIVED
            item.resampled_at = None
            report.resample_returned += 1
        elif item.status == MSTATUS_ACTIVE and should_retire(item, now):
            item.status = MSTATUS_RETIRED
            report.retired += 1
        elif item.status == MSTATUS_RETIRED and should_archive(item, now):
            item.status = MSTATUS_ARCHIVED
            report.archived += 1

    await session.flush()
    log.info(
        "curator_sweep",
        scanned=report.scanned,
        retired=report.retired,
        archived=report.archived,
    )
    return report
