"""Local channel — outbound replies print straight to the terminal.

Used by `agent chat` for the interactive REPL. Same shape as DevChannel, but
user-facing instead of audit-only.
"""

from __future__ import annotations

from rich.console import Console

_console = Console()


class LocalChannel:
    name = "local"

    async def send(self, chat_id: str, text: str) -> None:
        _console.print(f"[bold cyan]agent>[/] {text}")
