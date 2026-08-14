"""Goal commands: `agent goals ls|add|drop|probe`."""

from __future__ import annotations

import typer
from rich.table import Table

from agent.autonomy.goals import create_goal, drop_goal, get_goal, list_goals, probe_goal
from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.models import GOAL_ORIGIN_USER
from agent.llm.base import ChatMessage
from agent.llm.ollama import OllamaProvider

goals_app = typer.Typer(help="Goal stack: list, add, drop, probe feasibility.")


def _guard(exc: Exception) -> None:
    err_console.print(f"[red]goals error:[/] {type(exc).__name__}: {exc}")
    err_console.print("[dim]Is postgres up? (probe also needs Ollama)[/]")
    raise typer.Exit(code=1) from None


@goals_app.command("ls")
def goals_ls(
    status: str | None = typer.Option(None, "--status"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List goals (optionally filtered by status)."""
    s = get_settings()

    async def _run() -> list[dict[str, object]]:
        async with session_scope(s.postgres_dsn) as session:
            goals = await list_goals(session, status=status)
            return [
                {
                    "id": g.id,
                    "status": g.status,
                    "origin": g.origin,
                    "depth": g.depth,
                    "text": g.text[:80],
                }
                for g in goals
            ]

    try:
        rows = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if json_out:
        emit_json({"goals": rows})
        return
    table = Table(title="goals")
    for col in ("id", "status", "origin", "depth", "text"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]), str(r["status"]), str(r["origin"]), str(r["depth"]), str(r["text"])
        )
    console.print(table)


@goals_app.command("add")
def goals_add(text: str = typer.Argument(...)) -> None:
    """Add an operator goal (origin=user → active, no probe)."""
    s = get_settings()

    async def _run() -> int:
        async with session_scope(s.postgres_dsn) as session:
            goal = await create_goal(session, text=text, origin=GOAL_ORIGIN_USER)
            return goal.id

    try:
        goal_id = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    console.print(f"[green]added[/] goal #{goal_id} (active)")


@goals_app.command("drop")
def goals_drop(goal_id: int = typer.Argument(...)) -> None:
    """Drop a goal (status=dropped)."""
    s = get_settings()

    async def _run() -> str | None:
        async with session_scope(s.postgres_dsn) as session:
            goal = await drop_goal(session, goal_id)
            return goal.status if goal else None

    try:
        status = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if status is None:
        err_console.print(f"[red]no goal {goal_id}[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]dropped[/] goal #{goal_id}")


@goals_app.command("probe")
def goals_probe(
    goal_id: int = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Manually run a feasibility probe on a goal (cheap Ollama model)."""
    s = get_settings()

    async def _run() -> dict[str, object] | None:
        provider = OllamaProvider(
            base_url=s.ollama_base_url,
            model=s.ollama_probe_model,
            timeout_s=s.ollama_timeout_s,
        )

        async def prober(prompt: str) -> str:
            result = await provider.complete(
                [ChatMessage(role="user", content=prompt)], max_tokens=256
            )
            return result.text

        try:
            async with session_scope(s.postgres_dsn) as session:
                goal = await get_goal(session, goal_id)
                if goal is None:
                    return None
                await probe_goal(session, goal, prober)
                return {"id": goal.id, "status": goal.status, "probe": goal.probe_result}
        finally:
            await provider.aclose()

    try:
        data = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if data is None:
        err_console.print(f"[red]no goal {goal_id}[/]")
        raise typer.Exit(code=1)
    if json_out:
        emit_json(data)
        return
    console.print(f"goal #{data['id']} → [bold]{data['status']}[/]")
    console.print(data["probe"])
