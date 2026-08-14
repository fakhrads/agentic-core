import fakeredis.aioredis
import pytest

from agent.bus.events import STREAM_AUDIT, STREAM_DLQ, Event, EventType
from agent.bus.streams import EventBus


@pytest.fixture
async def bus() -> EventBus:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return EventBus(client)


async def test_publish_and_history(bus: EventBus) -> None:
    await bus.publish(STREAM_AUDIT, Event(type=EventType.NOTE, message="one"))
    await bus.publish(STREAM_AUDIT, Event(type=EventType.NOTE, message="two"))
    assert await bus.xlen(STREAM_AUDIT) == 2

    msgs = await bus.history(STREAM_AUDIT, count=10)
    assert [m.event.message for m in msgs] == ["one", "two"]  # chronological


async def test_process_batch_acks_on_success(bus: EventBus) -> None:
    await bus.ensure_group(STREAM_AUDIT, "g1")
    await bus.publish(STREAM_AUDIT, Event(type="note", message="hi"))

    seen: list[str] = []

    async def handler(ev: Event) -> None:
        seen.append(ev.message)

    stats = await bus.process_batch(
        STREAM_AUDIT, "g1", "c1", handler, block_ms=10
    )
    assert stats.processed == 1
    assert seen == ["hi"]
    # Acked → nothing pending.
    pending = await bus.redis.xpending(STREAM_AUDIT, "g1")
    assert pending["pending"] == 0


async def test_failing_handler_retries_then_dead_letters(bus: EventBus) -> None:
    await bus.ensure_group(STREAM_AUDIT, "g2")
    await bus.publish(STREAM_AUDIT, Event(type="note", message="boom"))

    async def bad(ev: Event) -> None:
        raise RuntimeError("nope")

    # max_deliveries=2: first attempt retries (left pending), second dead-letters.
    # min_idle_ms=0 → reclaim the pending entry immediately on the next pass.
    s1 = await bus.process_batch(
        STREAM_AUDIT, "g2", "c1", bad, block_ms=10, max_deliveries=2, min_idle_ms=0
    )
    assert s1.retried == 1
    assert s1.dead_lettered == 0

    s2 = await bus.process_batch(
        STREAM_AUDIT, "g2", "c1", bad, block_ms=10, max_deliveries=2, min_idle_ms=0
    )
    assert s2.dead_lettered == 1
    assert await bus.xlen(STREAM_DLQ) == 1


async def test_unparseable_event_goes_to_dlq(bus: EventBus) -> None:
    await bus.ensure_group(STREAM_AUDIT, "g3")
    await bus.redis.xadd(STREAM_AUDIT, {"data": "not-json{"})

    async def handler(ev: Event) -> None:  # pragma: no cover - never called
        raise AssertionError("should not reach handler")

    stats = await bus.process_batch(STREAM_AUDIT, "g3", "c1", handler, block_ms=10)
    assert stats.parse_errors == 1
    assert await bus.xlen(STREAM_DLQ) == 1
