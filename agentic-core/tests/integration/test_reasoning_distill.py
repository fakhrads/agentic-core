"""ReasoningBank + skill distillation from trajectories (sqlite, fake distiller)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base
from agent.db.models import EPISODE_DONE, EPISODE_FAILED
from agent.db.repo import add_step, create_episode, end_episode, get_episode_by_trace
from agent.memory.models import TIER_REASONING, MemoryItem
from agent.memory.reasoning import distill_reasoning
from agent.skills.distill import distill_skill


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _episode(session: AsyncSession, trace: str, status: str) -> None:
    ep = await create_episode(session, trace_id=trace, source="test")
    await add_step(session, ep, kind="plan", input={"g": "x"}, ok=True)
    await add_step(session, ep, kind="reply", output={"r": "y"}, ok=(status == EPISODE_DONE))
    await end_episode(session, trace, status=status, summary="did a thing")
    await session.commit()


async def _distiller(prompt: str) -> str:
    return "the distilled strategy text"


async def test_success_distills_strategy(session: AsyncSession) -> None:
    await _episode(session, "t-ok", EPISODE_DONE)
    ep = await get_episode_by_trace(session, "t-ok")
    assert ep is not None

    item = await distill_reasoning(session, ep, _distiller)
    assert item.tier == TIER_REASONING
    assert item.content.startswith("[STRATEGY]")
    assert item.source_kind == "self"


async def test_failure_distills_avoid_lesson(session: AsyncSession) -> None:
    await _episode(session, "t-bad", EPISODE_FAILED)
    ep = await get_episode_by_trace(session, "t-bad")
    assert ep is not None

    item = await distill_reasoning(session, ep, _distiller)
    assert item.content.startswith("[AVOID]")


async def test_distill_is_idempotent_per_trace(session: AsyncSession) -> None:
    await _episode(session, "t-ok", EPISODE_DONE)
    ep = await get_episode_by_trace(session, "t-ok")
    assert ep is not None

    a = await distill_reasoning(session, ep, _distiller)
    b = await distill_reasoning(session, ep, _distiller)
    assert a.id == b.id
    count = await session.scalar(
        select(func.count(MemoryItem.id)).where(MemoryItem.tier == TIER_REASONING)
    )
    assert count == 1


async def test_distill_skill_only_from_success(session: AsyncSession) -> None:
    await _episode(session, "t-bad", EPISODE_FAILED)
    ep_bad = await get_episode_by_trace(session, "t-bad")
    assert ep_bad is not None
    assert await distill_skill(session, ep_bad, _distiller) is None

    await _episode(session, "t-ok", EPISODE_DONE)
    ep_ok = await get_episode_by_trace(session, "t-ok")
    assert ep_ok is not None
    skill = await distill_skill(session, ep_ok, _distiller, name="my_skill")
    assert skill is not None
    assert skill.status == "probation"
    assert skill.created_from_trace == "t-ok"
