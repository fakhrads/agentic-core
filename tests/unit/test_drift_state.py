import fakeredis.aioredis

from agent.evolution.drift import DriftState


async def test_drift_state_set_read_clear() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    drift = DriftState(redis)

    assert await drift.is_paused() is False

    await drift.set_paused("regression drop: ['arith_mul']")
    assert await drift.is_paused() is True
    status = await drift.status()
    assert status.paused is True
    assert "arith_mul" in status.reason
    assert status.since is not None

    await drift.clear()
    assert await drift.is_paused() is False
