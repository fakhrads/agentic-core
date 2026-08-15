from collections.abc import Awaitable, Callable

import httpx
import pytest

from agent.llm.base import Attempt, ChatMessage
from agent.llm.deepseek import DeepSeekProvider, LLMError


def _collector(sink: list[Attempt]) -> Callable[[Attempt], Awaitable[None]]:
    async def _c(attempt: Attempt) -> None:
        sink.append(attempt)

    return _c


def _provider(handler: object) -> DeepSeekProvider:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return DeepSeekProvider(
        base_url="https://api.deepseek.com",
        api_key="test",
        model="deepseek-chat",
        timeout_s=5,
        transport=transport,
    )


def _ok_body() -> dict[str, object]:
    return {
        "choices": [{"message": {"content": "hi there"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 8},
    }


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fast(_seconds: float) -> None:
        return None

    monkeypatch.setattr("agent.llm.deepseek.asyncio.sleep", fast)


async def test_success_returns_result_with_usage() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler)
    attempts: list[Attempt] = []
    result = await provider.complete(
        [ChatMessage("user", "hi")], max_tokens=16, on_attempt=_collector(attempts)
    )
    assert result.text == "hi there"
    assert result.tok_in == 12 and result.tok_out == 8
    assert result.cost_usd > 0
    assert len(attempts) == 1 and attempts[0].ok
    await provider.aclose()


async def test_retries_on_503_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=_ok_body())

    provider = _provider(handler)
    attempts: list[Attempt] = []
    result = await provider.complete(
        [ChatMessage("user", "hi")], max_tokens=16, on_attempt=_collector(attempts)
    )
    assert result.text == "hi there"
    assert calls["n"] == 3
    # 2 failed attempts recorded + 1 success = 3 attempts.
    assert len(attempts) == 3
    assert [a.ok for a in attempts] == [False, False, True]
    await provider.aclose()


async def test_provider_name_overrides_cost_label() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body())

    provider = DeepSeekProvider(
        base_url="https://api.openai.com/v1",
        api_key="test",
        model="gpt-4o-mini",
        timeout_s=5,
        provider_name="openai",
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    assert provider.name == "openai"
    result = await provider.complete([ChatMessage("user", "hi")], max_tokens=16)
    assert result.provider == "openai"
    await provider.aclose()


async def test_does_not_retry_on_400() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, text="bad request")

    provider = _provider(handler)
    attempts: list[Attempt] = []
    with pytest.raises(LLMError):
        await provider.complete(
            [ChatMessage("user", "hi")], max_tokens=16, on_attempt=_collector(attempts)
        )
    assert calls["n"] == 1  # no retry
    assert len(attempts) == 1 and attempts[0].ok is False
    await provider.aclose()
