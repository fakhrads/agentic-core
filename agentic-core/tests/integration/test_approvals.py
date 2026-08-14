"""Approval queue + forge request flow on sqlite."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.autonomy.approvals import (
    ApprovalError,
    decide_approval,
    get_approval,
    list_pending,
    request_approval,
)
from agent.db.base import Base
from agent.db.models import APPROVAL_APPROVED, APPROVAL_REJECTED
from agent.tools.forge import ACTION_TOOL_REGISTER, ForgeArtifact, ToolForge


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def test_request_approval_only_for_approve_tier(session: AsyncSession) -> None:
    # chat.send is AUTO — must not create an approval.
    with pytest.raises(ApprovalError):
        await request_approval(session, action_kind="chat.send", payload={})


async def test_approval_lifecycle(session: AsyncSession) -> None:
    a = await request_approval(
        session, action_kind=ACTION_TOOL_REGISTER, payload={"submission": {"name": "x"}}
    )
    assert a.status == "pending"
    pending = await list_pending(session)
    assert [p.id for p in pending] == [a.id]

    decided = await decide_approval(session, a.id, approved=True)
    assert decided is not None and decided.status == APPROVAL_APPROVED
    assert decided.decided_at is not None

    # Deciding twice is an error.
    with pytest.raises(ApprovalError):
        await decide_approval(session, a.id, approved=False)


async def test_reject(session: AsyncSession) -> None:
    a = await request_approval(
        session, action_kind=ACTION_TOOL_REGISTER, payload={"submission": {}}
    )
    decided = await decide_approval(session, a.id, approved=False)
    assert decided is not None and decided.status == APPROVAL_REJECTED


async def test_forge_and_request_creates_pending_approval(session: AsyncSession) -> None:
    async def generator(need: str) -> ForgeArtifact:
        return ForgeArtifact(
            name="csv_diff",
            description=need,
            params_schema={"type": "object"},
            code="def run(): ...",
            tests="def test_run(): assert True",
        )

    forge = ToolForge(generator)
    approval, artifact = await forge.forge_and_request(
        session, need="diff two csv files", trace_id="t-9"
    )

    assert approval.action_kind == ACTION_TOOL_REGISTER
    assert approval.status == "pending"
    assert artifact.name == "csv_diff"
    # Payload carries the exact POST /tools submission for the approver to send.
    sub = approval.payload["submission"]
    assert sub["name"] == "csv_diff"
    assert sub["requested_by_trace"] == "t-9"
    assert "code" in sub and "tests" in sub

    stored = await get_approval(session, approval.id)
    assert stored is not None
