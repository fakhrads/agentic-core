"""Tool-need registry — capability gaps surfaced by trajectories.

When the model calls a tool that doesn't exist, that's a signal it wanted a
capability. We record it (upsert by proposed name), and the night shift forges
frequently-requested needs into APPROVE-gated registrations.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.base import utcnow
from agent.db.models import NEED_DISMISSED, NEED_FORGED, NEED_OPEN, ToolNeed
from agent.logging import get_logger

log = get_logger("tools.needs")


async def record_tool_need(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    args: dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> ToolNeed:
    """Upsert a need by proposed name. Re-seen open needs bump their count.

    A need already forged or dismissed is left untouched — we don't re-forge a
    rejected proposal or spam the approval queue.
    """
    existing = await session.scalar(select(ToolNeed).where(ToolNeed.name == name))
    if existing is not None:
        if existing.status == NEED_OPEN:
            existing.count += 1
            existing.last_seen_at = utcnow()
            await session.flush()
        return existing
    need = ToolNeed(
        name=name,
        description=description,
        args_sample=args or {},
        requested_by_trace=trace_id,
    )
    session.add(need)
    await session.flush()
    log.info("tool_need_recorded", name=name)
    return need


async def list_open_needs(
    session: AsyncSession, *, min_count: int = 1, limit: int = 5
) -> list[ToolNeed]:
    stmt = (
        select(ToolNeed)
        .where(ToolNeed.status == NEED_OPEN, ToolNeed.count >= min_count)
        .order_by(ToolNeed.count.desc(), ToolNeed.last_seen_at.desc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def list_needs(session: AsyncSession, *, limit: int = 50) -> list[ToolNeed]:
    stmt = select(ToolNeed).order_by(ToolNeed.count.desc()).limit(limit)
    result = await session.scalars(stmt)
    return list(result.all())


async def mark_forged(session: AsyncSession, need_id: int) -> ToolNeed | None:
    need = await session.get(ToolNeed, need_id)
    if need is None:
        return None
    need.status = NEED_FORGED
    await session.flush()
    return need


async def mark_dismissed(session: AsyncSession, need_id: int) -> ToolNeed | None:
    need = await session.get(ToolNeed, need_id)
    if need is None:
        return None
    need.status = NEED_DISMISSED
    await session.flush()
    return need
