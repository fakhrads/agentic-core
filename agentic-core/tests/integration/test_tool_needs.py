"""Tool-need recording + forge-from-needs (autonomous forge trigger)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.autonomy.approvals import list_pending
from agent.db.base import Base
from agent.db.models import NEED_FORGED, NEED_OPEN
from agent.tools.forge import ForgeArtifact, ToolForge
from agent.tools.needs import (
    list_open_needs,
    mark_forged,
    record_tool_need,
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


async def test_record_need_upserts_and_counts(session: AsyncSession) -> None:
    n1 = await record_tool_need(session, name="csv_diff", description="d", args={"a": 1})
    assert n1.count == 1 and n1.status == NEED_OPEN
    n2 = await record_tool_need(session, name="csv_diff", description="d", args={"a": 2})
    assert n2.id == n1.id and n2.count == 2  # same need, bumped

    other = await record_tool_need(session, name="html_parse", description="d")
    assert other.id != n1.id


async def test_forged_need_not_recounted(session: AsyncSession) -> None:
    n = await record_tool_need(session, name="csv_diff", description="d")
    await mark_forged(session, n.id)
    again = await record_tool_need(session, name="csv_diff", description="d")
    assert again.status == NEED_FORGED
    assert again.count == 1  # not bumped once forged


async def test_open_needs_ranked_by_count(session: AsyncSession) -> None:
    await record_tool_need(session, name="low", description="d")
    hi = await record_tool_need(session, name="high", description="d")
    await record_tool_need(session, name="high", description="d")  # count 2
    _ = hi
    ranked = await list_open_needs(session)
    assert ranked[0].name == "high"


async def test_forge_from_need_creates_approval(session: AsyncSession) -> None:
    await record_tool_need(session, name="csv_diff", description="diff csvs", args={})

    async def generator(need_text: str) -> ForgeArtifact:
        assert "csv_diff" in need_text  # need context is passed to the generator
        return ForgeArtifact(
            name="csv_diff",
            description="diff two csvs",
            params_schema={"type": "object"},
            code="def run(): ...",
            tests="def test_run(): assert True",
        )

    forge = ToolForge(generator)
    needs = await list_open_needs(session)
    for need in needs:
        need_text = f"{need.description}\nProposed name: {need.name}"
        approval, _art = await forge.forge_and_request(
            session, need=need_text, trace_id="t"
        )
        await mark_forged(session, need.id)
        assert approval.status == "pending"

    pending = await list_pending(session)
    assert len(pending) == 1
    assert pending[0].payload["submission"]["name"] == "csv_diff"
