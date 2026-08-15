from agent.config import Settings
from agent.health import CheckResult, _check_whatsapp, _timed, all_ok


async def test_timed_reports_ok_on_success() -> None:
    async def good() -> str:
        return "fine"

    res = await _timed("dep", good())
    assert res.ok is True
    assert res.detail == "fine"
    assert res.latency_ms >= 0


async def test_timed_catches_exception_as_down() -> None:
    async def bad() -> str:
        raise ConnectionError("boom")

    res = await _timed("dep", bad())
    assert res.ok is False
    assert "ConnectionError" in res.detail


def test_all_ok() -> None:
    ok = [CheckResult("a", True, "", 1.0), CheckResult("b", True, "", 1.0)]
    mixed = [CheckResult("a", True, "", 1.0), CheckResult("b", False, "x", 1.0)]
    assert all_ok(ok) is True
    assert all_ok(mixed) is False


async def test_check_whatsapp_reports_not_configured_when_unset() -> None:
    detail = await _check_whatsapp(Settings())
    assert detail == "not configured"
