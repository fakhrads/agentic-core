"""`agent update` — pull the latest code and reinstall.

Locates the git checkout this `agent` was installed from (walking up from the
installed package's own file — works whether you're in the venv shim, a
detached shell, wherever) and runs `git pull --ff-only` + `pip install -e .`,
so `AGENT_INSTALL_DIR`/cwd don't need to be tracked separately.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from agent.cli._output import console, err_console
from agent.cli.db import run_migrations


def _find_repo_root() -> Path | None:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() and (parent / "pyproject.toml").exists():
            return parent
    return None


def update() -> None:
    """Pull the latest agent-core code and reinstall (git pull + pip install -e .)."""
    repo = _find_repo_root()
    if repo is None:
        err_console.print(
            "[red]Couldn't find a git checkout to update.[/] This only works when "
            "`agent` was installed from a git clone (e.g. via scripts/install.sh)."
        )
        raise typer.Exit(code=1)

    console.print(f"[bold]Updating[/] {repo}")
    try:
        subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"], check=True)
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "-e", str(repo)], check=True
        )
    except subprocess.CalledProcessError as exc:
        err_console.print(f"[red]Update failed:[/] {exc}")
        err_console.print(
            "[dim]If you have local edits in that checkout, commit/stash them first — "
            "`git pull --ff-only` refuses to overwrite them.[/]"
        )
        raise typer.Exit(code=1) from None

    # An update may ship new migrations; applying them here keeps `agent
    # update` a single step. Quiet + best-effort: Postgres may legitimately be
    # down at this moment, and that shouldn't fail the code update.
    if run_migrations(quiet=True):
        console.print("[dim]Database schema up to date.[/]")
    else:
        console.print("[yellow]Migrations not applied[/] — run [cyan]agent db upgrade[/] later.")

    console.print("[green]Updated.[/] Restart `agent` / `agent up` for changes to take effect.")


if __name__ == "__main__":
    typer.run(update)
