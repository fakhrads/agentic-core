"""`agent doctor` — find configuration problems and fix them in place.

Exists because the failures it targets are silent-but-fatal and used to
require hand-editing `.env`: the daemon starts, logs look almost normal, and
nothing is ever processed. Everything here is a *capability* probe (does the
thing the daemon actually relies on work?), never a reachability ping.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import typer

from agent.cli._envfile import read_env, write_env
from agent.cli._output import console, err_console
from agent.config import get_settings, reset_settings_cache

doctor_app = typer.Typer(help="Diagnose and repair configuration problems.")

_ENV_PATH = Path(".env")

#: Ports docker-compose publishes, in preference order. The defaults avoid the
#: stock ports precisely because a native Redis/Postgres often squats there.
_REDIS_CANDIDATE_PORTS = (6380, 6379)

_PROBE_STREAM = "events:doctor"
_PROBE_GROUP = "doctor"


@dataclass(slots=True)
class Finding:
    ok: bool
    label: str
    detail: str
    fixed: str | None = None


async def _redis_usable(url: str, timeout_s: float = 4.0) -> tuple[bool, str]:
    """Can we actually run the daemon's blocking stream read against this URL?

    PING is deliberately not enough: some Redis builds answer PING instantly
    yet never return from a blocking XREADGROUP, which wedges the event loop
    while every surface-level check still reports green.
    """
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        url, socket_connect_timeout=2, socket_timeout=timeout_s, decode_responses=True
    )
    try:
        await client.ping()
        try:
            await client.xgroup_create(_PROBE_STREAM, _PROBE_GROUP, id="0", mkstream=True)
        except Exception:  # noqa: BLE001 — BUSYGROUP means it already exists
            pass
        await client.xreadgroup(_PROBE_GROUP, "doctor", {_PROBE_STREAM: ">"}, count=1, block=100)
        return True, "blocking read ok"
    except Exception as exc:  # noqa: BLE001 — a probe reports, never raises
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await client.aclose()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass


def redis_usable_sync(url: str) -> tuple[bool, str]:
    """Blocking wrapper around the probe, for CLI preflight checks."""
    return asyncio.run(_redis_usable(url))


def _with_port(url: str, port: int) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    netloc = f"{host}:{port}"
    if parsed.username:
        netloc = f"{parsed.username}:{parsed.password}@{netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def _compose_dir() -> Path | None:
    """The checkout holding docker-compose.yml (may differ from the cwd)."""
    if Path("docker-compose.yml").exists():
        return Path.cwd()
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").exists():
            return parent
    return None


def _compose_up() -> bool:
    """Bring the dev dependencies up so their published ports match the
    current compose file. Returns True if compose ran successfully."""
    if shutil.which("docker") is None:
        return False
    directory = _compose_dir()
    if directory is None:
        return False
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"], cwd=directory, check=True, capture_output=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return True


async def _find_working_redis(url: str) -> str | None:
    for port in _REDIS_CANDIDATE_PORTS:
        candidate = _with_port(url, port)
        if candidate == url:
            continue
        ok, _ = await _redis_usable(candidate)
        if ok:
            return candidate
    return None


async def _diagnose_redis(fix: bool) -> Finding:
    url = get_settings().redis_url
    ok, detail = await _redis_usable(url)
    if ok:
        return Finding(True, "redis", f"{url} — {detail}")

    working = await _find_working_redis(url)

    # Nothing usable yet. An out-of-date container is the common cause (the
    # compose file may now publish a different port than the running one), so
    # try bringing compose up before giving up.
    if working is None and fix and _compose_up():
        await asyncio.sleep(2)  # let redis finish starting
        ok, detail_retry = await _redis_usable(url)
        if ok:
            return Finding(
                False,
                "redis",
                "containers were out of date",
                fixed="ran docker compose up -d",
            )
        detail = detail_retry
        working = await _find_working_redis(url)

    if working is not None:
        if not fix:
            return Finding(
                False,
                "redis",
                f"{url} unusable ({detail}). {working} works — re-run with --fix to switch.",
            )
        write_env(_ENV_PATH, {"AGENT_REDIS_URL": working})
        reset_settings_cache()
        return Finding(
            False,
            "redis",
            f"{url} unusable ({detail})",
            fixed=f"switched AGENT_REDIS_URL to {working}",
        )

    return Finding(
        False,
        "redis",
        f"{url} unusable ({detail}) and no working alternative found. "
        "Start the dependencies with `docker compose up -d`, and check "
        "`lsof -nP -iTCP:6379 -sTCP:LISTEN` for a native Redis on the port.",
    )


def _diagnose_whatsapp_bridge() -> Finding | None:
    """The bridge reads the agent's .env directly, so a stale bridge/.env with
    its own BRIDGE_SECRET is the one way the two can still disagree (-> 403)."""
    agent_secret = get_settings().whatsapp_bridge_secret.get_secret_value()
    if not agent_secret:
        return None

    bridge_env = Path("whatsapp-bridge/.env")
    if not bridge_env.exists():
        return Finding(True, "whatsapp bridge", "secret inherited from .env")

    bridge_secret = read_env(bridge_env).get("BRIDGE_SECRET", "")
    if not bridge_secret:
        return Finding(True, "whatsapp bridge", "secret inherited from .env")
    if bridge_secret == agent_secret:
        return Finding(True, "whatsapp bridge", "secrets match")
    return Finding(
        False,
        "whatsapp bridge",
        "whatsapp-bridge/.env sets a different BRIDGE_SECRET — inbound messages "
        "will be rejected with 403. Remove that line to inherit the agent's, or "
        "make the two match.",
    )


async def _run(fix: bool) -> list[Finding]:
    findings: list[Finding] = [await _diagnose_redis(fix)]
    wa = _diagnose_whatsapp_bridge()
    if wa is not None:
        findings.append(wa)
    return findings


def repair_config() -> bool:
    """Run the checks with repairs enabled, reporting only what mattered.

    Used by `agent update` so a config that a new default made stale is fixed
    as part of updating, rather than surfacing later as a silent daemon.
    Returns True if everything is healthy afterwards.
    """
    findings = asyncio.run(_run(fix=True))
    healthy = True
    for f in findings:
        if f.fixed:
            console.print(f"  [green]fixed[/] {f.label} — {f.fixed}")
        elif not f.ok:
            err_console.print(f"  [yellow]![/] {f.label}: {f.detail}")
            healthy = False
    if healthy and not any(f.fixed for f in findings):
        console.print("  [dim]no changes needed.[/]")
    return healthy


@doctor_app.callback(invoke_without_command=True)
def doctor(
    ctx: typer.Context,
    fix: bool = typer.Option(False, "--fix", help="Apply repairs, don't just report."),
) -> None:
    """Check the config the daemon depends on, and optionally repair it."""
    if ctx.invoked_subcommand is not None:
        return

    findings = asyncio.run(_run(fix))
    for f in findings:
        if f.fixed:
            console.print(f"[yellow]![/] {f.label}: {f.detail}")
            console.print(f"  [green]fixed[/] — {f.fixed}")
        elif f.ok:
            console.print(f"[green]✓[/] {f.label}: {f.detail}")
        else:
            err_console.print(f"[red]✗[/] {f.label}: {f.detail}")

    unresolved = [f for f in findings if not f.ok and not f.fixed]
    if unresolved:
        if not fix and any(f for f in findings if not f.ok):
            console.print("\n[dim]Run [cyan]agent doctor --fix[/] to apply repairs.[/]")
        raise typer.Exit(code=1)
    console.print("\n[green]Configuration looks good.[/]")
