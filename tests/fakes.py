"""Deterministic fakes for tests. Never call DeepSeek in tests (spec §12)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent.llm.base import Attempt, ChatMessage, LLMResult, OnAttempt, ToolCall
from agent.tools.models import InvokeError, InvokeResult, ToolEntry


class FakeLLM:
    """Echoes a canned reply and reports fixed token usage."""

    name = "fake"
    model = "fake-1"

    def __init__(self, reply: str = "hello back", tok_in: int = 10, tok_out: int = 5) -> None:
        self.reply = reply
        self.tok_in = tok_in
        self.tok_out = tok_out
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.7,
        tools: Sequence[dict[str, Any]] | None = None,
        on_attempt: OnAttempt | None = None,
    ) -> LLMResult:
        self.calls += 1
        cost = 0.0001
        if on_attempt is not None:
            await on_attempt(
                Attempt(
                    provider=self.name,
                    model=self.model,
                    tok_in=self.tok_in,
                    tok_out=self.tok_out,
                    cost_usd=cost,
                    ok=True,
                )
            )
        return LLMResult(
            text=self.reply,
            provider=self.name,
            model=self.model,
            tok_in=self.tok_in,
            tok_out=self.tok_out,
            cost_usd=cost,
        )


class ScriptedLLM:
    """Returns a scripted sequence of LLMResults (for tool-loop tests)."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, script: list[LLMResult]) -> None:
        self._script = script
        self.calls = 0
        self.last_tools: Sequence[dict[str, Any]] | None = None

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.7,
        tools: Sequence[dict[str, Any]] | None = None,
        on_attempt: OnAttempt | None = None,
    ) -> LLMResult:
        self.last_tools = tools
        result = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if on_attempt is not None:
            await on_attempt(
                Attempt(
                    provider=self.name,
                    model=self.model,
                    tok_in=result.tok_in,
                    tok_out=result.tok_out,
                    cost_usd=result.cost_usd,
                    ok=True,
                )
            )
        return result


def tool_call_result(call_id: str, name: str, arguments: dict[str, Any]) -> LLMResult:
    return LLMResult(
        text="",
        provider="scripted",
        model="scripted-1",
        tok_in=8,
        tok_out=4,
        cost_usd=0.0001,
        finish_reason="tool_calls",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
    )


def final_result(text: str) -> LLMResult:
    return LLMResult(
        text=text,
        provider="scripted",
        model="scripted-1",
        tok_in=6,
        tok_out=6,
        cost_usd=0.0001,
    )


class FakeToolsClient:
    """In-memory tools backend for executor/cache tests."""

    def __init__(
        self,
        tools: list[ToolEntry] | None = None,
        results: dict[str, InvokeResult] | None = None,
    ) -> None:
        self._tools = tools or []
        self._results = results or {}
        self.invocations: list[tuple[str, dict[str, Any], str]] = []
        self.list_calls = 0

    async def list_tools(self) -> list[ToolEntry]:
        self.list_calls += 1
        return list(self._tools)

    async def invoke(
        self,
        name: str,
        *,
        input: dict[str, Any],
        trace_id: str,
        idempotency_key: str | None = None,
        mode: str = "sync",
    ) -> InvokeResult:
        self.invocations.append((name, input, trace_id))
        return self._results.get(
            name, InvokeResult(ok=False, error=InvokeError(code="RUNTIME", message="no result"))
        )


class RecordingChannel:
    """Captures sent replies instead of hitting a network."""

    name = "dev"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, chat_id: str, text: str) -> None:
        self.sent.append((chat_id, text))


class ListRecorder:
    """Collects recorded LLM attempts in memory."""

    def __init__(self) -> None:
        self.records: list[tuple[Attempt, str | None]] = []

    async def record(self, attempt: Attempt, trace_id: str | None) -> None:
        self.records.append((attempt, trace_id))


class FakeEmbedder:
    """Returns a fixed-length constant vector (no real embedding)."""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls = 0

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        return [0.1] * self.dim
