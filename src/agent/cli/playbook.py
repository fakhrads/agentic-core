"""Playbook commands: `agent playbook diff|rollback`."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.syntax import Syntax

from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.playbook.revise import list_revisions, rollback
from agent.playbook.store import PlaybookStore

playbook_app = typer.Typer(help="Playbook (MEMORY/USER/SELF): inspect and roll back.")


@playbook_app.command("diff")
def playbook_diff(
    file: str | None = typer.Option(None, "--file", help="Limit to one file."),
    limit: int = typer.Option(5, "--limit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show recent revisions with rationale and diff."""
    s = get_settings()

    async def _run() -> list[dict[str, object]]:
        async with session_scope(s.postgres_dsn) as session:
            revs = await list_revisions(session, file=file, limit=limit)
            return [
                {
                    "id": r.id,
                    "file": r.file,
                    "at": r.at.isoformat() if r.at else None,
                    "rationale": r.rationale,
                    "reverted": r.reverted_bool,
                    "diff": r.diff,
                }
                for r in revs
            ]

    try:
        rows = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]playbook error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None

    if json_out:
        emit_json({"revisions": rows})
        return
    if not rows:
        console.print("[dim]no revisions yet[/]")
        return
    diff_console: Console = console
    for r in rows:
        rev_flag = " [yellow](reverted)[/]" if r["reverted"] else ""
        console.print(
            f"[bold]#{r['id']}[/] [cyan]{r['file']}[/] {r['at']}{rev_flag}\n"
            f"  rationale: {r['rationale']}"
        )
        if r["diff"]:
            diff_console.print(Syntax(str(r["diff"]), "diff", theme="ansi_dark"))


@playbook_app.command("rollback")
def playbook_rollback(rev: int = typer.Argument(..., help="Revision id to restore.")) -> None:
    """Restore the playbook file to a prior revision's content."""
    s = get_settings()
    store = PlaybookStore(s.playbook_dir)

    async def _run() -> int | None:
        async with session_scope(s.postgres_dsn) as session:
            new_rev = await rollback(session, store, rev)
            return new_rev.id if new_rev else None

    try:
        new_id = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]playbook error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None

    if new_id is None:
        err_console.print(f"[red]no revision {rev}[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]rolled back[/] to rev {rev} (new rev #{new_id})")
