"""`agent chat` — foreground interactive REPL (the Hermes-style default UX).

Runs the full daemon (loop, memory, budget, channels — same as `agent up`)
with a `LocalChannel` injected so typed lines round-trip through the real
agent loop and replies print to the terminal. `/exit` or Ctrl-C shuts down
gracefully (same signal-driven shutdown path as `agent up`).
"""

from __future__ import annotations

import asyncio
import os
import signal

import typer

from agent.channels.base import InboundMessage
from agent.channels.local import LocalChannel
from agent.cli._output import console, err_console
from agent.config import get_settings

_EXIT_COMMANDS = {"/exit", "/quit"}


async def _chat() -> None:
    from agent.daemon import Daemon

    settings = get_settings()
    daemon = Daemon(settings, extra_channels=[LocalChannel()])
    daemon_task = asyncio.create_task(daemon.run(), name="daemon")

    console.print(
        "[bold]agent chat[/] — type a message and press enter. "
        "[dim]/exit to quit, Ctrl-C also works.[/]\n"
    )
    try:
        while not daemon_task.done():
            line = await asyncio.to_thread(input, "you> ")
            if line.strip() in _EXIT_COMMANDS:
                break
            if not line.strip():
                continue
            await daemon.ingest(
                InboundMessage(channel="local", chat_id="cli", text=line)
            )
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if not daemon_task.done():
            os.kill(os.getpid(), signal.SIGTERM)
        await daemon_task


def chat() -> None:
    """Start the agent and chat with it interactively in this terminal."""
    try:
        asyncio.run(_chat())
    except KeyboardInterrupt:
        err_console.print("\n[dim]chat stopped[/]")
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]chat error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
