"""Goal store + feasibility probe on sqlite (fake prober, no LLM)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.autonomy.goals import (
    break_into_subgoals,
    create_goal,
    drop_goal,
    open_self_goals,
    probe_goal,
)
from agent.db.base import Base
from agent.db.models import (
    GOAL_ORIGIN_SELF,
    GOAL_ORIGIN_USER,
    GSTATUS_ACTIVE,
    GSTATUS_DONE,
    GSTATUS_DROPPED,
    GSTATUS_INFEASIBLE,
    GSTATUS_OPEN,
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


async def test_user_goal_skips_probe_to_active(session: AsyncSession) -> None:
    g = await create_goal(session, text="do a thing", origin=GOAL_ORIGIN_USER)
    assert g.status == GSTATUS_ACTIVE


async def test_self_goal_starts_open(session: AsyncSession) -> None:
    g = await create_goal(session, text="learn something", origin=GOAL_ORIGIN_SELF)
    assert g.status == GSTATUS_OPEN
    opens = await open_self_goals(session, limit=10)
    assert g.id in {x.id for x in opens}


async def test_probe_partial_becomes_active(session: AsyncSession) -> None:
    g = await create_goal(session, text="hard task", origin=GOAL_ORIGIN_SELF)

    async def prober(_prompt: str) -> str:
        return "made some progress\nPARTIAL"

    await probe_goal(session, g, prober)
    assert g.status == GSTATUS_ACTIVE
    assert g.probe_result is not None
    assert g.probe_result["marker"] == "PARTIAL"


async def test_probe_solved_becomes_done(session: AsyncSession) -> None:
    g = await create_goal(session, text="easy", origin=GOAL_ORIGIN_SELF)

    async def prober(_prompt: str) -> str:
        return "trivially done\nSOLVED"

    await probe_goal(session, g, prober)
    assert g.status == GSTATUS_DONE


async def test_probe_stuck_infeasible_then_subgoals_respect_depth(
    session: AsyncSession,
) -> None:
    g = await create_goal(session, text="impossible", origin=GOAL_ORIGIN_SELF)

    async def prober(_prompt: str) -> str:
        return "no progress\nSTUCK"

    await probe_goal(session, g, prober)
    assert g.status == GSTATUS_INFEASIBLE

    children = await break_into_subgoals(session, g, ["part a", "part b"])
    assert len(children) == 2
    assert all(c.depth == 1 for c in children)

    # A depth-2 child cannot spawn further sub-goals.
    grandchild = children[0]
    grandchild.depth = 2
    assert await break_into_subgoals(session, grandchild, ["deeper"]) == []


async def test_drop_goal(session: AsyncSession) -> None:
    g = await create_goal(session, text="x", origin=GOAL_ORIGIN_USER)
    dropped = await drop_goal(session, g.id)
    assert dropped is not None and dropped.status == GSTATUS_DROPPED
