"""`agent` CLI entrypoint (typer).

M1 wires the real ops commands (up/down/health/config) and registers the full
command surface from the spec as stubs so the shape is visible from day one.
Each later milestone replaces its stub with a working implementation.
"""

from __future__ import annotations

from pathlib import Path

import typer

from agent.cli.approvals import approve, reject
from agent.cli.chat import chat
from agent.cli.control import budget_app
from agent.cli.dev import dev_app
from agent.cli.drift import drift_app
from agent.cli.evolution import regression_app
from agent.cli.goals import goals_app
from agent.cli.inspect import trace
from agent.cli.memory import memory_app
from agent.cli.model import model_app
from agent.cli.monitor import tail
from agent.cli.nightshift import nightshift_app
from agent.cli.ops import config_app, down, health, up
from agent.cli.playbook import playbook_app
from agent.cli.review import review
from agent.cli.setup import setup
from agent.cli.skills import skills_app
from agent.cli.tools import tools_app
from agent.cli.update import update
from agent.cli.watch import watch

app = typer.Typer(
    name="agent",
    help="Autonomous agent core — operator CLI.",
    no_args_is_help=False,
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
    """Bare `agent` runs setup on first use, then drops into interactive chat."""
    if ctx.invoked_subcommand is not None:
        return
    if not Path(".env").exists():
        setup()
    chat()

# --- Install/run UX ---
app.command("setup")(setup)
app.command("chat")(chat)
app.command("update")(update)
app.add_typer(model_app, name="model")

# --- Real M1 commands ---
app.command("up")(up)
app.command("down")(down)
app.command("health")(health)
app.add_typer(config_app, name="config")

# --- Real M2 commands ---
app.command("tail")(tail)
app.command("trace")(trace)
app.add_typer(dev_app, name="dev")

# --- Real M3 commands ---
app.add_typer(budget_app, name="budget")

# --- Real M4 commands ---
app.add_typer(tools_app, name="tools")

# --- Real M5 commands ---
app.add_typer(memory_app, name="memory")

# --- Real M6 commands ---
app.add_typer(regression_app, name="regression")

# --- Real M7 commands ---
app.add_typer(playbook_app, name="playbook")

# --- Real M8 commands ---
app.add_typer(goals_app, name="goals")
app.add_typer(nightshift_app, name="night-shift")

# --- Real M9 commands ---
app.add_typer(skills_app, name="skills")

# --- Real M10 commands ---
app.command("review")(review)
app.add_typer(drift_app, name="drift")

# --- Real M11 commands ---
app.command("approve")(approve)
app.command("reject")(reject)

# --- Real M12 commands ---
app.command("watch")(watch)

if __name__ == "__main__":
    app()
