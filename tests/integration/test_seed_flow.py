"""End-to-end seed flow on fakeredis + sqlite (no docker needed).

Drives the real `agent dev seed` code path and asserts the DB episode and the
audit stream stay consistent: same trace_id, correct event sequence, ordered
steps. This is the M2 "jalan dengan event dummy" acceptance, minus real infra.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import fakeredis.aioredis
import pytest

from agent.bus.events import STREAM_AUDIT, EventType
from agent.bus.streams import EventBus
from agent.cli.dev import _seed
from agent.db.base import Base, dispose_engines, get_engine, session_scope
from agent.db.repo import get_episode_by_trace


@pytest.fixture
async def sqlite_dsn(tmp_path: Path) -> AsyncIterator[str]:
    # File-based (not :memory:) so multiple connections share one DB.
    dsn = f"sqlite+aiosqlite:///{tmp_path/'seed.db'}"
    engine = get_engine(dsn)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield dsn
    await dispose_engines()


async def test_seed_persists_episode_and_publishes_audit(sqlite_dsn: str) -> None:
    shared = fakeredis.aioredis.FakeRedis(decode_responses=True)

    trace_id = await _seed(
        fail=False,
        bus_factory=lambda: EventBus(shared),
        dsn=sqlite_dsn,
    )

    # DB side: episode done, three ordered steps.
    async with session_scope(sqlite_dsn) as session:
        ep = await get_episode_by_trace(session, trace_id)
    assert ep is not None
    assert ep.status == "done"
    assert [s.idx for s in ep.steps] == [0, 1, 2]
    assert [s.kind for s in ep.steps] == ["plan", "tool_call", "reply"]
    assert all(s.ok for s in ep.steps)

    # Bus side: started + 3 step.finished + ended, all sharing the trace_id.
    bus = EventBus(shared)
    msgs = await bus.history(STREAM_AUDIT, count=50)
    events = [m.event for m in msgs if m.event.trace_id == trace_id]
    types = [e.type for e in events]
    assert types == [
        EventType.EPISODE_STARTED,
        EventType.STEP_FINISHED,
        EventType.STEP_FINISHED,
        EventType.STEP_FINISHED,
        EventType.EPISODE_ENDED,
    ]


async def test_seed_failed_marks_episode_failed(sqlite_dsn: str) -> None:
    shared = fakeredis.aioredis.FakeRedis(decode_responses=True)
    trace_id = await _seed(
        fail=True, bus_factory=lambda: EventBus(shared), dsn=sqlite_dsn
    )
    async with session_scope(sqlite_dsn) as session:
        ep = await get_episode_by_trace(session, trace_id)
    assert ep is not None
    assert ep.status == "failed"
    assert all(s.ok is False for s in ep.steps)
