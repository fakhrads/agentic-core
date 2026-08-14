"""Approval commands: `agent approve <id|list>` and `agent reject <id>`.

Approving a `tool.register` action submits the forged tool to the tools backend
using the register-scoped token; the backend runs its tests before it can reach
probation (contract §2.4).
"""

from __future__ import annotations

import typer
from rich.table import Table

from agent.autonomy.approvals import (
    ApprovalError,
    decide_approval,
    get_approval,
    list_pending,
)
from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.models import Approval
from agent.tools.forge import ACTION_TOOL_REGISTER, RegisterResult, ToolForgeClient


async def _list() -> list[dict[str, object]]:
    s = get_settings()
    async with session_scope(s.postgres_dsn) as session:
        pend = await list_pending(session)
        return [
            {
                "id": a.id,
                "action_kind": a.action_kind,
                "tier": a.tier,
                "requested_at": a.requested_at.isoformat() if a.requested_at else None,
                "summary": _summary(a),
            }
            for a in pend
        ]


def _summary(a: Approval) -> str:
    if a.action_kind == ACTION_TOOL_REGISTER:
        sub = a.payload.get("submission", {})
        return f"tool '{sub.get('name', '?')}'"
    return a.action_kind


async def _register_forged(submission: dict[str, object]) -> RegisterResult:
    s = get_settings()
    client = ToolForgeClient(
        base_url=s.tools_base_url,
        forge_token=s.tools_forge_token.get_secret_value(),
        contract_version=s.contract_version,
        timeout_s=s.tools_timeout_s,
    )
    try:
        return await client.register(submission)
    finally:
        await client.aclose()


async def _approve(approval_id: int) -> dict[str, object] | None:
    s = get_settings()
    async with session_scope(s.postgres_dsn) as session:
        approval = await get_approval(session, approval_id)
        if approval is None:
            return None
        action_kind = approval.action_kind
        submission = dict(approval.payload.get("submission", {}))
        await decide_approval(session, approval_id, approved=True)

    result: dict[str, object] = {"id": approval_id, "status": "approved"}
    if action_kind == ACTION_TOOL_REGISTER:
        reg = await _register_forged(submission)
        result["registration"] = {
            "ok": reg.ok, "status": reg.status, "name": reg.name,
            "reason": reg.reason, "detail": reg.detail,
        }
    return result


def approve(
    target: str = typer.Argument(..., help="An approval id, or the literal 'list'."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Approve a pending action (executes it), or `agent approve list`."""
    try:
        if target == "list":
            rows = run_async(_list())
            if json_out:
                emit_json({"pending": rows})
                return
            table = Table(title="pending approvals")
            for col in ("id", "action_kind", "tier", "summary"):
                table.add_column(col)
            for r in rows:
                table.add_row(
                    str(r["id"]), str(r["action_kind"]), str(r["tier"]), str(r["summary"])
                )
            console.print(table)
            return
        data = run_async(_approve(int(target)))
    except ApprovalError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from None
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]approve error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None

    if data is None:
        err_console.print(f"[red]no approval {target}[/]")
        raise typer.Exit(code=1)
    if json_out:
        emit_json(data)
        return
    console.print(f"[green]approved[/] #{target}")
    if "registration" in data:
        console.print(f"registration: {data['registration']}")


def reject(approval_id: int = typer.Argument(...)) -> None:
    """Reject a pending action."""
    s = get_settings()

    async def _run() -> Approval | None:
        async with session_scope(s.postgres_dsn) as session:
            return await decide_approval(session, approval_id, approved=False)

    try:
        got = run_async(_run())
    except ApprovalError as exc:
        err_console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from None
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]reject error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
    if got is None:
        err_console.print(f"[red]no approval {approval_id}[/]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]rejected[/] #{approval_id}")
