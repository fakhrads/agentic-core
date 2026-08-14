"""drift_report suspect ranking + resample/return lifecycle (sqlite)."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base, utcnow
from agent.db.repo import record_change_event, record_regression_run
from agent.evolution.drift import drift_report
from agent.jobs.curator import sweep
from agent.memory.archive import resample_archived
from agent.memory.models import MSTATUS_ACTIVE, MSTATUS_ARCHIVED, SRC_SELF
from agent.memory.semantic import add_semantic


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_drift_report_ranks_suspects(session: AsyncSession) -> None:
    # Prior run (baseline), then some changes, then a dropped run.
    await record_regression_run(
        session, suite="regression", passed=20, total=20,
        detail={"results": {"a": True}, "dropped": 0},
    )
    # Changes since prior run: a tool and a playbook edit.
    await record_change_event(session, kind="tool", ref_id="csv_diff")
    await record_change_event(session, kind="playbook", ref_id="rev-9")
    await record_regression_run(
        session, suite="regression", passed=18, total=20,
        detail={"results": {"a": False}, "dropped": 2, "newly_failing": ["a", "b"]},
    )
    await session.commit()

    rep = await drift_report(session)
    assert rep.have_comparison is True
    assert rep.dropped == 2
    # Playbook ranks ahead of tool.
    assert [s.kind for s in rep.suspects][:2] == ["playbook", "tool"]


async def test_resample_and_return_to_archive(session: AsyncSession) -> None:
    item = await add_semantic(
        session, content="stepping stone", embedding=None, source="s", source_kind=SRC_SELF
    )
    item.status = MSTATUS_ARCHIVED
    await session.commit()

    revived = await resample_archived(session, limit=5)
    assert len(revived) == 1 and revived[0].status == MSTATUS_ACTIVE
    assert revived[0].resampled_at is not None

    # Simulate 15 days passing with no use → curator returns it to the archive.
    item.resampled_at = utcnow() - timedelta(days=15)
    report = await sweep(session)
    assert report.resample_returned == 1
    assert item.status == MSTATUS_ARCHIVED
    assert item.resampled_at is None
