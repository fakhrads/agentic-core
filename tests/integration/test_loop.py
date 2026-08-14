"""AgentLoop end-to-end on fakeredis + sqlite + FakeLLM (no network, no docker).

Asserts the M3 acceptance: inbound → reply, with episode/step persisted,
llm_call recorded, budget charged (tokens + action), audit events published.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import fakeredis.aioredis
import pytest
from sqlalchemy import func, select

from agent.autonomy.budget import BudgetManager
from agent.bus.events import STREAM_AUDIT, EventType
from agent.bus.streams import EventBus
from agent.channels.base import ChannelRegistry, InboundMessage
from agent.db.base import Base, dispose_engines, get_engine, session_scope
from agent.db.models import LLMCall
from agent.db.repo import get_episode_by_trace
from agent.llm.base import BudgetedLLM
from agent.llm.recorder import DBCostRecorder
from agent.loop.context import LoopContext
from agent.loop.runner import AgentLoop
from tests.fakes import FakeLLM, RecordingChannel


@pytest.fixture
async def dsn(tmp_path: Path) -> AsyncIterator[str]:
    d = f"sqlite+aiosqlite:///{tmp_path/'loop.db'}"
    engine = get_engine(d)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield d
    await dispose_engines()


def _make_loop(dsn: str, channel: RecordingChannel, *, tokens: int = 10_000) -> AgentLoop:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bus = EventBus(redis)
    budget = BudgetManager(
        redis, default_tokens=tokens, default_cost_usd=10.0, default_actions=100
    )
    llm = BudgetedLLM(FakeLLM(reply="halo balik"), budget, DBCostRecorder(dsn))
    channels = ChannelRegistry()
    channels.register(channel)
    ctx = LoopContext(dsn=dsn, bus=bus, budget=budget, llm=llm, channels=channels)
    return AgentLoop(ctx)


async def test_handle_replies_and_persists(dsn: str) -> None:
    channel = RecordingChannel()
    loop = _make_loop(dsn, channel)

    inbound = InboundMessage(channel="dev", chat_id="1", text="hai")
    await loop.handle(inbound, trace_id="trace-1")

    # Reply was sent.
    assert channel.sent == [("1", "halo balik")]

    # Episode persisted, done, with a reply step.
    async with session_scope(dsn) as session:
        ep = await get_episode_by_trace(session, "trace-1")
        assert ep is not None
        assert ep.status == "done"
        assert len(ep.steps) == 1
        assert ep.steps[0].kind == "reply"
        assert ep.steps[0].ok is True
        assert ep.steps[0].output["reply"] == "halo balik"

        # llm_call recorded.
        count = await session.scalar(select(func.count(LLMCall.id)))
        assert count == 1

    # Budget charged tokens + one action.
    usage = await loop.ctx.budget.get_usage()
    assert usage.tokens == 15  # 10 in + 5 out
    assert usage.actions == 1

    # Audit stream has started + step + ended for this trace.
    msgs = await loop.ctx.bus.history(STREAM_AUDIT, count=50)
    types = [m.event.type for m in msgs if m.event.trace_id == "trace-1"]
    assert EventType.EPISODE_STARTED in types
    assert EventType.EPISODE_ENDED in types


async def test_budget_exceeded_fails_gracefully(dsn: str) -> None:
    channel = RecordingChannel()
    # Tiny token budget → the pre-flight check trips BudgetExceeded.
    loop = _make_loop(dsn, channel, tokens=1)

    inbound = InboundMessage(channel="dev", chat_id="9", text="hello world this is long")
    await loop.handle(inbound, trace_id="trace-broke")

    # A friendly failure reply still went out.
    assert len(channel.sent) == 1
    assert "budget" in channel.sent[0][1].lower()

    async with session_scope(dsn) as session:
        ep = await get_episode_by_trace(session, "trace-broke")
        assert ep is not None
        assert ep.status == "failed"
        assert ep.steps[0].ok is False


async def test_retrieval_records_artefact_use(dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy import func, select

    from agent.db.models import ARTEFACT_MEMORY, EpisodeArtefact
    from agent.memory.models import SRC_USER
    from agent.memory.semantic import add_semantic
    from tests.fakes import FakeEmbedder

    channel = RecordingChannel()
    loop = _make_loop(dsn, channel)
    loop.ctx.embedder = FakeEmbedder()

    # Seed one active memory item.
    async with session_scope(dsn) as session:
        item = await add_semantic(
            session, content="user likes brevity", embedding=[0.1] * 4,
            source="chat", source_kind=SRC_USER,
        )
        item_id = item.id

    # Avoid pgvector (sqlite): stub hybrid_search to return the seeded item.
    async def fake_hybrid(session, vector, *, limit=8, candidate_factor=3, now=None):  # type: ignore[no-untyped-def]
        from agent.memory.semantic import get as get_mem

        got = await get_mem(session, item_id)
        return [(got, 0.9)] if got else []

    monkeypatch.setattr("agent.loop.runner.hybrid_search", fake_hybrid)

    await loop.handle(InboundMessage(channel="dev", chat_id="1", text="hi"), trace_id="tr")

    async with session_scope(dsn) as session:
        n = await session.scalar(select(func.count(EpisodeArtefact.id)))
        assert n == 1
        art = (await session.scalars(select(EpisodeArtefact))).one()
        assert art.kind == ARTEFACT_MEMORY and art.ref_id == item_id


async def test_unknown_tool_call_records_tool_need(dsn: str) -> None:
    from sqlalchemy import select

    from agent.db.models import NEED_OPEN, ToolNeed
    from agent.loop.context import LoopContext
    from agent.tools.cache import ToolCache
    from tests.fakes import FakeToolsClient, ScriptedLLM, final_result, tool_call_result

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    budget = BudgetManager(
        redis, default_tokens=10_000, default_cost_usd=10.0, default_actions=100
    )
    llm = BudgetedLLM(
        ScriptedLLM([tool_call_result("c1", "ghost", {"x": 1}), final_result("done")]),
        budget,
        DBCostRecorder(dsn),
    )
    client = FakeToolsClient(tools=[], results={})  # empty registry → 'ghost' unknown
    cache = ToolCache(client)
    await cache.refresh()
    channel = RecordingChannel()
    channels = ChannelRegistry()
    channels.register(channel)
    ctx = LoopContext(
        dsn=dsn, bus=EventBus(redis), budget=budget, llm=llm, channels=channels,
        tools_client=client, tool_cache=cache,  # type: ignore[arg-type]
    )
    loop = AgentLoop(ctx)

    await loop.handle(InboundMessage(channel="dev", chat_id="1", text="call ghost"), "tn")

    async with session_scope(dsn) as session:
        need = (await session.scalars(select(ToolNeed))).one()
        assert need.name == "ghost" and need.status == NEED_OPEN
        # The backend was never asked to invoke the unknown tool.
        assert client.invocations == []


async def test_duplicate_delivery_is_idempotent(dsn: str) -> None:
    channel = RecordingChannel()
    loop = _make_loop(dsn, channel)
    inbound = InboundMessage(channel="dev", chat_id="1", text="hai")

    await loop.handle(inbound, trace_id="dup")
    await loop.handle(inbound, trace_id="dup")  # redelivery

    # Only one reply, one episode.
    assert len(channel.sent) == 1
    async with session_scope(dsn) as session:
        ep = await get_episode_by_trace(session, "dup")
        assert ep is not None
        assert len(ep.steps) == 1
