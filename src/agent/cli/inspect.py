"""Inspection commands: `agent trace <id>` and `agent trace list`.

Single overloaded `trace` command so the spec surface holds exactly:
  agent trace <id> [--tree]
  agent trace list [--today] [--failed]
"""

from __future__ import annotations

import typer
from rich.table import Table
from rich.tree import Tree

from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.models import Episode
from agent.db.repo import get_episode_by_trace, list_episodes


def _episode_dict(ep: Episode, with_steps: bool = True) -> dict[str, object]:
    d: dict[str, object] = {
        "trace_id": ep.trace_id,
        "source": ep.source,
        "status": ep.status,
        "started_at": ep.started_at.isoformat() if ep.started_at else None,
        "ended_at": ep.ended_at.isoformat() if ep.ended_at else None,
        "summary": ep.summary,
        "human_score": ep.human_score,
    }
    if with_steps:
        d["steps"] = [
            {
                "idx": s.idx,
                "kind": s.kind,
                "ok": s.ok,
                "duration_ms": s.duration_ms,
                "input": s.input,
                "output": s.output,
            }
            for s in ep.steps
        ]
    return d


async def _show(trace_id: str, tree: bool, json_out: bool) -> int:
    s = get_settings()
    async with session_scope(s.postgres_dsn) as session:
        ep = await get_episode_by_trace(session, trace_id)
        if ep is None:
            if json_out:
                emit_json({"error": "not_found", "trace_id": trace_id})
            else:
                err_console.print(f"[red]No episode with trace_id={trace_id}[/]")
            return 1
        data = _episode_dict(ep)

    if json_out:
        emit_json(data)
        return 0

    status_color = {"done": "green", "failed": "red", "running": "yellow"}.get(
        ep.status, "white"
    )
    console.print(
        f"[bold]{ep.trace_id}[/]  source=[cyan]{ep.source}[/]  "
        f"status=[{status_color}]{ep.status}[/]"
    )
    if ep.summary:
        console.print(f"summary: {ep.summary}")

    if tree:
        root = Tree(f"episode {ep.trace_id}")
        for st in ep.steps:
            mark = "✓" if st.ok else ("✗" if st.ok is False else "•")
            dur = f" {st.duration_ms}ms" if st.duration_ms is not None else ""
            root.add(f"[{mark}] {st.idx} {st.kind}{dur}")
        console.print(root)
    else:
        table = Table(title="steps")
        table.add_column("idx")
        table.add_column("kind")
        table.add_column("ok")
        table.add_column("ms")
        for st in ep.steps:
            table.add_row(
                str(st.idx),
                st.kind,
                "✓" if st.ok else ("✗" if st.ok is False else "-"),
                str(st.duration_ms or "-"),
            )
        console.print(table)
    return 0


async def _list(today: bool, failed: bool, json_out: bool) -> int:
    s = get_settings()
    async with session_scope(s.postgres_dsn) as session:
        eps = await list_episodes(session, today=today, failed=failed)
        rows = [_episode_dict(ep, with_steps=False) for ep in eps]

    if json_out:
        emit_json({"episodes": rows})
        return 0

    table = Table(title="episodes")
    table.add_column("trace_id")
    table.add_column("source")
    table.add_column("status")
    table.add_column("started")
    for r in rows:
        table.add_row(
            str(r["trace_id"]),
            str(r["source"]),
            str(r["status"]),
            str(r["started_at"]),
        )
    console.print(table)
    return 0


def trace(
    target: str = typer.Argument(..., help="A trace_id, or the literal 'list'."),
    tree: bool = typer.Option(False, "--tree", help="Render steps as a tree."),
    today: bool = typer.Option(False, "--today", help="[list] only today."),
    failed: bool = typer.Option(False, "--failed", help="[list] only failed."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Inspect an episode, or `agent trace list` to enumerate them."""
    try:
        if target == "list":
            code = run_async(_list(today, failed, json_out))
        else:
            code = run_async(_show(target, tree, json_out))
    except Exception as exc:  # noqa: BLE001 — surface DB errors cleanly
        err_console.print(f"[red]DB error:[/] {type(exc).__name__}: {exc}")
        err_console.print("[dim]Is postgres up? `docker compose up -d`[/]")
        raise typer.Exit(code=1) from None
    raise typer.Exit(code=code)
