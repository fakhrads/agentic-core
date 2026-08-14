"""Quarantine — staging for external content (Prinsip 2).

External content is never written straight to long-term memory. It lands in
`quarantine` and only becomes `active` after distillation + verification (M9);
here we provide the staging and the promotion primitive.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent.memory.models import (
    MSTATUS_ACTIVE,
    MSTATUS_QUARANTINE,
    SRC_EXTERNAL,
    MemoryItem,
)
from agent.memory.semantic import add_semantic, list_by_status


async def stage_external(
    session: AsyncSession,
    *,
    content: str,
    source: str,
    embedding: list[float] | None = None,
    trace_id: str | None = None,
) -> MemoryItem:
    """Stage external content into quarantine."""
    return await add_semantic(
        session,
        content=content,
        embedding=embedding,
        source=source,
        source_kind=SRC_EXTERNAL,
        trace_id=trace_id,
    )


async def list_quarantine(session: AsyncSession, *, limit: int = 100) -> list[MemoryItem]:
    return await list_by_status(session, MSTATUS_QUARANTINE, limit=limit)


async def promote(
    session: AsyncSession, item_id: int, *, neutral_fitness: float = 1.0
) -> MemoryItem | None:
    """quarantine → active (after distillation/verification). Neutral fitness."""
    item = await session.get(MemoryItem, item_id)
    if item is None or item.status != MSTATUS_QUARANTINE:
        return None
    item.status = MSTATUS_ACTIVE
    item.fitness = neutral_fitness
    await session.flush()
    return item
