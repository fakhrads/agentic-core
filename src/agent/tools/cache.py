"""Tool registry cache.

Pulls `GET /tools` at startup and refreshes every 5 minutes, or immediately on a
Redis pub/sub `tools:changed` message (real-time). Disabled tools are never
exposed to the LLM; probation tools are exposed but flagged so their output is
not promoted to fact (spec §2.1).
"""

from __future__ import annotations

from typing import Any, Protocol

from redis.asyncio import Redis

from agent.logging import get_logger
from agent.tools.client import ToolClientError
from agent.tools.models import STATUS_DISABLED, ToolEntry

log = get_logger("tools.cache")

CHANNEL_TOOLS_CHANGED = "tools:changed"


class _Lister(Protocol):
    async def list_tools(self) -> list[ToolEntry]: ...


class ToolCache:
    def __init__(
        self,
        client: _Lister,
        redis: Redis[str] | None = None,
        *,
        refresh_interval_s: float = 300.0,
    ) -> None:
        self._client = client
        self._redis = redis
        self._interval = refresh_interval_s
        self._by_name: dict[str, ToolEntry] = {}
        self._stop = False
        self.refreshes = 0  # observability / test hook

    async def refresh(self) -> None:
        tools = await self._client.list_tools()
        self._by_name = {t.name: t for t in tools}
        self.refreshes += 1
        log.info("tools_refreshed", count=len(self._by_name))

    def all_tools(self) -> list[ToolEntry]:
        return list(self._by_name.values())

    def usable(self) -> list[ToolEntry]:
        """Active + probation (never disabled) — what the LLM may see."""
        return [t for t in self._by_name.values() if t.status != STATUS_DISABLED]

    def get(self, name: str) -> ToolEntry | None:
        return self._by_name.get(name)

    def is_probation(self, name: str) -> bool:
        entry = self._by_name.get(name)
        return entry is not None and entry.is_probation

    def function_defs(self) -> list[dict[str, Any]]:
        return [t.to_function_def() for t in self.usable()]

    def stop(self) -> None:
        self._stop = True

    async def _safe_refresh(self) -> None:
        # The tools backend is a soft dependency (spec: agent boots without it,
        # calls just fail at use-time) — a refresh failure must never take down
        # the daemon's other tasks via asyncio.gather. Keep whatever cache we
        # last had (possibly empty) and retry on the next interval/signal.
        try:
            await self.refresh()
        except ToolClientError as exc:
            log.warning("tools_refresh_failed", error=str(exc))

    async def run(self) -> None:
        """Background loop: refresh on interval, or immediately on pub/sub signal.

        Polls pub/sub in short (<=1s) slices rather than blocking for the full
        `refresh_interval_s` — otherwise `stop()` wouldn't be noticed until the
        in-flight `get_message` call times out (up to 5 minutes at the default
        interval), making `agent up` feel hung on shutdown.
        """
        await self._safe_refresh()
        if self._redis is None:
            return
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL_TOOLS_CHANGED)
        log.info("tools_cache_watching", channel=CHANNEL_TOOLS_CHANGED)
        poll_s = min(self._interval, 1.0)
        elapsed = 0.0
        try:
            while not self._stop:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=poll_s)
                if self._stop:
                    break
                if msg is not None:
                    log.info("tools_changed_signal")
                    await self._safe_refresh()
                    elapsed = 0.0
                    continue
                elapsed += poll_s
                if elapsed >= self._interval:
                    await self._safe_refresh()
                    elapsed = 0.0
        finally:
            await pubsub.unsubscribe(CHANNEL_TOOLS_CHANGED)
            await pubsub.aclose()  # type: ignore[attr-defined]
        log.info("tools_cache_stopped")
