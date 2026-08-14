from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base
from agent.db.models import EPISODE_DONE, EPISODE_FAILED
from agent.db.repo import (
    add_step,
    create_episode,
    end_episode,
    get_episode_by_trace,
    list_episodes,
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


async def test_create_and_fetch_episode_with_steps(session: AsyncSession) -> None:
    ep = await create_episode(session, trace_id="t-1", source="telegram")
    await add_step(session, ep, kind="plan", input={"goal": "x"}, ok=True)
    await add_step(session, ep, kind="reply", output={"sent": True}, duration_ms=40, ok=True)
    await session.commit()

    fetched = await get_episode_by_trace(session, "t-1")
    assert fetched is not None
    assert fetched.source == "telegram"
    assert [s.idx for s in fetched.steps] == [0, 1]  # auto-incremented, ordered
    assert fetched.steps[0].kind == "plan"


async def test_end_episode_sets_status_and_timestamp(session: AsyncSession) -> None:
    await create_episode(session, trace_id="t-2", source="self")
    ep = await end_episode(session, "t-2", status=EPISODE_DONE, summary="done")
    assert ep is not None
    assert ep.status == EPISODE_DONE
    assert ep.ended_at is not None
    assert ep.summary == "done"


async def test_end_missing_episode_returns_none(session: AsyncSession) -> None:
    assert await end_episode(session, "nope", status=EPISODE_DONE) is None


async def test_list_episodes_failed_filter(session: AsyncSession) -> None:
    await create_episode(session, trace_id="ok", source="s")
    await end_episode(session, "ok", status=EPISODE_DONE)
    await create_episode(session, trace_id="bad", source="s")
    await end_episode(session, "bad", status=EPISODE_FAILED)
    await session.commit()

    failed = await list_episodes(session, failed=True)
    assert {e.trace_id for e in failed} == {"bad"}
    all_eps = await list_episodes(session)
    assert {e.trace_id for e in all_eps} == {"ok", "bad"}
