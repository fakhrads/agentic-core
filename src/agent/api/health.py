"""HTTP surface: /health and /metrics (Prometheus).

Kept intentionally small — the daemon's real work is the event loop, this is
just liveness + scrape target. Metrics registry is process-global so other
components (loop, curator) register their own gauges/counters against it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

from agent.health import all_ok, run_health_checks

REGISTRY = CollectorRegistry()

# M1 seed metrics; later milestones register token/cost/regression gauges here.
UP = Gauge("agent_up", "1 if the agent process is serving", registry=REGISTRY)
DEP_UP = Gauge(
    "agent_dependency_up",
    "1 if a dependency health check passed",
    labelnames=["dependency"],
    registry=REGISTRY,
)
REGRESSION_PASSED = Gauge(
    "agent_regression_passed", "tasks passed in the latest regression run", registry=REGISTRY
)
REGRESSION_TOTAL = Gauge(
    "agent_regression_total", "tasks in the latest regression run", registry=REGISTRY
)


async def _refresh_regression_gauges() -> None:
    """Best-effort: reflect the latest regression run. Never fail a scrape."""
    try:
        from agent.config import get_settings
        from agent.db.base import session_scope
        from agent.db.repo import list_regression_runs

        async with session_scope(get_settings().postgres_dsn) as session:
            runs = await list_regression_runs(session, suite="regression", limit=1)
        if runs:
            REGRESSION_PASSED.set(runs[0].passed)
            REGRESSION_TOTAL.set(runs[0].total)
    except Exception:  # noqa: BLE001 — metrics must not depend on DB availability
        return


def create_app(daemon: Any | None = None) -> FastAPI:
    """Build the daemon's HTTP surface.

    `daemon` is the live `Daemon` instance when served from `agent up` (needed
    by webhook routes that must call `daemon.ingest()`); it's None for
    ad-hoc/test app construction, in which case webhook POSTs 503.
    """
    app = FastAPI(title="agent-core", version="0.1.0")
    app.state.daemon = daemon
    UP.set(1)

    from agent.config import get_settings

    s = get_settings()
    if s.whatsapp_bridge_secret.get_secret_value():
        from agent.api.whatsapp_webhook import router as whatsapp_router

        app.include_router(whatsapp_router)

    @app.get("/health")
    async def health() -> Response:
        results = await run_health_checks()
        for r in results:
            DEP_UP.labels(dependency=r.name).set(1 if r.ok else 0)
        ok = all_ok(results)
        payload = {
            "ok": ok,
            "checks": [r.as_dict() for r in results],
        }
        import json

        return Response(
            content=json.dumps(payload),
            media_type="application/json",
            status_code=200 if ok else 503,
        )

    @app.get("/metrics")
    async def metrics() -> Response:
        await _refresh_regression_gauges()
        return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/status")
    async def api_status() -> Response:
        """Same data `agent watch` renders, as JSON — foundation for a future
        desktop/web client that polls instead of touching Redis/Postgres
        directly. No auth: matches /health and /metrics, localhost-only by
        default (AGENT_HTTP_HOST)."""
        import json
        from dataclasses import asdict

        import redis.asyncio as redis_asyncio

        from agent.autonomy.budget import BudgetManager
        from agent.bus.streams import EventBus
        from agent.dashboard import gather_snapshot

        settings = get_settings()
        redis = redis_asyncio.from_url(settings.redis_url, decode_responses=True)
        try:
            bus = EventBus(redis)
            budget = BudgetManager(
                redis,
                default_tokens=settings.budget_tokens,
                default_cost_usd=settings.budget_cost_usd,
                default_actions=settings.budget_actions,
            )
            snap = await gather_snapshot(settings.postgres_dsn, redis, bus, budget)
        finally:
            await redis.aclose()  # type: ignore[attr-defined]

        return Response(
            content=json.dumps(asdict(snap), default=str),
            media_type="application/json",
        )

    return app


app = create_app()
