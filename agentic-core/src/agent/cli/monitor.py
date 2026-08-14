"""Monitoring: `agent tail` — a live consumer of the audit stream.

Reads recent history then follows new events, applying optional trace/grep
filters. Uses XREAD (no consumer group) so it never steals work from real
consumers. Ctrl-C stops cleanly.
"""

from __future__ import annotations

import asyncio

import typer

from agent.bus.events import STREAM_AUDIT, Event
from agent.bus.streams import EventBus
from agent.cli._output import console, err_console
from agent.config import get_settings


def _render(event: Event, json_out: bool) -> None:
    if json_out:
        # One JSON object per line (structured tail).
        print(event.model_dump_json())
        return
    ts = event.ts.strftime("%H:%M:%S")
    tid = (event.trace_id or "--------")[:8]
    console.print(
        f"[dim]{ts}[/] [cyan]{tid}[/] [bold]{event.type:<16}[/] "
        f"[magenta]{event.component}[/] {event.message}"
    )


async def _tail(
    trace: str | None,
    grep: str | None,
    last: int,
    json_out: bool,
) -> None:
    s = get_settings()
    bus = EventBus.from_url(s.redis_url)
    try:
        for msg in await bus.history(STREAM_AUDIT, count=last):
            if msg.event.matches(trace, grep):
                _render(msg.event, json_out)
        async for msg in bus.follow(STREAM_AUDIT):
            if msg.event.matches(trace, grep):
                _render(msg.event, json_out)
    finally:
        await bus.aclose()


def tail(
    trace: str | None = typer.Option(None, "--trace", help="Filter by trace_id."),
    grep: str | None = typer.Option(None, "--grep", help="Substring filter."),
    last: int = typer.Option(20, "--last", help="History lines before following."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Follow the audit event stream (Ctrl-C to stop)."""
    try:
        asyncio.run(_tail(trace, grep, last, json_out))
    except KeyboardInterrupt:
        err_console.print("\n[dim]tail stopped[/]")
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]bus error:[/] {type(exc).__name__}: {exc}")
        err_console.print("[dim]Is redis up? `docker compose up -d`[/]")
        raise typer.Exit(code=1) from None
