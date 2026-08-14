import fakeredis.aioredis
import pytest

from agent.autonomy.budget import BudgetManager


@pytest.fixture
def mgr() -> BudgetManager:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return BudgetManager(
        client, default_tokens=1000, default_cost_usd=1.0, default_actions=10
    )


async def test_defaults_when_no_override(mgr: BudgetManager) -> None:
    limits = await mgr.get_limits()
    assert limits.tokens == 1000
    assert limits.cost_usd == 1.0
    assert limits.actions == 10


async def test_set_limits_overrides_persist(mgr: BudgetManager) -> None:
    await mgr.set_limits(tokens=42)
    limits = await mgr.get_limits()
    assert limits.tokens == 42
    # Unset fields keep defaults.
    assert limits.actions == 10


async def test_record_and_usage(mgr: BudgetManager) -> None:
    await mgr.record_llm(tok_in=100, tok_out=50, cost_usd=0.25)
    await mgr.record_action(2)
    usage = await mgr.get_usage()
    assert usage.tokens == 150
    assert usage.cost_usd == pytest.approx(0.25)
    assert usage.actions == 2


async def test_check_allows_within_budget(mgr: BudgetManager) -> None:
    dec = await mgr.check(tokens=500, cost_usd=0.5, actions=1)
    assert dec.allowed is True


async def test_check_denies_token_overflow(mgr: BudgetManager) -> None:
    await mgr.record_llm(tok_in=900, tok_out=0, cost_usd=0.0)
    dec = await mgr.check(tokens=200)
    assert dec.allowed is False
    assert "token budget" in dec.reason


async def test_check_denies_action_overflow(mgr: BudgetManager) -> None:
    await mgr.record_action(10)
    dec = await mgr.check(actions=1)
    assert dec.allowed is False
    assert "action budget" in dec.reason
