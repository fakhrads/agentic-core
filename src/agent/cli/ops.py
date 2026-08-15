"""Ops commands: up / down / health / config show."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

import typer
from rich.table import Table

from agent.cli._output import console, emit_json, err_console
from agent.config import get_settings
from agent.health import run_health_checks

ops_app = typer.Typer(help="Operate the daemon: up, down, health, config.")

_PIDFILE = Path(os.environ.get("AGENT_PIDFILE", "./.agent.pid"))


def _serve() -> None:
    """Run the full daemon (health server + loop consumer + channels).

    A first Ctrl-C triggers the daemon's own graceful shutdown (see
    `Daemon.run`'s signal handler); this only exists to catch what a forced
    second Ctrl-C (or any other shutdown-time hiccup) kicks loose, so it
    prints one clean line instead of a wall of tracebacks.
    """
    from agent.daemon import run_daemon

    try:
        asyncio.run(run_daemon())
    except KeyboardInterrupt:
        err_console.print("\n[dim]agent stopped[/]")
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]agent stopped with an error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None


@ops_app.command("up")
def up(
    detach: bool = typer.Option(False, "--detach", help="Run in the background."),
) -> None:
    """Start the daemon (health server + loop consumer + channels)."""
    if _PIDFILE.exists():
        err_console.print(f"[yellow]Already running? pidfile exists at {_PIDFILE}[/]")
        raise typer.Exit(code=1)

    s = get_settings()
    if detach:
        # Spawn the server entrypoint directly — going back through `up` would
        # trip the pidfile guard the parent just created.
        proc = subprocess.Popen(
            [sys.executable, "-c", "from agent.cli.ops import _serve; _serve()"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _PIDFILE.write_text(str(proc.pid))
        console.print(f"[green]Started detached[/] pid={proc.pid} "
                      f"→ http://{s.http_host}:{s.http_port}")
        return

    _PIDFILE.write_text(str(os.getpid()))
    try:
        _serve()
    finally:
        _PIDFILE.unlink(missing_ok=True)


@ops_app.command("down")
def down() -> None:
    """Stop a detached daemon (SIGTERM, graceful)."""
    if not _PIDFILE.exists():
        err_console.print("[yellow]No pidfile; nothing to stop.[/]")
        raise typer.Exit(code=1)
    pid = int(_PIDFILE.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Sent SIGTERM[/] to pid={pid}")
    except ProcessLookupError:
        console.print(f"[yellow]No process {pid}; clearing stale pidfile.[/]")
    finally:
        _PIDFILE.unlink(missing_ok=True)


@ops_app.command("health")
def health(json_out: bool = typer.Option(False, "--json")) -> None:
    """Check redis, postgres, deepseek, ollama, tools backend."""
    results = asyncio.run(run_health_checks())
    ok = all(r.ok for r in results)

    if json_out:
        emit_json({"ok": ok, "checks": [r.as_dict() for r in results]})
    else:
        table = Table(title="agent health")
        table.add_column("dependency")
        table.add_column("status")
        table.add_column("latency")
        table.add_column("detail")
        for r in results:
            table.add_row(
                r.name,
                "[green]ok[/]" if r.ok else "[red]down[/]",
                f"{r.latency_ms} ms",
                r.detail,
            )
        console.print(table)
        console.print("[green]all ok[/]" if ok else "[red]degraded[/]")

    raise typer.Exit(code=0 if ok else 1)


config_app = typer.Typer(help="Inspect configuration.")


@config_app.command("show")
def config_show(
    secrets: bool = typer.Option(False, "--secrets", help="Reveal secret values."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Print effective configuration (secrets masked unless --secrets)."""
    s = get_settings()
    data = s.unredacted() if secrets else s.redacted()

    if json_out:
        emit_json(data)
    else:
        table = Table(title="agent config" + (" (SECRETS)" if secrets else ""))
        table.add_column("key")
        table.add_column("value")
        for k, v in data.items():
            table.add_row(k, str(v))
        console.print(table)
