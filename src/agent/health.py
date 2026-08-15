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


_HEALTH_STREAM = "events:healthcheck"
_HEALTH_GROUP = "healthcheck"


async def _check_redis(s: Settings) -> str:
    """PING plus a short blocking XREADGROUP.

    PING alone is not enough: the daemon's event loop lives on blocking
    XREADGROUP, and a server can answer PING fine while never returning from a
    blocking stream read (observed with some non-Docker Redis builds squatting
    on the default port). That combination made `agent health` report "ok"
    while the agent silently consumed nothing.
    """
    import redis.asyncio as aioredis

    client = aioredis.from_url(s.redis_url, socket_connect_timeout=3, socket_timeout=5)
    try:
        await client.ping()
        try:
            await client.xgroup_create(_HEALTH_STREAM, _HEALTH_GROUP, id="0", mkstream=True)
        except Exception:  # noqa: BLE001 — BUSYGROUP just means it already exists
            pass
        await client.xreadgroup(
            _HEALTH_GROUP, "healthcheck", {_HEALTH_STREAM: ">"}, count=1, block=100
        )
        return "ping + blocking read ok"
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
    """Reachable *and* has the configured models pulled.

    Reachability alone is misleading: `/api/tags` answers 200 on a fresh
    Ollama with nothing installed, while every embed/probe call then 404s at
    runtime (memory retrieval silently degrades). Name the missing models so
    the fix is obvious — `ollama pull <model>`.
    """
    async with httpx.AsyncClient(timeout=s.ollama_timeout_s) as client:
        resp = await client.get(f"{s.ollama_base_url}/api/tags")
        resp.raise_for_status()
        installed = {m.get("name", "") for m in resp.json().get("models", [])}

    # Ollama reports "name:tag"; a bare configured name means the default tag.
    def _present(model: str) -> bool:
        return model in installed or f"{model}:latest" in installed

    missing = [m for m in (s.ollama_embed_model, s.ollama_probe_model) if not _present(m)]
    if missing:
        raise RuntimeError(f"model not pulled: {', '.join(missing)} — run `ollama pull <model>`")
    return f"HTTP {resp.status_code}, models ok"


async def _check_whatsapp(s: Settings) -> str:
    secret = s.whatsapp_bridge_secret.get_secret_value()
    if not secret:
        return "not configured"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(f"{s.whatsapp_bridge_url.rstrip('/')}/health")
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
        _timed("whatsapp", _check_whatsapp(s)),
    )
    return list(results)


def all_ok(results: list[CheckResult]) -> bool:
    return all(r.ok for r in results)
