"""Semantic memory store — create/read/status operations.

Prinsip 2 is enforced here: external content can only enter as `quarantine`.
Prinsip 1 is enforced by never deleting — demote/retire/archive only change
status. Deletion is a human-only CLI action, never on an automatic path.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory.fitness import compute_fitness
from agent.memory.models import (
    MSTATUS_ACTIVE,
    MSTATUS_ARCHIVED,
    MSTATUS_QUARANTINE,
    MSTATUS_RETIRED,
    SRC_EXTERNAL,
    TIER_SEMANTIC,
    MemoryItem,
)


class MemoryError(Exception):
    pass


async def add_semantic(
    session: AsyncSession,
    *,
    content: str,
    embedding: list[float] | None,
    source: str,
    source_kind: str,
    trace_id: str | None = None,
    tier: str = TIER_SEMANTIC,
) -> MemoryItem:
    """Create a memory item. External source is forced into quarantine."""
    status = MSTATUS_QUARANTINE if source_kind == SRC_EXTERNAL else MSTATUS_ACTIVE
    item = MemoryItem(
        tier=tier,
        content=content,
        embedding=embedding,
        source=source,
        source_kind=source_kind,
        trace_id=trace_id,
        status=status,
    )
    session.add(item)
    await session.flush()
    return item


async def get(session: AsyncSession, item_id: int) -> MemoryItem | None:
    return await session.get(MemoryItem, item_id)


async def set_status(
    session: AsyncSession, item_id: int, status: str
) -> MemoryItem | None:
    item = await session.get(MemoryItem, item_id)
    if item is None:
        return None
    item.status = status
    await session.flush()
    return item


async def demote(session: AsyncSession, item_id: int) -> MemoryItem | None:
    """active → retired (NOT delete). Retired items are not retrieved."""
    item = await session.get(MemoryItem, item_id)
    if item is None:
        return None
    if item.status != MSTATUS_ACTIVE:
        raise MemoryError(f"can only demote active items (is {item.status})")
    item.status = MSTATUS_RETIRED
    await session.flush()
    return item


async def restore(session: AsyncSession, item_id: int) -> MemoryItem | None:
    """retired → active."""
    item = await session.get(MemoryItem, item_id)
    if item is None:
        return None
    if item.status != MSTATUS_RETIRED:
        raise MemoryError(f"can only restore retired items (is {item.status})")
    item.status = MSTATUS_ACTIVE
    await session.flush()
    return item


async def list_by_status(
    session: AsyncSession, status: str, *, limit: int = 100
) -> list[MemoryItem]:
    stmt = (
        select(MemoryItem)
        .where(MemoryItem.status == status)
        .order_by(MemoryItem.created_at.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


@dataclass(slots=True)
class MemoryStats:
    total: int
    by_status: dict[str, int]
    by_tier: dict[str, int]
    avg_fitness_active: float


async def stats(session: AsyncSession) -> MemoryStats:
    total = int(await session.scalar(select(func.count(MemoryItem.id))) or 0)

    by_status: dict[str, int] = {}
    rows = await session.execute(
        select(MemoryItem.status, func.count(MemoryItem.id)).group_by(MemoryItem.status)
    )
    for status_val, count in rows.all():
        by_status[str(status_val)] = int(count)

    by_tier: dict[str, int] = {}
    rows2 = await session.execute(
        select(MemoryItem.tier, func.count(MemoryItem.id)).group_by(MemoryItem.tier)
    )
    for tier_val, count in rows2.all():
        by_tier[str(tier_val)] = int(count)

    actives = await list_by_status(session, MSTATUS_ACTIVE, limit=1000)
    avg_fit = (
        sum(compute_fitness(i) for i in actives) / len(actives) if actives else 0.0
    )
    return MemoryStats(
        total=total,
        by_status=by_status,
        by_tier=by_tier,
        avg_fitness_active=round(avg_fit, 3),
    )


# Ensure the archived status constant is importable from one place.
ALL_STATUSES = (
    MSTATUS_QUARANTINE,
    MSTATUS_ACTIVE,
    MSTATUS_RETIRED,
    MSTATUS_ARCHIVED,
)
