"""Redis Streams event bus: publish, consumer group, ack, DLQ.

Observation (`agent tail`) uses XREAD without a group so it never competes with
real consumers. Work consumers use a group with XREADGROUP + XACK; a message
that keeps failing past `max_deliveries` is routed to `events:dlq` and acked,
so a poison event can never wedge the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import redis.asyncio as redis_asyncio
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from agent.bus.events import STREAM_DLQ, Event
from agent.logging import get_logger

log = get_logger("bus")

Handler = Callable[[Event], Awaitable[None]]


@dataclass(slots=True)
class ProcessStats:
    read: int = 0
    processed: int = 0
    retried: int = 0
    dead_lettered: int = 0
    parse_errors: int = 0


@dataclass(slots=True)
class Message:
    id: str
    event: Event


class EventBus:
    """Thin async wrapper over Redis Streams."""

    def __init__(self, client: Redis[str]) -> None:
        self.redis = client

    @classmethod
    def from_url(cls, url: str) -> EventBus:
        return cls(redis_asyncio.from_url(url, decode_responses=True))

    async def aclose(self) -> None:
        await self.redis.aclose()  # type: ignore[attr-defined]  # types-redis lags

    # --- publish ---
    async def publish(self, stream: str, event: Event) -> str:
        msg_id: str = await self.redis.xadd(stream, event.to_fields())
        return msg_id

    async def xlen(self, stream: str) -> int:
        return int(await self.redis.xlen(stream))

    # --- consumer group ---
    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def _read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int,
        block_ms: int,
    ) -> list[tuple[str, dict[str, str]]]:
        resp = await self.redis.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms
        )
        if not resp:
            return []
        # resp = [(stream_name, [(id, {field: val}), ...])]
        _stream_name, entries = resp[0]
        return list(entries)

    async def _claim_pending(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        count: int,
    ) -> list[tuple[str, dict[str, str]]]:
        """Reclaim this group's pending (unacked) entries for redelivery.

        XREADGROUP '>' only returns never-delivered messages; a failed message
        sits in the PEL until claimed. XAUTOCLAIM increments its delivery count,
        which is how `process_batch` knows when to dead-letter.
        """
        resp = await self.redis.xautoclaim(
            stream, group, consumer, min_idle_time=min_idle_ms, start_id="0-0", count=count
        )
        # redis-py returns (cursor, entries) or (cursor, entries, deleted_ids).
        entries = resp[1] if len(resp) >= 2 else []
        return [(mid, fields) for mid, fields in entries if fields is not None]

    async def ack(self, stream: str, group: str, *ids: str) -> None:
        if ids:
            await self.redis.xack(stream, group, *ids)  # type: ignore[no-untyped-call]

    async def _delivery_count(self, stream: str, group: str, msg_id: str) -> int:
        pending = await self.redis.xpending_range(
            stream, group, min=msg_id, max=msg_id, count=1
        )
        if not pending:
            return 1
        return int(pending[0]["times_delivered"])

    async def to_dlq(self, stream: str, raw: dict[str, str], error: str) -> str:
        payload = dict(raw)
        payload["_origin_stream"] = stream
        payload["_error"] = error
        return str(await self.redis.xadd(STREAM_DLQ, payload))

    async def process_batch(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler: Handler,
        *,
        count: int = 16,
        block_ms: int = 5000,
        max_deliveries: int = 5,
        min_idle_ms: int = 30_000,
    ) -> ProcessStats:
        """Read one batch, dispatch to handler, ack/retry/DLQ per outcome.

        Pending (previously failed) entries idle longer than ``min_idle_ms`` are
        reclaimed first, then new entries are read.
        """
        stats = ProcessStats()
        pending = await self._claim_pending(
            stream, group, consumer, min_idle_ms=min_idle_ms, count=count
        )
        fresh = await self._read_group(
            stream, group, consumer, count=count, block_ms=block_ms
        )
        for msg_id, fields in [*pending, *fresh]:
            stats.read += 1
            try:
                event = Event.from_fields(fields)
            except Exception as exc:  # noqa: BLE001 — unparseable → straight to DLQ
                await self.to_dlq(stream, fields, error=f"parse: {exc}")
                await self.ack(stream, group, msg_id)
                stats.parse_errors += 1
                log.error("event_parse_failed", msg_id=msg_id, error=str(exc))
                continue

            try:
                await handler(event)
                await self.ack(stream, group, msg_id)
                stats.processed += 1
            except Exception as exc:  # noqa: BLE001 — handler failure is data, not crash
                delivered = await self._delivery_count(stream, group, msg_id)
                if delivered >= max_deliveries:
                    await self.to_dlq(stream, fields, error=str(exc))
                    await self.ack(stream, group, msg_id)
                    stats.dead_lettered += 1
                    log.error(
                        "event_dead_lettered",
                        msg_id=msg_id,
                        delivered=delivered,
                        error=str(exc),
                    )
                else:
                    # Leave unacked → redelivered on next XREADGROUP autoclaim.
                    stats.retried += 1
                    log.warning(
                        "event_retry",
                        msg_id=msg_id,
                        delivered=delivered,
                        error=str(exc),
                    )
        return stats

    # --- observation (tail): XREAD, no group ---
    async def history(self, stream: str, count: int = 50) -> list[Message]:
        entries = await self.redis.xrevrange(stream, count=count)
        out: list[Message] = []
        for mid, fields in reversed(entries):  # xrevrange = newest first
            event = _safe_event(fields)
            if event is not None:
                out.append(Message(id=mid, event=event))
        return out

    async def follow(
        self,
        stream: str,
        *,
        last_id: str = "$",
        block_ms: int = 2000,
    ) -> AsyncIterator[Message]:
        """Yield new events as they arrive (for `agent tail`). Runs until cancelled."""
        cursor = last_id
        while True:
            resp = await self.redis.xread({stream: cursor}, block=block_ms, count=32)
            if not resp:
                continue
            _stream_name, entries = resp[0]
            for mid, fields in entries:
                cursor = mid
                event = _safe_event(fields)
                if event is not None:
                    yield Message(id=mid, event=event)


def _safe_event(fields: dict[str, str]) -> Event | None:
    try:
        return Event.from_fields(fields)
    except Exception:  # noqa: BLE001 — observation must never crash on bad data
        return None


# Consumer group name registry (keep consumers coordinated).
GROUP_LOOP = "agent-loop"


@dataclass(slots=True)
class ConsumerRunner:
    """Long-running consumer: loops process_batch until `stop` is set."""

    bus: EventBus
    stream: str
    group: str
    consumer: str
    handler: Handler
    max_deliveries: int = 5
    min_idle_ms: int = 30_000
    # Short poll ceiling, not process_batch's 5s default — stop() must be
    # noticed quickly so `agent up` exits promptly on a single Ctrl-C instead
    # of tempting a second, forced one mid-shutdown.
    block_ms: int = 1000
    _stop: bool = field(default=False, init=False)

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        await self.bus.ensure_group(self.stream, self.group)
        log.info("consumer_started", stream=self.stream, group=self.group)
        while not self._stop:
            try:
                await self.bus.process_batch(
                    self.stream,
                    self.group,
                    self.consumer,
                    self.handler,
                    max_deliveries=self.max_deliveries,
                    min_idle_ms=self.min_idle_ms,
                    block_ms=self.block_ms,
                )
            except Exception as exc:  # noqa: BLE001 — a transient redis/network
                # hiccup on the blocking XREADGROUP call must not take the whole
                # daemon down with it (asyncio.gather propagates any task's
                # exception to every sibling task). Log and keep polling;
                # CancelledError isn't an Exception subclass so a real stop()
                # still exits the loop normally on the next check.
                log.warning("consumer_batch_failed", stream=self.stream, error=str(exc))
                await asyncio.sleep(1.0)
        log.info("consumer_stopped", stream=self.stream, group=self.group)
