"""Regression: config drift used to require hand-editing .env.

A stale AGENT_REDIS_URL pointing at an unusable Redis produced a daemon that
started, logged a warning every second, and processed nothing — with the only
remedy being "go edit .env yourself".
"""

from pathlib import Path

import pytest

from agent.cli import doctor
from agent.cli._envfile import read_env
from agent.cli.doctor import Finding, _diagnose_whatsapp_bridge, _with_port
from agent.config import Settings


def _settings_on(port: int):
    """get_settings() replacement pinning the configured Redis port, so these
    tests don't depend on whatever the current default happens to be."""
    return lambda: Settings(redis_url=f"redis://localhost:{port}/0")


def test_with_port_preserves_scheme_host_and_db() -> None:
    assert _with_port("redis://localhost:6379/0", 6380) == "redis://localhost:6380/0"


def test_with_port_preserves_credentials() -> None:
    assert (
        _with_port("redis://user:pw@example.com:6379/2", 6380)
        == "redis://user:pw@example.com:6380/2"
    )


async def test_diagnose_redis_reports_ok_when_configured_url_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def usable(url: str, timeout_s: float = 4.0) -> tuple[bool, str]:
        return True, "blocking read ok"

    monkeypatch.setattr(doctor, "_redis_usable", usable)
    finding = await doctor._diagnose_redis(fix=False)
    assert finding.ok
    assert finding.fixed is None


async def test_diagnose_redis_finds_alternative_but_only_writes_with_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def usable(url: str, timeout_s: float = 4.0) -> tuple[bool, str]:
        return ("6380" in url), ("ok" if "6380" in url else "TimeoutError: Timeout reading")

    monkeypatch.setattr(doctor, "_redis_usable", usable)
    monkeypatch.setattr(doctor, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(doctor, "get_settings", _settings_on(6379))

    # Without --fix: reports the working alternative, changes nothing.
    finding = await doctor._diagnose_redis(fix=False)
    assert not finding.ok and finding.fixed is None
    assert "6380" in finding.detail
    assert not (tmp_path / ".env").exists()


async def test_diagnose_redis_rewrites_env_with_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def usable(url: str, timeout_s: float = 4.0) -> tuple[bool, str]:
        return ("6380" in url), ("ok" if "6380" in url else "TimeoutError: Timeout reading")

    env = tmp_path / ".env"
    env.write_text("# keep me\nAGENT_REDIS_URL=redis://localhost:6379/0\nOTHER=untouched\n")
    monkeypatch.setattr(doctor, "_redis_usable", usable)
    monkeypatch.setattr(doctor, "_ENV_PATH", env)
    monkeypatch.setattr(doctor, "get_settings", _settings_on(6379))

    finding = await doctor._diagnose_redis(fix=True)
    assert finding.fixed is not None
    written = read_env(env)
    assert written["AGENT_REDIS_URL"] == "redis://localhost:6380/0"
    assert written["OTHER"] == "untouched"  # unrelated keys survive
    assert "# keep me" in env.read_text()


async def test_diagnose_redis_tries_compose_before_giving_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Nothing works until compose runs — the common "container still publishes
    # the old port" case, which the user shouldn't have to diagnose by hand.
    state = {"compose_ran": False}

    async def usable(url: str, timeout_s: float = 4.0) -> tuple[bool, str]:
        if not state["compose_ran"]:
            return False, "ConnectionError: refused"
        return ("6380" in url), "ok"

    def compose_up() -> bool:
        state["compose_ran"] = True
        return True

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(doctor, "_redis_usable", usable)
    monkeypatch.setattr(doctor, "_compose_up", compose_up)
    monkeypatch.setattr(doctor.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(doctor, "_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(doctor, "get_settings", _settings_on(6379))

    finding = await doctor._diagnose_redis(fix=True)
    assert state["compose_ran"]
    assert finding.fixed is not None


def test_whatsapp_check_skipped_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "get_settings", lambda: Settings())
    assert _diagnose_whatsapp_bridge() is None


def test_whatsapp_ok_when_bridge_env_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The bridge reads the agent's .env directly, so "no bridge/.env" is the
    # healthy default, not a problem.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor, "get_settings", lambda: Settings(whatsapp_bridge_secret="s3cret"))
    finding = _diagnose_whatsapp_bridge()
    assert finding is not None and finding.ok


def test_whatsapp_flags_conflicting_bridge_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "whatsapp-bridge").mkdir()
    (tmp_path / "whatsapp-bridge" / ".env").write_text("BRIDGE_SECRET=different\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(doctor, "get_settings", lambda: Settings(whatsapp_bridge_secret="s3cret"))

    finding = _diagnose_whatsapp_bridge()
    assert finding is not None and not finding.ok
    assert "403" in finding.detail  # names the symptom the user actually sees


def test_finding_defaults_to_unfixed() -> None:
    assert Finding(True, "x", "y").fixed is None
