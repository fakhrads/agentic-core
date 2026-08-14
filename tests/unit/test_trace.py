from agent.trace import (
    get_episode_id,
    get_trace_id,
    new_trace_id,
    trace_context,
)


def test_new_trace_id_is_unique() -> None:
    assert new_trace_id() != new_trace_id()


def test_trace_context_binds_and_restores() -> None:
    assert get_trace_id() is None
    with trace_context(trace_id="t-1", episode_id="e-1") as tid:
        assert tid == "t-1"
        assert get_trace_id() == "t-1"
        assert get_episode_id() == "e-1"
    # Restored after exit.
    assert get_trace_id() is None
    assert get_episode_id() is None


def test_nested_trace_context_does_not_leak() -> None:
    with trace_context(trace_id="outer"):
        with trace_context(trace_id="inner"):
            assert get_trace_id() == "inner"
        assert get_trace_id() == "outer"


def test_trace_context_autogenerates_id() -> None:
    with trace_context() as tid:
        assert tid
        assert get_trace_id() == tid
