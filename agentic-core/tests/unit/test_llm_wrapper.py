import fakeredis.aioredis
import pytest

from agent.autonomy.budget import BudgetExceeded, BudgetManager
from agent.llm.base import BudgetedLLM, ChatMessage, compute_cost, estimate_tokens
from tests.fakes import FakeLLM, ListRecorder


def _budget(tokens: int = 1000, cost: float = 1.0, actions: int = 10) -> BudgetManager:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return BudgetManager(
        client, default_tokens=tokens, default_cost_usd=cost, default_actions=actions
    )


def test_estimate_tokens_and_cost() -> None:
    msgs = [ChatMessage("user", "x" * 40)]
    assert estimate_tokens(msgs) == 10
    assert compute_cost("deepseek", "deepseek-chat", 1_000_000, 0) == pytest.approx(0.27)
    assert compute_cost("unknown", "x", 1000, 1000) == 0.0


async def test_wrapper_records_attempt_and_charges_budget() -> None:
    budget = _budget()
    recorder = ListRecorder()
    llm = BudgetedLLM(FakeLLM(tok_in=10, tok_out=5), budget, recorder)

    result = await llm.complete([ChatMessage("user", "hi")], max_tokens=16)

    assert result.text == "hello back"
    # Attempt was recorded.
    assert len(recorder.records) == 1
    attempt, _trace = recorder.records[0]
    assert attempt.ok and attempt.tok_in == 10 and attempt.tok_out == 5
    # Budget charged the actual usage.
    usage = await budget.get_usage()
    assert usage.tokens == 15
    assert usage.cost_usd == pytest.approx(0.0001)


async def test_wrapper_raises_when_over_budget_before_calling() -> None:
    budget = _budget(tokens=5)  # too small for est_in + max_tokens
    recorder = ListRecorder()
    provider = FakeLLM()
    llm = BudgetedLLM(provider, budget, recorder)

    with pytest.raises(BudgetExceeded):
        await llm.complete([ChatMessage("user", "hello world")], max_tokens=100)

    # Provider was never called; nothing recorded.
    assert provider.calls == 0
    assert recorder.records == []
