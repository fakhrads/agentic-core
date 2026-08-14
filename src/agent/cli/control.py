"""Autonomy control commands: `agent budget show|set`."""

from __future__ import annotations

import redis.asyncio as redis_asyncio
import typer
from rich.table import Table

from agent.autonomy.budget import BudgetManager, BudgetSnapshot
from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import Settings, get_settings

budget_app = typer.Typer(help="Daily autonomy budget: tokens, cost, actions.")


def _manager(s: Settings) -> tuple[BudgetManager, redis_asyncio.Redis[str]]:
    client: redis_asyncio.Redis[str] = redis_asyncio.from_url(
        s.redis_url, decode_responses=True
    )
    mgr = BudgetManager(
        client,
        default_tokens=s.budget_tokens,
        default_cost_usd=s.budget_cost_usd,
        default_actions=s.budget_actions,
    )
    return mgr, client


def _bar(used: float, limit: float, width: int = 20) -> str:
    if limit <= 0:
        return "-" * width
    filled = min(width, int(width * used / limit))
    return "█" * filled + "░" * (width - filled)


async def _show() -> BudgetSnapshot:
    s = get_settings()
    mgr, client = _manager(s)
    try:
        return await mgr.snapshot()
    finally:
        await client.aclose()  # type: ignore[attr-defined]


@budget_app.command("show")
def budget_show(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show today's usage against the daily limits."""
    try:
        snap = run_async(_show())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]budget error:[/] {type(exc).__name__}: {exc}")
        err_console.print("[dim]Is redis up? `docker compose up -d`[/]")
        raise typer.Exit(code=1) from None

    if json_out:
        emit_json(
            {
                "day": snap.day,
                "limits": {
                    "tokens": snap.limits.tokens,
                    "cost_usd": snap.limits.cost_usd,
                    "actions": snap.limits.actions,
                },
                "usage": {
                    "tokens": snap.usage.tokens,
                    "cost_usd": snap.usage.cost_usd,
                    "actions": snap.usage.actions,
                },
            }
        )
        return

    table = Table(title=f"budget {snap.day}")
    table.add_column("metric")
    table.add_column("used")
    table.add_column("limit")
    table.add_column("")
    table.add_row(
        "tokens",
        f"{snap.usage.tokens:,}",
        f"{snap.limits.tokens:,}",
        _bar(snap.usage.tokens, snap.limits.tokens),
    )
    table.add_row(
        "cost",
        f"${snap.usage.cost_usd:.4f}",
        f"${snap.limits.cost_usd:.2f}",
        _bar(snap.usage.cost_usd, snap.limits.cost_usd),
    )
    table.add_row(
        "actions",
        str(snap.usage.actions),
        str(snap.limits.actions),
        _bar(snap.usage.actions, snap.limits.actions),
    )
    console.print(table)


async def _set(tokens: int | None, cost: float | None, actions: int | None) -> None:
    s = get_settings()
    mgr, client = _manager(s)
    try:
        await mgr.set_limits(tokens=tokens, cost_usd=cost, actions=actions)
    finally:
        await client.aclose()  # type: ignore[attr-defined]


@budget_app.command("set")
def budget_set(
    tokens: int | None = typer.Option(None, "--tokens"),
    cost: float | None = typer.Option(None, "--cost"),
    actions: int | None = typer.Option(None, "--actions"),
) -> None:
    """Override one or more daily limits (persisted in Redis)."""
    if tokens is None and cost is None and actions is None:
        err_console.print("[yellow]Nothing to set. Pass --tokens / --cost / --actions.[/]")
        raise typer.Exit(code=1)
    run_async(_set(tokens, cost, actions))
    console.print("[green]limits updated[/]")
