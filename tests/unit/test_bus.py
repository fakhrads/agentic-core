import asyncio

import fakeredis.aioredis
import pytest
import redis.exceptions

from agent.bus.events import STREAM_AUDIT, STREAM_DLQ, Event, EventType
from agent.bus.streams import ConsumerRunner, EventBus


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


class _FlakyBus:
    """process_batch fails a few times (simulating a transient redis hiccup),
    then succeeds. ensure_group is a no-op."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    async def ensure_group(self, stream: str, group: str) -> None:
        return None

    async def process_batch(self, *args: object, **kwargs: object) -> None:
        self.calls += 1
        # Always yield — a real xreadgroup call awaits I/O either way, and
        # without this the success path never yields control back to the
        # event loop, starving the test's own stop-watcher task forever.
        await asyncio.sleep(0)
        if self.calls <= self.fail_times:
            raise redis.exceptions.TimeoutError("Timeout reading from localhost:6379")


async def test_consumer_runner_survives_transient_redis_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: a single spurious TimeoutError on the blocking XREADGROUP
    # call (redis-py occasionally raises these under network jitter, e.g. via
    # Docker port-forwarding — not an actual outage) used to crash the whole
    # daemon, since asyncio.gather propagates one task's exception to every
    # sibling. The consumer loop must log and keep going instead.
    # `agent.bus.streams.asyncio` is the real asyncio module (not a copy), so
    # capture the original sleep before patching to avoid patching over itself.
    _real_sleep = asyncio.sleep
    monkeypatch.setattr("agent.bus.streams.asyncio.sleep", lambda _s: _real_sleep(0))

    async def handler(ev: Event) -> None:  # pragma: no cover - not invoked
        pass

    flaky_bus = _FlakyBus(fail_times=3)
    runner = ConsumerRunner(
        bus=flaky_bus,  # type: ignore[arg-type]
        stream=STREAM_AUDIT,
        group="g5",
        consumer="c1",
        handler=handler,
    )

    async def _stop_after_calls() -> None:
        while flaky_bus.calls < 5:  # noqa: ASYNC110 - polling a plain counter, not a real wait
            await asyncio.sleep(0)
        runner.stop()

    await asyncio.wait_for(
        asyncio.gather(runner.run(), _stop_after_calls()), timeout=2.0
    )
    assert flaky_bus.calls >= 5  # kept looping through the failures


async def test_consumer_runner_defaults_to_a_short_poll(bus: EventBus) -> None:
    # Regression: block_ms used to default to process_batch's 5s ceiling, so
    # stop() could sit unnoticed for up to 5s per poll — noticeable enough to
    # tempt a second, forced Ctrl-C on `agent up`. ConsumerRunner now pins its
    # own short default instead of inheriting process_batch's.
    #
    # Not exercised end-to-end here: fakeredis's async XREADGROUP doesn't
    # honor `block` (returns immediately), so a real run()/stop() loop against
    # it spins hot instead of blocking — this only checks the wiring.
    async def handler(ev: Event) -> None:  # pragma: no cover - not invoked
        pass

    runner = ConsumerRunner(
        bus=bus, stream=STREAM_AUDIT, group="g4", consumer="c1", handler=handler
    )
    assert runner.block_ms == 1000
