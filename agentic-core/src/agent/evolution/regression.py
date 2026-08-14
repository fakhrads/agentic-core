"""Regression harness (spec §7).

Runs a fixed, human-written suite through a solver and records the pass/fail
history. Drift detection compares the latest run against prior runs to catch
misevolution before it compounds. Pure logic (loading, checking, drift) is
separated from I/O so it is testable without an LLM or a database.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from agent.bus.events import STREAM_AUDIT, Event
from agent.db.models import RegressionRun
from agent.db.repo import list_regression_runs, record_regression_run

if TYPE_CHECKING:
    from agent.bus.streams import EventBus
    from agent.evolution.drift import DriftState

# A solver turns a prompt into an answer. Production wires the agent's LLM;
# tests inject a deterministic fake.
Solver = Callable[[str], Awaitable[str]]

DEFAULT_SUITE_DIR = Path("benchmarks/regression")

# On a drop of this many tasks (or any regressed 3x-streak) → drift-pause.
PAUSE_ON_DROP = 2


@dataclass(slots=True)
class RegressionTask:
    id: str
    prompt: str
    expected: str
    checker: str  # "exact" | "regex"


@dataclass(slots=True)
class TaskResult:
    id: str
    passed: bool
    expected: str
    actual: str


@dataclass(slots=True)
class SuiteResult:
    suite: str
    results: list[TaskResult]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def as_map(self) -> dict[str, bool]:
        return {r.id: r.passed for r in self.results}


def check_answer(checker: str, expected: str, actual: str) -> bool:
    answer = actual.strip()
    if checker == "exact":
        return answer == expected
    if checker == "regex":
        return re.search(expected, answer) is not None
    if checker == "pytest":
        raise NotImplementedError("pytest checker is used for skill benchmarks (M9)")
    raise ValueError(f"unknown checker: {checker}")


def load_suite(suite_dir: Path = DEFAULT_SUITE_DIR) -> list[RegressionTask]:
    tasks: list[RegressionTask] = []
    for path in sorted(suite_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            tasks.append(
                RegressionTask(
                    id=data["id"],
                    prompt=data["prompt"],
                    expected=data["expected"],
                    checker=data["checker"],
                )
            )
    return tasks


async def run_suite(
    solver: Solver, tasks: list[RegressionTask], *, suite: str = "regression"
) -> SuiteResult:
    results: list[TaskResult] = []
    for task in tasks:
        try:
            actual = await solver(task.prompt)
        except Exception as exc:  # noqa: BLE001 — a solver failure is a task failure
            actual = f"<error: {exc}>"
        passed = check_answer(task.checker, task.expected, actual)
        results.append(
            TaskResult(id=task.id, passed=passed, expected=task.expected, actual=actual)
        )
    return SuiteResult(suite=suite, results=results)


@dataclass(slots=True)
class DriftVerdict:
    dropped: int
    newly_failing: list[str] = field(default_factory=list)
    regressed_streak: list[str] = field(default_factory=list)
    should_pause: bool = False
    note: str = ""


def detect_drift(
    current: dict[str, bool], previous_runs: list[dict[str, bool]]
) -> DriftVerdict:
    """Compare the latest run to prior runs (most-recent-first).

    - dropped: tasks that passed in the immediately prior run but fail now.
    - regressed_streak: tasks that passed the last 3 runs in a row, now failing.
    - should_pause: dropped >= 2, OR any regressed_streak task.
    """
    if not previous_runs:
        return DriftVerdict(dropped=0, note="no prior run; baseline recorded")

    prior = previous_runs[0]
    newly_failing = [
        tid
        for tid, ok_now in current.items()
        if not ok_now and prior.get(tid, False)
    ]

    regressed_streak: list[str] = []
    last3 = previous_runs[:3]
    if len(last3) == 3:
        for tid, ok_now in current.items():
            if not ok_now and all(run.get(tid, False) for run in last3):
                regressed_streak.append(tid)

    dropped = len(newly_failing)
    should_pause = dropped >= PAUSE_ON_DROP or len(regressed_streak) > 0
    note = ""
    if dropped == 1 and not should_pause:
        note = "1 task dropped — recorded, not paused"
    elif should_pause:
        note = "drift-pause: NOTIFY/APPROVE held"
    return DriftVerdict(
        dropped=dropped,
        newly_failing=newly_failing,
        regressed_streak=regressed_streak,
        should_pause=should_pause,
        note=note,
    )


async def persist_run(
    session: AsyncSession, result: SuiteResult, verdict: DriftVerdict
) -> RegressionRun:
    detail = {
        "results": result.as_map(),
        "newly_failing": verdict.newly_failing,
        "regressed_streak": verdict.regressed_streak,
        "dropped": verdict.dropped,
        "should_pause": verdict.should_pause,
    }
    return await record_regression_run(
        session,
        suite=result.suite,
        passed=result.passed,
        total=result.total,
        detail=detail,
    )


async def previous_result_maps(
    session: AsyncSession, suite: str, *, limit: int = 5
) -> list[dict[str, bool]]:
    runs = await list_regression_runs(session, suite=suite, limit=limit)
    maps: list[dict[str, bool]] = []
    for run in runs:
        results = run.detail.get("results", {}) if run.detail else {}
        maps.append({k: bool(v) for k, v in results.items()})
    return maps


async def execute_regression(
    session: AsyncSession,
    solver: Solver,
    *,
    suite: str = "regression",
    drift_state: DriftState | None = None,
    bus: EventBus | None = None,
    suite_dir: Path = DEFAULT_SUITE_DIR,
) -> tuple[SuiteResult, DriftVerdict]:
    """Run + persist + detect drift + (optionally) engage drift-pause.

    Single entry point shared by the CLI and the post-playbook-change trigger.
    The caller owns the session transaction (commit on success).
    """
    tasks = load_suite(suite_dir)
    result = await run_suite(solver, tasks, suite=suite)
    prev = await previous_result_maps(session, suite)
    verdict = detect_drift(result.as_map(), prev)
    await persist_run(session, result, verdict)

    if verdict.should_pause and drift_state is not None:
        await drift_state.set_paused(f"regression drop: {verdict.newly_failing}")
        if bus is not None:
            await bus.publish(
                STREAM_AUDIT,
                Event(
                    type="drift.pause",
                    component="regression",
                    message=verdict.note,
                    payload={
                        "newly_failing": verdict.newly_failing,
                        "regressed_streak": verdict.regressed_streak,
                    },
                ),
            )
    return result, verdict
