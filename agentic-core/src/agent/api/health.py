"""HTTP surface: /health and /metrics (Prometheus).

Kept intentionally small — the daemon's real work is the event loop, this is
just liveness + scrape target. Metrics registry is process-global so other
components (loop, curator) register their own gauges/counters against it.
"""

from __future__ import annotations

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


def create_app() -> FastAPI:
    app = FastAPI(title="agent-core", version="0.1.0")
    UP.set(1)

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

    return app


app = create_app()
