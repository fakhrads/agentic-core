"""Daily autonomy budget: tokens, cost, actions — Redis-backed.

Prinsip 3: every autonomous action has a budget. A loop without a cap is a bug.
Limits default from settings but can be overridden at runtime (`agent budget set`),
persisted in Redis so the override survives restarts. Usage counters are per-UTC-day
and expire automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

_LIMITS_KEY = "agent:budget:limits"
_USAGE_TTL_S = 172_800  # 2 days — yesterday stays inspectable, then self-cleans.


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


@dataclass(slots=True)
class Limits:
    tokens: int
    cost_usd: float
    actions: int


@dataclass(slots=True)
class Usage:
    tokens: int
    cost_usd: float
    actions: int


@dataclass(slots=True)
class BudgetSnapshot:
    day: str
    limits: Limits
    usage: Usage


@dataclass(slots=True)
class Decision:
    allowed: bool
    reason: str = ""


class BudgetExceeded(Exception):
    """Raised when a budgeted operation would exceed a daily limit."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BudgetManager:
    def __init__(
        self,
        redis: Redis[str],
        *,
        default_tokens: int,
        default_cost_usd: float,
        default_actions: int,
    ) -> None:
        self.redis = redis
        self._defaults = Limits(default_tokens, default_cost_usd, default_actions)

    def _usage_key(self, metric: str) -> str:
        return f"agent:budget:{_today()}:{metric}"

    async def get_limits(self) -> Limits:
        raw = await self.redis.hgetall(_LIMITS_KEY)
        return Limits(
            tokens=int(raw.get("tokens", self._defaults.tokens)),
            cost_usd=float(raw.get("cost_usd", self._defaults.cost_usd)),
            actions=int(raw.get("actions", self._defaults.actions)),
        )

    async def set_limits(
        self,
        *,
        tokens: int | None = None,
        cost_usd: float | None = None,
        actions: int | None = None,
    ) -> Limits:
        mapping: dict[str, str] = {}
        if tokens is not None:
            mapping["tokens"] = str(tokens)
        if cost_usd is not None:
            mapping["cost_usd"] = str(cost_usd)
        if actions is not None:
            mapping["actions"] = str(actions)
        if mapping:
            await self.redis.hset(_LIMITS_KEY, mapping=mapping)  # type: ignore[arg-type]
        return await self.get_limits()

    async def get_usage(self) -> Usage:
        tok = await self.redis.get(self._usage_key("tokens"))
        cost = await self.redis.get(self._usage_key("cost_usd"))
        act = await self.redis.get(self._usage_key("actions"))
        return Usage(
            tokens=int(tok or 0),
            cost_usd=float(cost or 0.0),
            actions=int(act or 0),
        )

    async def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            day=_today(),
            limits=await self.get_limits(),
            usage=await self.get_usage(),
        )

    async def check(
        self, *, tokens: int = 0, cost_usd: float = 0.0, actions: int = 0
    ) -> Decision:
        """Would adding this usage breach any limit? First breach wins."""
        limits = await self.get_limits()
        usage = await self.get_usage()
        if usage.tokens + tokens > limits.tokens:
            return Decision(False, f"token budget: {usage.tokens}+{tokens} > {limits.tokens}")
        if usage.cost_usd + cost_usd > limits.cost_usd:
            return Decision(
                False, f"cost budget: {usage.cost_usd:.4f}+{cost_usd:.4f} > {limits.cost_usd}"
            )
        if usage.actions + actions > limits.actions:
            return Decision(
                False, f"action budget: {usage.actions}+{actions} > {limits.actions}"
            )
        return Decision(True)

    async def _incr(self, metric: str, amount: float, *, is_float: bool) -> None:
        key = self._usage_key(metric)
        if is_float:
            await self.redis.incrbyfloat(key, amount)
        else:
            await self.redis.incrby(key, int(amount))
        await self.redis.expire(key, _USAGE_TTL_S)

    async def record_llm(self, tok_in: int, tok_out: int, cost_usd: float) -> None:
        await self._incr("tokens", tok_in + tok_out, is_float=False)
        if cost_usd:
            await self._incr("cost_usd", cost_usd, is_float=True)

    async def record_action(self, n: int = 1) -> None:
        await self._incr("actions", n, is_float=False)
