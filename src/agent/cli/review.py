"""Review command: `agent review <trace> --score 1..5` and `agent review pending`."""

from __future__ import annotations

import typer
from rich.table import Table

from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.evolution.review import (
    ReviewError,
    distribute_reward,
    pending_reviews,
    set_review,
)

review_app = typer.Typer()


async def _pending() -> list[dict[str, object]]:
    s = get_settings()
    async with session_scope(s.postgres_dsn) as session:
        eps = await pending_reviews(session)
        return [
            {"trace_id": p.trace_id, "episode_id": p.episode_id, "impact": p.impact}
            for p in eps
        ]


async def _review(trace_id: str, score: int, note: str | None) -> str | None:
    s = get_settings()
    async with session_scope(s.postgres_dsn) as session:
        ep = await set_review(session, trace_id, score=score, note=note)
        if ep is None:
            return None
        per = await distribute_reward(session, ep)
        return f"score={score} reward_per_artefact={per:+.3f}"


def review(
    target: str = typer.Argument(..., help="A trace_id, or the literal 'pending'."),
    score: int = typer.Option(0, "--score", min=0, max=5, help="1..5 (required for a trace)."),
    note: str | None = typer.Option(None, "--note"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Grade an episode (distributes reward), or `agent review pending`."""
    try:
        if target == "pending":
            rows = run_async(_pending())
            if json_out:
                emit_json({"pending": rows})
                return
            table = Table(title="pending review (most impactful)")
            for col in ("trace_id", "episode_id", "impact"):
                table.add_column(col)
            for r in rows:
                table.add_row(str(r["trace_id"]), str(r["episode_id"]), str(r["impact"]))
            console.print(table)
            return

        if not 1 <= score <= 5:
            err_console.print("[red]--score must be 1..5 for a trace review[/]")
            raise typer.Exit(code=1)
        result = run_async(_review(target, score, note))
    except ReviewError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from None
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]review error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None

    if result is None:
        err_console.print(f"[red]no episode with trace_id={target}[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]reviewed[/] {target} → {result}")
