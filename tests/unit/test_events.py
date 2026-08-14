from agent.bus.events import Event, EventType


def test_event_roundtrip_via_fields() -> None:
    ev = Event(
        type=EventType.TOOL_CALL,
        trace_id="t-1",
        episode_id="42",
        component="loop",
        message="called regex_explainer",
        payload={"tool": "regex_explainer", "ms": 812},
    )
    fields = ev.to_fields()
    assert set(fields) == {"data"}
    back = Event.from_fields(fields)
    assert back.trace_id == "t-1"
    assert back.payload["tool"] == "regex_explainer"
    assert back.event_id == ev.event_id


def test_matches_trace_filter() -> None:
    ev = Event(type="note", trace_id="abc")
    assert ev.matches("abc", None) is True
    assert ev.matches("xyz", None) is False
    assert ev.matches(None, None) is True


def test_matches_grep_is_case_insensitive() -> None:
    ev = Event(type="tool_call", message="Regex Explainer done")
    assert ev.matches(None, "regex") is True
    assert ev.matches(None, "REGEX") is True
    assert ev.matches(None, "missing") is False
