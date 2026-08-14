"""Memory store lifecycle on sqlite: quarantine → active → retired → archived.

Enforces Prinsip 1 (never deleted) and Prinsip 2 (external quarantined first).
Vector similarity search itself needs pgvector (Postgres) and is covered by an
integration test that runs on the real host.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base
from agent.memory import archive, quarantine, semantic
from agent.memory.models import (
    MSTATUS_ACTIVE,
    MSTATUS_ARCHIVED,
    MSTATUS_QUARANTINE,
    MSTATUS_RETIRED,
    SRC_EXTERNAL,
    SRC_USER,
)
from agent.memory.retrieval import cosine_similarity, mark_retrieved
from agent.memory.semantic import MemoryError


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_external_content_forced_into_quarantine(session: AsyncSession) -> None:
    item = await quarantine.stage_external(
        session, content="from the web", source="http://x", embedding=[0.1, 0.2]
    )
    assert item.status == MSTATUS_QUARANTINE
    assert item.source_kind == SRC_EXTERNAL

    # add_semantic with external kind also forces quarantine.
    item2 = await semantic.add_semantic(
        session, content="c", embedding=None, source="s", source_kind=SRC_EXTERNAL
    )
    assert item2.status == MSTATUS_QUARANTINE


async def test_user_content_goes_active(session: AsyncSession) -> None:
    item = await semantic.add_semantic(
        session, content="user fact", embedding=[0.1], source="chat", source_kind=SRC_USER
    )
    assert item.status == MSTATUS_ACTIVE


async def test_full_lifecycle_never_deletes(session: AsyncSession) -> None:
    item = await quarantine.stage_external(session, content="c", source="s")
    await session.commit()

    promoted = await quarantine.promote(session, item.id)
    assert promoted is not None and promoted.status == MSTATUS_ACTIVE
    assert promoted.fitness == 1.0

    demoted = await semantic.demote(session, item.id)
    assert demoted is not None and demoted.status == MSTATUS_RETIRED

    # Retired → archived (curator path).
    archived = await archive.to_archived(session, item.id)
    assert archived is not None and archived.status == MSTATUS_ARCHIVED

    # Resample brings it back to active with neutral fitness + timestamp.
    resampled = await archive.resample(session, item.id)
    assert resampled is not None and resampled.status == MSTATUS_ACTIVE
    assert resampled.resampled_at is not None


async def test_demote_restore_guards(session: AsyncSession) -> None:
    item = await semantic.add_semantic(
        session, content="x", embedding=None, source="s", source_kind=SRC_USER
    )
    # Can't restore an active item.
    with pytest.raises(MemoryError):
        await semantic.restore(session, item.id)
    await semantic.demote(session, item.id)
    # Can't demote a retired item.
    with pytest.raises(MemoryError):
        await semantic.demote(session, item.id)
    restored = await semantic.restore(session, item.id)
    assert restored is not None and restored.status == MSTATUS_ACTIVE


async def test_mark_retrieved_bumps_counters(session: AsyncSession) -> None:
    item = await semantic.add_semantic(
        session, content="x", embedding=[0.1], source="s", source_kind=SRC_USER
    )
    assert item.retrieval_count == 0
    await mark_retrieved(session, [item])
    assert item.retrieval_count == 1
    assert item.last_used_at is not None


async def test_stats_counts_by_status(session: AsyncSession) -> None:
    await semantic.add_semantic(
        session, content="a", embedding=None, source="s", source_kind=SRC_USER
    )
    await quarantine.stage_external(session, content="b", source="s")
    st = await semantic.stats(session)
    assert st.total == 2
    assert st.by_status[MSTATUS_ACTIVE] == 1
    assert st.by_status[MSTATUS_QUARANTINE] == 1


def test_cosine_similarity() -> None:
    assert cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine_similarity([], [1]) == 0.0
