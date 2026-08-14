"""Regression persistence + history-driven drift on sqlite."""

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base, utcnow
from agent.db.repo import list_change_events_since, record_change_event
from agent.evolution.regression import (
    RegressionTask,
    detect_drift,
    persist_run,
    previous_result_maps,
    run_suite,
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


_TASKS = [
    RegressionTask(id="a", prompt="p", expected="1", checker="exact"),
    RegressionTask(id="b", prompt="p", expected="1", checker="exact"),
]


async def _record(session: AsyncSession, answer_ok: dict[str, bool]) -> None:
    """Record a run where the solver always answers '1'; a task fails when its
    expected value is set to something else."""

    async def solver(_prompt: str) -> str:
        return "1"

    tasks = [
        RegressionTask(
            id=tid, prompt="p", expected=("1" if ok else "999"), checker="exact"
        )
        for tid, ok in answer_ok.items()
    ]
    result = await run_suite(solver, tasks)
    prev = await previous_result_maps(session, "regression")
    verdict = detect_drift(result.as_map(), prev)
    await persist_run(session, result, verdict)
    await session.commit()


async def test_history_and_drift_pause_persist(session: AsyncSession) -> None:
    # Baseline: both pass.
    await _record(session, {"a": True, "b": True})
    prev = await previous_result_maps(session, "regression")
    assert prev[0] == {"a": True, "b": True}

    # Next run: both fail → dropped 2 → should_pause recorded in detail.
    await _record(session, {"a": False, "b": False})
    runs = await previous_result_maps(session, "regression")
    assert runs[0] == {"a": False, "b": False}

    from agent.db.repo import list_regression_runs

    all_runs = await list_regression_runs(session, suite="regression", limit=10)
    latest = all_runs[0]
    assert latest.passed == 0
    assert latest.detail["should_pause"] is True
    assert set(latest.detail["newly_failing"]) == {"a", "b"}


async def test_change_event_recording(session: AsyncSession) -> None:
    await record_change_event(session, kind="playbook", ref_id="rev-1")
    await record_change_event(session, kind="skill", ref_id="csv_diff")
    await session.commit()

    since = utcnow() - timedelta(hours=1)
    events = await list_change_events_since(session, since)
    assert {e.kind for e in events} == {"playbook", "skill"}
