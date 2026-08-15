"""DeepSeek provider — OpenAI-compatible chat completions over httpx.

Retries only transient failures (connect, timeout, 429, 502/503). 400/401 fail
fast. Every attempt fires `on_attempt` so the wrapper can record it, including
failed retries.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import httpx

from agent.llm.base import (
    Attempt,
    ChatMessage,
    LLMResult,
    OnAttempt,
    ToolCall,
    compute_cost,
    messages_to_payload,
)
from agent.logging import get_logger

log = get_logger("llm.deepseek")

_RETRYABLE_STATUS = {429, 502, 503}
_MAX_ATTEMPTS = 3


class LLMError(Exception):
    pass


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    if not raw:
        return []
    calls: list[ToolCall] = []
    for tc in raw:
        fn = tc.get("function", {})
        args_raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(args_raw)
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
    return calls


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float,
        provider_name: str = "deepseek",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = provider_name
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.7,
        tools: Sequence[dict[str, Any]] | None = None,
        on_attempt: OnAttempt | None = None,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages_to_payload(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        last_error: str = "unknown"
        for attempt_no in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                await self._emit_failure(on_attempt, last_error)
                if attempt_no < _MAX_ATTEMPTS:
                    await asyncio.sleep(0.5 * 2 ** (attempt_no - 1))
                    continue
                raise LLMError(last_error) from exc

            if resp.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                await self._emit_failure(on_attempt, last_error)
                if attempt_no < _MAX_ATTEMPTS:
                    await asyncio.sleep(0.5 * 2 ** (attempt_no - 1))
                    continue
                raise LLMError(last_error)

            if resp.status_code >= 400:
                # 400/401/403/404 → do not retry.
                last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                await self._emit_failure(on_attempt, last_error)
                raise LLMError(last_error)

            return await self._parse_success(resp, on_attempt)

        raise LLMError(last_error)

    async def _parse_success(
        self, resp: httpx.Response, on_attempt: OnAttempt | None
    ) -> LLMResult:
        data = resp.json()
        choice = data["choices"][0]
        message = choice["message"]
        text = message.get("content") or ""
        finish_reason = choice.get("finish_reason", "stop")
        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        usage = data.get("usage", {})
        tok_in = int(usage.get("prompt_tokens", 0))
        tok_out = int(usage.get("completion_tokens", 0))
        cost = compute_cost(self.name, self.model, tok_in, tok_out)
        if on_attempt is not None:
            await on_attempt(
                Attempt(
                    provider=self.name,
                    model=self.model,
                    tok_in=tok_in,
                    tok_out=tok_out,
                    cost_usd=cost,
                    ok=True,
                )
            )
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            tok_in=tok_in,
            tok_out=tok_out,
            cost_usd=cost,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            raw_message=message,
        )

    async def _emit_failure(self, on_attempt: OnAttempt | None, error: str) -> None:
        log.warning("deepseek_attempt_failed", error=error)
        if on_attempt is not None:
            await on_attempt(
                Attempt(
                    provider=self.name,
                    model=self.model,
                    tok_in=0,
                    tok_out=0,
                    cost_usd=0.0,
                    ok=False,
                    error=error,
                )
            )
