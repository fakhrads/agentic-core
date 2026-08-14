import fakeredis.aioredis

from agent.heartbeat import beat, is_alive, mark_started, started_at


async def test_heartbeat_lifecycle() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await is_alive(redis) is False

    await mark_started(redis)
    await beat(redis)
    assert await is_alive(redis) is True
    assert await started_at(redis) is not None


async def test_started_at_absent_returns_none() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    assert await started_at(redis) is None
