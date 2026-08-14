"""Dependency health checks: redis, postgres, deepseek, ollama, tools backend.

Every check is defensive: an unreachable dependency yields a structured
``down`` result, never an exception. Used by ``agent health`` and ``/health``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import asdict, dataclass

import httpx

from agent.config import Settings, get_settings


@dataclass(slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str
    latency_ms: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


async def _timed(name: str, coro: Awaitable[str]) -> CheckResult:
    start = time.perf_counter()
    try:
        detail = await coro
        ok = True
    except Exception as exc:  # noqa: BLE001 — health must never raise
        detail = f"{type(exc).__name__}: {exc}"
        ok = False
    latency = (time.perf_counter() - start) * 1000
    return CheckResult(name=name, ok=ok, detail=detail, latency_ms=round(latency, 1))


async def _check_redis(s: Settings) -> str:
    import redis.asyncio as aioredis

    client = aioredis.from_url(s.redis_url, socket_connect_timeout=3)
    try:
        await client.ping()
        return "ping ok"
    finally:
        await client.aclose()  # type: ignore[attr-defined]  # types-redis lags runtime


async def _check_postgres(s: Settings) -> str:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(s.postgres_dsn, connect_args={"timeout": 3})
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "select 1 ok"
    finally:
        await engine.dispose()


async def _check_deepseek(s: Settings) -> str:
    # Reachability only — models endpoint requires the key.
    headers = {}
    key = s.deepseek_api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=s.deepseek_timeout_s) as client:
        resp = await client.get(f"{s.deepseek_base_url}/models", headers=headers)
        return f"HTTP {resp.status_code}"


async def _check_ollama(s: Settings) -> str:
    async with httpx.AsyncClient(timeout=s.ollama_timeout_s) as client:
        resp = await client.get(f"{s.ollama_base_url}/api/tags")
        return f"HTTP {resp.status_code}"


async def _check_tools(s: Settings) -> str:
    headers = {
        "Authorization": f"Bearer {s.tools_service_token.get_secret_value()}",
        "X-Contract-Version": str(s.contract_version),
    }
    async with httpx.AsyncClient(timeout=s.tools_timeout_s) as client:
        resp = await client.get(f"{s.tools_base_url}/tools", headers=headers)
        return f"HTTP {resp.status_code}"


async def run_health_checks(settings: Settings | None = None) -> list[CheckResult]:
    s = settings or get_settings()
    results = await asyncio.gather(
        _timed("redis", _check_redis(s)),
        _timed("postgres", _check_postgres(s)),
        _timed("deepseek", _check_deepseek(s)),
        _timed("ollama", _check_ollama(s)),
        _timed("tools_backend", _check_tools(s)),
    )
    return list(results)


def all_ok(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)
