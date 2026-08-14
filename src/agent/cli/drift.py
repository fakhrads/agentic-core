"""Drift commands: `agent drift report|status|clear`."""

from __future__ import annotations

import redis.asyncio as redis_asyncio
import typer
from rich.table import Table

from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.evolution.drift import DriftReport as DriftReportData
from agent.evolution.drift import DriftState, drift_report

drift_app = typer.Typer(help="Misevolution drift: report, status, clear pause.")


@drift_app.command("report")
def drift_report_cmd(json_out: bool = typer.Option(False, "--json")) -> None:
    """Correlate the latest regression drop with recent changes, ranked."""
    s = get_settings()

    async def _run() -> DriftReportData:
        async with session_scope(s.postgres_dsn) as session:
            return await drift_report(session)

    try:
        rep = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]drift error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None

    if json_out:
        emit_json(
            {
                "have_comparison": rep.have_comparison,
                "latest": rep.latest_score,
                "prior": rep.prior_score,
                "dropped": rep.dropped,
                "newly_failing": rep.newly_failing,
                "note": rep.note,
                "suspects": [
                    {"kind": sp.kind, "ref_id": sp.ref_id, "at": sp.at} for sp in rep.suspects
                ],
            }
        )
        return
    if not rep.have_comparison:
        console.print(f"[dim]{rep.note}[/]")
        return
    console.print(
        f"latest [bold]{rep.latest_score}[/] vs prior {rep.prior_score} — "
        f"dropped {rep.dropped} ({rep.note})"
    )
    if rep.newly_failing:
        console.print(f"[red]newly failing:[/] {', '.join(rep.newly_failing)}")
    if rep.suspects:
        table = Table(title="suspects (most likely first)")
        for col in ("kind", "ref_id", "at"):
            table.add_column(col)
        for sp in rep.suspects:
            table.add_row(sp.kind, str(sp.ref_id), sp.at)
        console.print(table)


def _drift_state() -> tuple[DriftState, redis_asyncio.Redis[str]]:
    s = get_settings()
    client: redis_asyncio.Redis[str] = redis_asyncio.from_url(
        s.redis_url, decode_responses=True
    )
    return DriftState(client), client


@drift_app.command("status")
def drift_status(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show whether drift-pause is active."""

    async def _run() -> dict[str, object]:
        state, client = _drift_state()
        try:
            st = await state.status()
            return {"paused": st.paused, "reason": st.reason, "since": st.since}
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    try:
        data = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]drift error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
    if json_out:
        emit_json(data)
        return
    if data["paused"]:
        console.print(f"[bold red]drift-pause ACTIVE[/] since {data['since']}: {data['reason']}")
    else:
        console.print("[green]no drift-pause[/]")


@drift_app.command("clear")
def drift_clear() -> None:
    """Clear drift-pause after remediation (rollback/demote/disable)."""

    async def _run() -> None:
        state, client = _drift_state()
        try:
            await state.clear()
        finally:
            await client.aclose()  # type: ignore[attr-defined]

    try:
        run_async(_run())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]drift error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
    console.print("[green]drift-pause cleared[/]")
