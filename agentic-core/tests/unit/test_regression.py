from pathlib import Path

import pytest

from agent.evolution.regression import (
    RegressionTask,
    check_answer,
    detect_drift,
    load_suite,
    run_suite,
)


def test_check_answer_exact_and_regex() -> None:
    assert check_answer("exact", "391", " 391 ") is True
    assert check_answer("exact", "391", "392") is False
    assert check_answer("regex", "^yes$", "yes") is True
    assert check_answer("regex", "^yes$", "yes!") is False
    with pytest.raises(NotImplementedError):
        check_answer("pytest", "", "")
    with pytest.raises(ValueError):
        check_answer("bogus", "", "")


def test_shipped_suite_loads_20_tasks() -> None:
    tasks = load_suite(Path("benchmarks/regression"))
    assert len(tasks) == 20
    ids = {t.id for t in tasks}
    assert "arith_mul" in ids and "leap_feb" in ids
    # Every task uses a supported checker.
    assert all(t.checker in {"exact", "regex"} for t in tasks)


async def test_run_suite_scores_with_fake_solver() -> None:
    tasks = [
        RegressionTask(id="a", prompt="p", expected="1", checker="exact"),
        RegressionTask(id="b", prompt="p", expected="2", checker="exact"),
    ]

    async def solver(prompt: str) -> str:
        return "1"  # only 'a' passes

    result = await run_suite(solver, tasks)
    assert result.passed == 1
    assert result.total == 2
    assert result.as_map() == {"a": True, "b": False}


async def test_run_suite_treats_solver_error_as_failure() -> None:
    tasks = [RegressionTask(id="a", prompt="p", expected="1", checker="exact")]

    async def solver(prompt: str) -> str:
        raise RuntimeError("boom")

    result = await run_suite(solver, tasks)
    assert result.passed == 0


def test_detect_drift_baseline() -> None:
    v = detect_drift({"a": True, "b": True}, previous_runs=[])
    assert v.dropped == 0 and v.should_pause is False


def test_detect_drift_single_drop_records_not_pause() -> None:
    v = detect_drift({"a": False, "b": True}, previous_runs=[{"a": True, "b": True}])
    assert v.dropped == 1
    assert v.newly_failing == ["a"]
    assert v.should_pause is False


def test_detect_drift_two_drops_pauses() -> None:
    v = detect_drift(
        {"a": False, "b": False, "c": True},
        previous_runs=[{"a": True, "b": True, "c": True}],
    )
    assert v.dropped == 2
    assert v.should_pause is True


def test_detect_drift_streak_regression_pauses() -> None:
    # 'a' passed the last 3 runs and now fails → pause even though only 1 drop.
    v = detect_drift(
        {"a": False},
        previous_runs=[{"a": True}, {"a": True}, {"a": True}],
    )
    assert v.regressed_streak == ["a"]
    assert v.should_pause is True
