"""Archive — permanent, non-destructive storage + (M10) resample.

The archive is why there is no DELETE on any automatic path: a low-fitness
artefact today may be a stepping stone tomorrow (Darwin Gödel Machine, spec §5).
The curator may move items to `archived`; only a human may delete permanently.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.base import utcnow
from agent.memory.models import (
    MSTATUS_ACTIVE,
    MSTATUS_ARCHIVED,
    MSTATUS_RETIRED,
    MemoryItem,
)


async def to_archived(session: AsyncSession, item_id: int) -> MemoryItem | None:
    """retired → archived (out of the hot table, still permanent)."""
    item = await session.get(MemoryItem, item_id)
    if item is None or item.status != MSTATUS_RETIRED:
        return None
    item.status = MSTATUS_ARCHIVED
    await session.flush()
    return item


async def list_archived(session: AsyncSession, *, limit: int = 100) -> list[MemoryItem]:
    stmt = (
        select(MemoryItem)
        .where(MemoryItem.status == MSTATUS_ARCHIVED)
        .order_by(MemoryItem.created_at.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def resample(session: AsyncSession, item_id: int) -> MemoryItem | None:
    """archived → active with neutral fitness, marked resampled (curator, M10)."""
    item = await session.get(MemoryItem, item_id)
    if item is None or item.status != MSTATUS_ARCHIVED:
        return None
    item.status = MSTATUS_ACTIVE
    item.fitness = 1.0
    item.resampled_at = utcnow()
    await session.flush()
    return item


async def resample_archived(session: AsyncSession, *, limit: int = 5) -> list[MemoryItem]:
    """Pull a few archived items back to active to keep exploration alive
    (stepping stones, spec §5). Most-recently-archived first."""
    archived = await list_archived(session, limit=limit)
    revived: list[MemoryItem] = []
    for item in archived:
        item.status = MSTATUS_ACTIVE
        item.fitness = 1.0
        item.resampled_at = utcnow()
        revived.append(item)
    await session.flush()
    return revived
