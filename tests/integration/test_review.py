"""Graded review → reward distribution + pending ranking (sqlite)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base
from agent.db.models import ARTEFACT_MEMORY, EPISODE_DONE
from agent.db.repo import create_episode, end_episode, record_artefact_use
from agent.evolution.review import (
    ReviewError,
    distribute_reward,
    pending_reviews,
    set_review,
)
from agent.memory.models import SRC_USER
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


async def _episode_with_artefacts(session: AsyncSession, trace: str, mem_ids: list[int]) -> int:
    ep = await create_episode(session, trace_id=trace, source="test")
    await end_episode(session, trace, status=EPISODE_DONE)
    for mid in mem_ids:
        await record_artefact_use(session, episode_id=ep.id, kind=ARTEFACT_MEMORY, ref_id=mid)
    return ep.id


async def test_positive_review_rewards_used_memory(session: AsyncSession) -> None:
    m1 = await add_semantic(session, content="a", embedding=None, source="s", source_kind=SRC_USER)
    m2 = await add_semantic(session, content="b", embedding=None, source="s", source_kind=SRC_USER)
    await _episode_with_artefacts(session, "t", [m1.id, m2.id])
    await session.commit()

    ep = await set_review(session, "t", score=5, note="great")
    assert ep is not None
    per = await distribute_reward(session, ep)
    # (5-3)*0.5 / 2 = 0.5 each
    assert per == pytest.approx(0.5)
    assert m1.human_reward == pytest.approx(0.5)
    assert m2.human_reward == pytest.approx(0.5)


async def test_neutral_score_changes_nothing(session: AsyncSession) -> None:
    m1 = await add_semantic(session, content="a", embedding=None, source="s", source_kind=SRC_USER)
    await _episode_with_artefacts(session, "t", [m1.id])
    await session.commit()
    ep = await set_review(session, "t", score=3)
    assert ep is not None
    per = await distribute_reward(session, ep)
    assert per == 0.0
    assert m1.human_reward == 0.0


async def test_negative_review_penalizes(session: AsyncSession) -> None:
    m1 = await add_semantic(session, content="a", embedding=None, source="s", source_kind=SRC_USER)
    await _episode_with_artefacts(session, "t", [m1.id])
    await session.commit()
    ep = await set_review(session, "t", score=1)
    assert ep is not None
    await distribute_reward(session, ep)
    assert m1.human_reward == pytest.approx(-1.0)  # (1-3)*0.5/1


async def test_invalid_score_raises(session: AsyncSession) -> None:
    await create_episode(session, trace_id="t", source="s")
    with pytest.raises(ReviewError):
        await set_review(session, "t", score=9)


async def test_pending_ranked_by_impact(session: AsyncSession) -> None:
    m1 = await add_semantic(session, content="a", embedding=None, source="s", source_kind=SRC_USER)
    m2 = await add_semantic(session, content="b", embedding=None, source="s", source_kind=SRC_USER)
    # m1 used by two episodes → higher global reuse.
    await _episode_with_artefacts(session, "high", [m1.id])
    await _episode_with_artefacts(session, "also", [m1.id])
    await _episode_with_artefacts(session, "low", [m2.id])
    await session.commit()

    pend = await pending_reviews(session, limit=5)
    # 'high' and 'also' each reference m1 (global count 2); 'low' references m2 (count 1).
    assert pend[0].impact >= pend[-1].impact
    impacts = {p.trace_id: p.impact for p in pend}
    assert impacts["low"] == 1
