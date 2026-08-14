"""Curator sweep on sqlite: fitness recompute + retire + archive, never delete."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base, utcnow
from agent.jobs.curator import sweep
from agent.memory.models import (
    MSTATUS_ACTIVE,
    MSTATUS_ARCHIVED,
    MSTATUS_RETIRED,
    MemoryItem,
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_sweep_retires_weak_aged_active(session: AsyncSession) -> None:
    now = utcnow()
    weak = MemoryItem(
        tier="semantic", content="weak", source="s", source_kind="self",
        status=MSTATUS_ACTIVE, success_count=0, retrieval_count=0,
        created_at=now - timedelta(days=30), last_used_at=now - timedelta(days=30),
    )
    strong = MemoryItem(
        tier="semantic", content="strong", source="s", source_kind="self",
        status=MSTATUS_ACTIVE, success_count=10, retrieval_count=5,
        created_at=now - timedelta(days=30), last_used_at=now,
    )
    session.add_all([weak, strong])
    await session.commit()

    report = await sweep(session, now=now)
    assert report.retired == 1
    await session.refresh(weak)
    await session.refresh(strong)
    assert weak.status == MSTATUS_RETIRED
    assert strong.status == MSTATUS_ACTIVE
    assert strong.fitness > 0  # fitness recomputed


async def test_sweep_archives_old_retired(session: AsyncSession) -> None:
    now = utcnow()
    old_retired = MemoryItem(
        tier="semantic", content="x", source="s", source_kind="self",
        status=MSTATUS_RETIRED, created_at=now - timedelta(days=200),
        last_used_at=now - timedelta(days=200),
    )
    session.add(old_retired)
    await session.commit()

    report = await sweep(session, now=now)
    assert report.archived == 1
    await session.refresh(old_retired)
    assert old_retired.status == MSTATUS_ARCHIVED


async def test_sweep_never_deletes(session: AsyncSession) -> None:
    now = utcnow()
    for i in range(3):
        session.add(
            MemoryItem(
                tier="semantic", content=f"m{i}", source="s", source_kind="self",
                status=MSTATUS_ACTIVE, created_at=now - timedelta(days=30),
                last_used_at=now - timedelta(days=30),
            )
        )
    await session.commit()

    await sweep(session, now=now)
    total = await session.scalar(select(func.count(MemoryItem.id)))
    assert total == 3  # all still present, just re-statused
