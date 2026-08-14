"""LLM abstraction + the mandatory budgeted wrapper.

Every provider call goes through `BudgetedLLM`: it checks the daily budget
BEFORE sending (raises BudgetExceeded if over) and records every attempt —
including failed retries — so cost is never under-reported (spec §3).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from agent.autonomy.budget import BudgetExceeded, BudgetManager
from agent.trace import get_trace_id


@dataclass(slots=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = ""
    # Assistant messages that requested tools echo them back verbatim.
    tool_calls: list[dict[str, Any]] | None = None
    # Tool-role messages answer a specific call.
    tool_call_id: str | None = None
    name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls is not None:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            msg["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            msg["name"] = self.name
        return msg


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class Attempt:
    """One HTTP attempt against a provider (success or failure)."""

    provider: str
    model: str
    tok_in: int
    tok_out: int
    cost_usd: float
    ok: bool
    error: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(slots=True)
class LLMResult:
    text: str
    provider: str
    model: str
    tok_in: int
    tok_out: int
    cost_usd: float
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The raw assistant message dict, to echo back when continuing a tool loop.
    raw_message: dict[str, Any] | None = None


OnAttempt = Callable[[Attempt], Awaitable[None]]


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float = 0.7,
        tools: Sequence[dict[str, Any]] | None = None,
        on_attempt: OnAttempt | None = None,
    ) -> LLMResult: ...


class CostRecorder(Protocol):
    async def record(self, attempt: Attempt, trace_id: str | None) -> None: ...


# USD per 1M tokens (input, output). Estimates — verify against provider pricing.
PRICING: dict[tuple[str, str], tuple[float, float]] = {
    ("deepseek", "deepseek-chat"): (0.27, 1.10),
    ("deepseek", "deepseek-reasoner"): (0.55, 2.19),
}


def price_for(provider: str, model: str) -> tuple[float, float]:
    return PRICING.get((provider, model), (0.0, 0.0))


def compute_cost(provider: str, model: str, tok_in: int, tok_out: int) -> float:
    in_rate, out_rate = price_for(provider, model)
    return (tok_in * in_rate + tok_out * out_rate) / 1_000_000


def estimate_tokens(messages: Sequence[ChatMessage]) -> int:
    """Rough pre-flight estimate (~4 chars/token) for the budget check."""
    chars = sum(len(m.content or "") for m in messages)
    return max(1, chars // 4)


class BudgetedLLM:
    """The only sanctioned way to call an LLM in the agent."""

    def __init__(
        self,
        provider: LLMProvider,
        budget: BudgetManager,
        recorder: CostRecorder,
    ) -> None:
        self.provider = provider
        self.budget = budget
        self.recorder = recorder

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        tools: Sequence[dict[str, Any]] | None = None,
    ) -> LLMResult:
        est_in = estimate_tokens(messages)
        est_cost = compute_cost(
            self.provider.name, self.provider.model, est_in, max_tokens
        )
        decision = await self.budget.check(
            tokens=est_in + max_tokens, cost_usd=est_cost
        )
        if not decision.allowed:
            raise BudgetExceeded(decision.reason)

        async def _on_attempt(attempt: Attempt) -> None:
            await self.recorder.record(attempt, get_trace_id())
            await self.budget.record_llm(attempt.tok_in, attempt.tok_out, attempt.cost_usd)

        return await self.provider.complete(
            messages,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            on_attempt=_on_attempt,
        )


def messages_to_payload(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    return [m.as_dict() for m in messages]
