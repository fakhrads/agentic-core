"""Episodic memory — thin views over the episode/step trajectory store.

Episodes ARE the episodic memory (they live in the `episode`/`step` tables from
M2). This module provides read helpers used when building loop context.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.models import Episode
from agent.db.repo import list_episodes


async def recent_episodes(session: AsyncSession, *, limit: int = 10) -> list[Episode]:
    return await list_episodes(session, limit=limit)
