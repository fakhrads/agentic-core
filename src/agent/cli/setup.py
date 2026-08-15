"""`agent setup` — interactive configuration wizard (writes .env).

Mirrors the Hermes-style "one command gets you running" flow: install ->
`agent setup` -> `agent`. Safe to re-run — only touches the keys it prompts
for, via `_envfile.write_env`.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
from pathlib import Path

import typer
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from agent.cli._envfile import read_env, write_env
from agent.cli._output import console, err_console
from agent.cli._providers import prompt_llm_provider
from agent.cli.db import run_migrations
from agent.config import Settings, reset_settings_cache

_ENV_PATH = Path(".env")


def _ask(
    label: str, current: dict[str, str], key: str, default: str, password: bool = False
) -> str:
    return Prompt.ask(label, default=current.get(key, default), password=password)


def setup() -> None:
    """Interactive setup wizard: LLM provider, dependencies, channels, budget."""
    console.print(
        Panel(
            "This writes/updates [bold].env[/]. Re-run anytime to change settings — "
            "existing values are kept as defaults.",
            title="agent setup",
        )
    )
    current = read_env(_ENV_PATH)
    updates: dict[str, str] = {}

    updates["AGENT_ENV"] = Prompt.ask(
        "Environment", choices=["dev", "prod"], default=current.get("AGENT_ENV", "dev")
    )

    updates.update(prompt_llm_provider(current))

    console.print("\n[bold]Ollama[/] — local embeddings + cheap probe model.")
    updates["AGENT_OLLAMA_BASE_URL"] = _ask(
        "Ollama base URL", current, "AGENT_OLLAMA_BASE_URL", "http://localhost:11434"
    )
    updates["AGENT_OLLAMA_EMBED_MODEL"] = _ask(
        "Embed model", current, "AGENT_OLLAMA_EMBED_MODEL", "nomic-embed-text"
    )
    updates["AGENT_OLLAMA_PROBE_MODEL"] = _ask(
        "Probe model", current, "AGENT_OLLAMA_PROBE_MODEL", "qwen2.5:3b"
    )

    console.print("\n[bold]Redis + Postgres[/]")
    updates["AGENT_REDIS_URL"] = _ask(
        "Redis URL", current, "AGENT_REDIS_URL", "redis://localhost:6380/0"
    )
    updates["AGENT_POSTGRES_DSN"] = _ask(
        "Postgres DSN",
        current,
        "AGENT_POSTGRES_DSN",
        "postgresql+asyncpg://agent:agent@localhost:5433/agent",
    )
    start_docker = shutil.which("docker") is not None and Confirm.ask(
        "Start redis+postgres now via `docker compose up -d`?", default=True
    )
    if start_docker:
        try:
            subprocess.run(["docker", "compose", "up", "-d"], check=True)
            console.print("[green]docker compose up -d[/] done.")
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            err_console.print(f"[yellow]docker compose failed: {exc}[/] — start it manually later.")

    console.print("\n[bold]Telegram[/]")
    if Confirm.ask("Enable Telegram?", default=bool(current.get("AGENT_TELEGRAM_BOT_TOKEN"))):
        updates["AGENT_TELEGRAM_BOT_TOKEN"] = _ask(
            "Bot token", current, "AGENT_TELEGRAM_BOT_TOKEN", "", password=True
        )
        updates["AGENT_TELEGRAM_ALLOWED_CHAT_IDS"] = _ask(
            "Allowed chat ids (comma-separated, blank = allow all)",
            current,
            "AGENT_TELEGRAM_ALLOWED_CHAT_IDS",
            "",
        )
    else:
        updates["AGENT_TELEGRAM_BOT_TOKEN"] = ""

    console.print(
        "\n[bold]WhatsApp[/] (unofficial, via the local whatsapp-bridge/ sidecar — "
        "no Meta Business account, just scan a QR code)."
    )
    if Confirm.ask("Enable WhatsApp?", default=bool(current.get("AGENT_WHATSAPP_BRIDGE_SECRET"))):
        updates["AGENT_WHATSAPP_BRIDGE_URL"] = _ask(
            "Bridge URL", current, "AGENT_WHATSAPP_BRIDGE_URL", "http://127.0.0.1:8098"
        )
        default_secret = current.get("AGENT_WHATSAPP_BRIDGE_SECRET") or secrets.token_hex(16)
        bridge_secret = _ask(
            "Shared secret (also set this as BRIDGE_SECRET in whatsapp-bridge/.env)",
            current,
            "AGENT_WHATSAPP_BRIDGE_SECRET",
            default_secret,
            password=True,
        )
        updates["AGENT_WHATSAPP_BRIDGE_SECRET"] = bridge_secret
        updates["AGENT_WHATSAPP_ALLOWED_NUMBERS"] = _ask(
            "Allowed numbers (comma-separated, blank = allow all)",
            current,
            "AGENT_WHATSAPP_ALLOWED_NUMBERS",
            "",
        )
        console.print(
            "[dim]Next: cd whatsapp-bridge && npm install && "
            f"BRIDGE_SECRET={bridge_secret} npm start — then scan the QR code.[/]"
        )
    else:
        updates["AGENT_WHATSAPP_BRIDGE_SECRET"] = ""

    console.print("\n[bold]Daily autonomy budget[/]")
    updates["AGENT_BUDGET_TOKENS"] = _ask("Token budget", current, "AGENT_BUDGET_TOKENS", "500000")
    updates["AGENT_BUDGET_COST_USD"] = _ask(
        "Cost budget (USD)", current, "AGENT_BUDGET_COST_USD", "2.00"
    )
    updates["AGENT_BUDGET_ACTIONS"] = _ask("Action budget", current, "AGENT_BUDGET_ACTIONS", "200")

    write_env(_ENV_PATH, updates)
    reset_settings_cache()

    console.print("\n[green]Wrote .env[/]\n")
    s = Settings()
    for k, v in s.redacted().items():
        console.print(f"  [dim]{k}[/] = {v}")

    # Without the schema the daemon starts fine but can never persist an
    # episode — the bot looks alive and silently never answers. Do it here so
    # the happy path is genuinely one command.
    console.print("\n[bold]Database schema[/]")
    if run_migrations():
        console.print("[green]Tables are up to date.[/]")
    else:
        console.print("[yellow]Skipped — run [cyan]agent db upgrade[/] once Postgres is up.[/]")

    console.print(
        "\n[bold]Next:[/] run [cyan]agent health[/] to verify connectivity, "
        "then [cyan]agent[/] to start chatting."
    )


if __name__ == "__main__":
    typer.run(setup)
