"""Minimal periodic scheduler.

M7 needs the curator to run on an interval; the night shift (M8) reuses this.
Kept tiny and cancellable so graceful shutdown just stops the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from agent.logging import get_logger

log = get_logger("scheduler")

Job = Callable[[], Awaitable[None]]


async def run_periodic(
    interval_s: float,
    job: Job,
    *,
    name: str,
    stop: asyncio.Event,
    run_immediately: bool = False,
) -> None:
    """Run `job` every `interval_s` until `stop` is set."""
    if run_immediately:
        await _safe_run(job, name)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            await _safe_run(job, name)
    log.info("scheduler_stopped", job=name)


async def _safe_run(job: Job, name: str) -> None:
    try:
        await job()
    except Exception as exc:  # noqa: BLE001 — a job failure must not kill the loop
        log.error("scheduled_job_failed", job=name, error=str(exc))
