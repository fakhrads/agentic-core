"""Night-shift command: `agent night-shift run [--dry-run]`."""

from __future__ import annotations

import typer

from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.jobs.night_shift import NightShift, NightShiftReport
from agent.llm.base import ChatMessage
from agent.llm.ollama import OllamaProvider

nightshift_app = typer.Typer(help="Nightly autonomous cycle.")


async def _run(dry_run: bool) -> NightShiftReport:
    s = get_settings()
    provider = OllamaProvider(
        base_url=s.ollama_base_url,
        model=s.ollama_probe_model,
        timeout_s=s.ollama_timeout_s,
    )

    async def prober(prompt: str) -> str:
        result = await provider.complete(
            [ChatMessage(role="user", content=prompt)], max_tokens=256
        )
        return result.text

    shift = NightShift(s.postgres_dsn, prober)
    try:
        return await shift.run(dry_run=dry_run)
    finally:
        await provider.aclose()


@nightshift_app.command("run")
def nightshift_run(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report only; no writes."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run the night shift: probe → ingest → distill → benchmark → curate."""
    try:
        report = run_async(_run(dry_run))
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]night-shift error:[/] {type(exc).__name__}: {exc}")
        err_console.print("[dim]Needs postgres; probe needs Ollama.[/]")
        raise typer.Exit(code=1) from None

    if json_out:
        emit_json(report.as_dict())
        return
    tag = "[yellow](dry-run)[/]" if report.dry_run else ""
    console.print(f"night shift {tag}")
    for step in report.steps:
        mark = "[green]✓[/]" if step.ok else "[red]✗[/]"
        console.print(f"  {mark} {step.name}: {step.detail}")
