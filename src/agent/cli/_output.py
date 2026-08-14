"""Shared CLI output helpers — the ONLY place print/console lives.

Every command supports ``--json``: machine output goes to stdout as a single
JSON object; human output uses rich tables/panels.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any

from rich.console import Console

console = Console()
err_console = Console(stderr=True)


def emit_json(payload: Any) -> None:
    print(json.dumps(payload, default=str))


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)
