"""Liveness heartbeat in Redis so monitoring works from another machine.

The daemon writes a TTL'd heartbeat; `agent watch` reads it. When the daemon
stops, the key expires and the dashboard shows the loop as stopped.
"""

from __future__ import annotations

from datetime import datetime

from redis.asyncio import Redis

from agent.db.base import utcnow

HEARTBEAT_KEY = "agent:heartbeat"
STARTED_KEY = "agent:started_at"
HEARTBEAT_TTL_S = 30


async def mark_started(redis: Redis[str]) -> None:
    await redis.set(STARTED_KEY, utcnow().isoformat())


async def beat(redis: Redis[str]) -> None:
    await redis.set(HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL_S)


async def is_alive(redis: Redis[str]) -> bool:
    return bool(await redis.exists(HEARTBEAT_KEY))


async def started_at(redis: Redis[str]) -> datetime | None:
    raw = await redis.get(STARTED_KEY)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
