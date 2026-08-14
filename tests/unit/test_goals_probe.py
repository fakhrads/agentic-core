from agent.autonomy.goals import classify_probe
from agent.db.models import GSTATUS_ACTIVE, GSTATUS_DONE, GSTATUS_INFEASIBLE


def test_classify_solved() -> None:
    pc = classify_probe("I computed 2+2=4.\nSOLVED")
    assert pc.marker == "SOLVED"
    assert pc.status == GSTATUS_DONE


def test_classify_partial() -> None:
    pc = classify_probe("I got part of it.\nPARTIAL")
    assert pc.marker == "PARTIAL"
    assert pc.status == GSTATUS_ACTIVE


def test_classify_stuck() -> None:
    pc = classify_probe("No idea.\nSTUCK")
    assert pc.status == GSTATUS_INFEASIBLE


def test_classify_defaults_to_stuck_when_no_marker() -> None:
    pc = classify_probe("some rambling answer with no marker line")
    assert pc.marker == "STUCK"
    assert pc.status == GSTATUS_INFEASIBLE


def test_classify_uses_last_marker_line() -> None:
    # Reasoning may mention words; the final marker line wins.
    pc = classify_probe("Maybe PARTIAL earlier.\nActually, SOLVED")
    assert pc.marker == "SOLVED"
