"""Night shift on sqlite: dry-run makes no writes; real run probes + curates."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select

from agent.autonomy.goals import create_goal
from agent.db.base import Base, dispose_engines, get_engine, session_scope
from agent.db.models import GOAL_ORIGIN_SELF, GSTATUS_ACTIVE, GSTATUS_OPEN, Goal
from agent.jobs.night_shift import NightShift


@pytest.fixture
async def dsn(tmp_path: Path) -> AsyncIterator[str]:
    d = f"sqlite+aiosqlite:///{tmp_path/'ns.db'}"
    engine = get_engine(d)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield d
    await dispose_engines()


async def _prober(_prompt: str) -> str:
    return "some progress\nPARTIAL"


async def test_dry_run_makes_no_writes(dsn: str) -> None:
    async with session_scope(dsn) as session:
        await create_goal(session, text="learn X", origin=GOAL_ORIGIN_SELF)

    shift = NightShift(dsn, _prober)
    report = await shift.run(dry_run=True)

    assert report.dry_run is True
    probe_step = next(s for s in report.steps if s.name == "probe")
    assert probe_step.detail["count"] == 1  # would probe 1 goal
    # But nothing was persisted — the goal is still open.
    async with session_scope(dsn) as session:
        goal = (await session.scalars(select(Goal))).one()
        assert goal.status == GSTATUS_OPEN


async def test_real_run_probes_and_persists(dsn: str) -> None:
    async with session_scope(dsn) as session:
        await create_goal(session, text="learn X", origin=GOAL_ORIGIN_SELF)

    shift = NightShift(dsn, _prober)
    report = await shift.run(dry_run=False)

    assert report.dry_run is False
    names = [s.name for s in report.steps]
    assert names == [
        "probe", "ingest", "distill", "benchmark", "forge", "curate", "resample"
    ]

    # PARTIAL → active, persisted.
    async with session_scope(dsn) as session:
        goal = (await session.scalars(select(Goal))).one()
        assert goal.status == GSTATUS_ACTIVE


async def test_unwired_hooks_are_reported_as_skipped(dsn: str) -> None:
    shift = NightShift(dsn, _prober)
    report = await shift.run(dry_run=True)
    ingest = next(s for s in report.steps if s.name == "ingest")
    assert "skipped" in ingest.detail


class _PausedDrift:
    async def is_paused(self) -> bool:
        return True


async def test_notify_steps_held_under_drift_pause(dsn: str) -> None:
    called = {"distill": False}

    async def distill_hook(session: object, dry_run: bool) -> dict[str, object]:
        called["distill"] = True
        return {"ran": True}

    shift = NightShift(
        dsn, _prober, distill_hook=distill_hook, drift_state=_PausedDrift()  # type: ignore[arg-type]
    )
    report = await shift.run(dry_run=False)

    distill = next(s for s in report.steps if s.name == "distill")
    assert "drift-pause" in distill.detail["skipped"]
    assert called["distill"] is False  # NOTIFY step was held
    # AUTO steps still ran.
    assert any(s.name == "probe" for s in report.steps)
