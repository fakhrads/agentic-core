"""`agent model` — show or switch the primary LLM provider/model."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from agent.cli._envfile import read_env, write_env
from agent.cli._output import console, emit_json
from agent.cli._providers import prompt_llm_provider
from agent.config import get_settings, reset_settings_cache

model_app = typer.Typer(help="Show or switch the primary LLM provider/model.")

_ENV_PATH = Path(".env")


@model_app.callback(invoke_without_command=True)
def model(ctx: typer.Context, json_out: bool = typer.Option(False, "--json")) -> None:
    """Show the active provider/model (run `agent model set` to change it)."""
    if ctx.invoked_subcommand is not None:
        return
    s = get_settings()
    if json_out:
        emit_json(
            {
                "provider": s.llm_provider,
                "model": s.deepseek_model,
                "base_url": s.deepseek_base_url,
            }
        )
        return
    table = Table(title="agent model")
    table.add_column("field")
    table.add_column("value")
    table.add_row("provider", s.llm_provider)
    table.add_row("model", s.deepseek_model)
    table.add_row("base_url", s.deepseek_base_url)
    key_state = "***set***" if s.deepseek_api_key.get_secret_value() else "***empty***"
    table.add_row("api_key", key_state)
    console.print(table)


@model_app.command("set")
def model_set() -> None:
    """Interactively switch provider/model/key, writing to .env."""
    current = read_env(_ENV_PATH)
    updates = prompt_llm_provider(current)
    write_env(_ENV_PATH, updates)
    reset_settings_cache()
    provider, model_name = updates["AGENT_LLM_PROVIDER"], updates["AGENT_DEEPSEEK_MODEL"]
    console.print(f"[green]Saved.[/] provider={provider} model={model_name}")
    console.print("[dim]Restart the daemon (`agent up`) / chat for it to take effect.[/]")
