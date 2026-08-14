"""Dashboard snapshot gathering on fakeredis + sqlite, plus rendering smoke."""

from collections.abc import AsyncIterator
from pathlib import Path

import fakeredis.aioredis
import pytest

from agent.autonomy.approvals import request_approval
from agent.autonomy.budget import BudgetManager
from agent.bus.events import STREAM_AUDIT, STREAM_INBOUND, Event
from agent.bus.streams import EventBus
from agent.cli.watch import render
from agent.dashboard import gather_snapshot
from agent.db.base import Base, dispose_engines, get_engine, session_scope
from agent.db.repo import create_episode, record_regression_run
from agent.heartbeat import beat, mark_started
from agent.tools.forge import ACTION_TOOL_REGISTER


@pytest.fixture
async def dsn(tmp_path: Path) -> AsyncIterator[str]:
    d = f"sqlite+aiosqlite:///{tmp_path/'dash.db'}"
    engine = get_engine(d)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield d
    await dispose_engines()


async def test_gather_snapshot_reflects_state(dsn: str) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bus = EventBus(redis)
    budget = BudgetManager(
        redis, default_tokens=1000, default_cost_usd=1.0, default_actions=10
    )

    # Seed live state.
    await mark_started(redis)
    await beat(redis)
    await bus.redis.xadd(STREAM_INBOUND, {"data": "x"})  # 1 queued
    await bus.publish(STREAM_AUDIT, Event(type="tool_call", message="regex_explainer ok"))
    await budget.record_llm(100, 50, 0.25)

    async with session_scope(dsn) as session:
        await create_episode(session, trace_id="run-1", source="telegram")  # running
        await record_regression_run(
            session, suite="regression", passed=18, total=20, detail={"dropped": 1}
        )
        await request_approval(
            session,
            action_kind=ACTION_TOOL_REGISTER,
            payload={"submission": {"name": "csv_diff"}},
        )

    snap = await gather_snapshot(dsn, redis, bus, budget)

    assert snap.running is True
    assert snap.uptime_s is not None
    assert snap.queue_len == 1
    assert snap.budget is not None and snap.budget.usage.tokens == 150
    assert snap.active_episode is not None and snap.active_episode.trace_id == "run-1"
    assert snap.regression is not None and snap.regression.passed == 18
    assert len(snap.events) == 1
    assert len(snap.approvals) == 1
    assert "csv_diff" in snap.approvals[0].summary

    # Rendering must not raise on a populated snapshot.
    render(snap)


async def test_gather_snapshot_empty_is_safe(dsn: str) -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bus = EventBus(redis)
    budget = BudgetManager(
        redis, default_tokens=1000, default_cost_usd=1.0, default_actions=10
    )
    snap = await gather_snapshot(dsn, redis, bus, budget)
    assert snap.running is False
    assert snap.active_episode is None
    assert snap.regression is None
    assert snap.approvals == []
    render(snap)  # empty render must not raise
