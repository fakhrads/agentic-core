"""`agent db` — schema migrations.

Without this the tables never exist on a fresh install: the daemon starts
fine, accepts a message, then fails to persist the episode and retries it
forever ("relation \"episode\" does not exist") — the bot looks alive but
never answers. `agent setup` runs `upgrade` for you; this command exists for
re-running it after an update, or when setup was skipped.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from agent.cli._output import console, err_console

db_app = typer.Typer(help="Database schema: apply migrations.")


def _repo_root() -> Path | None:
    """Locate the checkout containing alembic.ini (installed via git clone)."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "alembic.ini").exists():
            return parent
    return None


def run_migrations(*, quiet: bool = False) -> bool:
    """Apply migrations. Returns True on success. Never raises."""
    root = _repo_root()
    if root is None:
        if not quiet:
            err_console.print(
                "[yellow]Couldn't find alembic.ini[/] — skipping migrations. "
                "Run `alembic upgrade head` from the repo yourself."
            )
        return False
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=root,
            check=True,
            capture_output=quiet,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        if not quiet:
            err_console.print(f"[red]Migration failed:[/] {exc}")
            err_console.print(
                "[dim]Is Postgres reachable? Check `agent health`, then retry "
                "with `agent db upgrade`.[/]"
            )
        return False
    return True


@db_app.command("upgrade")
def upgrade() -> None:
    """Create/update the database tables (alembic upgrade head)."""
    console.print("[bold]Applying migrations[/]…")
    if not run_migrations():
        raise typer.Exit(code=1)
    console.print("[green]Database schema is up to date.[/]")
