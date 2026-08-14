"""`agent watch` — live TUI dashboard (rich.Live).

A consumer of live state: heartbeat + budget + audit stream (Redis) and
episode/regression/approval state (Postgres). Read-only; refreshes on an
interval. Runs from any machine that can reach Redis + Postgres.
"""

from __future__ import annotations

import asyncio

import redis.asyncio as redis_asyncio
import typer
from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from agent.autonomy.budget import BudgetManager
from agent.bus.streams import EventBus
from agent.cli._output import console, err_console
from agent.config import get_settings
from agent.dashboard import Snapshot, gather_snapshot


def _bar(used: float, limit: float, width: int = 12) -> str:
    if limit <= 0:
        return "·" * width
    filled = min(width, int(width * used / limit))
    return "█" * filled + "░" * (width - filled)


def _fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _fmt_num(n: int) -> str:
    return f"{n/1000:.0f}k" if n >= 1000 else str(n)


def _status_panel(snap: Snapshot) -> Panel:
    t = Table.grid(padding=(0, 2))
    loop = "[green]running[/]" if snap.running else "[red]stopped[/]"
    t.add_row("loop", loop)
    t.add_row("uptime", _fmt_uptime(snap.uptime_s))
    t.add_row("queue", f"{snap.queue_len} event")
    return Panel(t, title="status", title_align="left")


def _budget_panel(snap: Snapshot) -> Panel:
    t = Table.grid(padding=(0, 2))
    if snap.budget is None:
        t.add_row("budget", "n/a")
        return Panel(t, title="budget today", title_align="left")
    b = snap.budget
    t.add_row(
        "token",
        f"{_fmt_num(b.usage.tokens)} / {_fmt_num(b.limits.tokens)}  "
        f"{_bar(b.usage.tokens, b.limits.tokens)}",
    )
    t.add_row("cost", f"${b.usage.cost_usd:.2f} / ${b.limits.cost_usd:.2f}")
    t.add_row("aksi", f"{b.usage.actions} / {b.limits.actions}")
    return Panel(t, title="budget today", title_align="left")


def _episode_panel(snap: Snapshot) -> Panel:
    ep = snap.active_episode
    body = (
        f"[cyan]{ep.trace_id[:8]}[/]… {ep.summary} → {ep.last_step}"
        if ep is not None
        else "[dim]idle — no running episode[/]"
    )
    return Panel(body, title="active episode", title_align="left")


def _evolution_panel(snap: Snapshot) -> Panel:
    lines = []
    r = snap.regression
    if r is not None:
        prev = (
            f"(prev {r.prev_passed}/{r.prev_total})"
            if r.prev_passed is not None
            else "(no prior)"
        )
        flag = " [red]⚠ drop[/]" if r.dropped else " [green]✓[/]"
        lines.append(f"regression  {r.passed}/{r.total}{flag}  {prev}")
    else:
        lines.append("regression  [dim]no runs[/]")
    lines.append(f"archive     {snap.archive_count} item, {snap.resampled_today} resampled today")
    return Panel("\n".join(lines), title="evolution health", title_align="left")


def _events_panel(snap: Snapshot) -> Panel:
    t = Table.grid(padding=(0, 1))
    for ev in snap.events[-10:]:
        ts = ev.ts.strftime("%H:%M:%S")
        t.add_row(f"[dim]{ts}[/]", f"[bold]{ev.type[:14]:<14}[/]", ev.message[:44])
    if not snap.events:
        t.add_row("[dim]no events yet[/]")
    return Panel(t, title="last 10 events", title_align="left")


def _approvals_panel(snap: Snapshot) -> Panel:
    if not snap.approvals:
        return Panel("[dim]none pending[/]", title="approval queue", title_align="left")
    t = Table.grid(padding=(0, 2))
    for a in snap.approvals:
        t.add_row(f"#{a.id}", a.summary, f"[cyan]agent approve {a.id}[/]")
    return Panel(t, title="approval queue", title_align="left")


def render(snap: Snapshot) -> RenderableType:
    top = Columns([_status_panel(snap), _budget_panel(snap)], equal=True, expand=True)
    return Group(
        top,
        _episode_panel(snap),
        _evolution_panel(snap),
        _events_panel(snap),
        _approvals_panel(snap),
    )


async def _watch(interval: float) -> None:
    s = get_settings()
    redis: redis_asyncio.Redis[str] = redis_asyncio.from_url(
        s.redis_url, decode_responses=True
    )
    bus = EventBus(redis)
    budget = BudgetManager(
        redis, default_tokens=s.budget_tokens, default_cost_usd=s.budget_cost_usd,
        default_actions=s.budget_actions,
    )
    try:
        with Live(console=console, refresh_per_second=4, screen=True) as live:
            while True:
                snap = await gather_snapshot(s.postgres_dsn, redis, bus, budget)
                live.update(render(snap))
                await asyncio.sleep(interval)
    finally:
        await redis.aclose()  # type: ignore[attr-defined]


def watch(interval: float = typer.Option(1.5, "--interval", help="Refresh seconds.")) -> None:
    """Live dashboard (Ctrl-C to exit)."""
    try:
        asyncio.run(_watch(interval))
    except KeyboardInterrupt:
        err_console.print("\n[dim]watch stopped[/]")
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]watch error:[/] {type(exc).__name__}: {exc}")
        err_console.print("[dim]Needs redis + postgres reachable.[/]")
        raise typer.Exit(code=1) from None
