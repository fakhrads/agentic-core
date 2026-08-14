"""Tools inspection: `agent tools ls`."""

from __future__ import annotations

import typer
from rich.table import Table

from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.tools.client import ToolsClient
from agent.tools.models import ToolEntry

tools_app = typer.Typer(help="Inspect tools available from the tools backend.")


async def _forge(need: str) -> int:
    import redis.asyncio as redis_asyncio

    from agent.autonomy.budget import BudgetManager
    from agent.db.base import session_scope
    from agent.llm.base import BudgetedLLM, ChatMessage
    from agent.llm.deepseek import DeepSeekProvider
    from agent.llm.recorder import DBCostRecorder
    from agent.tools.forge import FORGE_PROMPT, ForgeArtifact, ToolForge, parse_forge_json
    from agent.trace import new_trace_id

    s = get_settings()
    redis: redis_asyncio.Redis[str] = redis_asyncio.from_url(s.redis_url, decode_responses=True)
    provider = DeepSeekProvider(
        base_url=s.deepseek_base_url,
        api_key=s.deepseek_api_key.get_secret_value(),
        model=s.deepseek_model,
        timeout_s=s.deepseek_timeout_s,
    )
    budget = BudgetManager(
        redis, default_tokens=s.budget_tokens, default_cost_usd=s.budget_cost_usd,
        default_actions=s.budget_actions,
    )
    llm = BudgetedLLM(provider, budget, DBCostRecorder(s.postgres_dsn))

    async def generator(need_text: str) -> ForgeArtifact:
        result = await llm.complete(
            [ChatMessage(role="user", content=FORGE_PROMPT.format(need=need_text))],
            max_tokens=1500,
        )
        return parse_forge_json(result.text)

    forge = ToolForge(generator)
    try:
        async with session_scope(s.postgres_dsn) as session:
            approval, _artifact = await forge.forge_and_request(
                session, need=need, trace_id=new_trace_id()
            )
            return approval.id
    finally:
        await provider.aclose()
        await redis.aclose()  # type: ignore[attr-defined]


@tools_app.command("needs")
def tools_needs(json_out: bool = typer.Option(False, "--json")) -> None:
    """List capability gaps recorded from trajectories (unknown-tool calls)."""
    from agent.db.base import session_scope
    from agent.tools.needs import list_needs

    s = get_settings()

    async def _run() -> list[dict[str, object]]:
        async with session_scope(s.postgres_dsn) as session:
            needs = await list_needs(session)
            return [
                {"id": n.id, "name": n.name, "count": n.count, "status": n.status}
                for n in needs
            ]

    try:
        rows = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]tools error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
    if json_out:
        emit_json({"needs": rows})
        return
    table = Table(title="tool needs")
    for col in ("id", "name", "count", "status"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), str(r["name"]), str(r["count"]), str(r["status"]))
    console.print(table)


@tools_app.command("forge")
def tools_forge(
    need: str = typer.Argument(..., help="Describe the tool the agent needs."),
) -> None:
    """Generate a tool + tests and queue it for approval (tier APPROVE)."""
    try:
        approval_id = run_async(_forge(need))
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]forge error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None
    console.print(f"[green]forged[/] → approval #{approval_id} pending")
    console.print(f"review: [cyan]agent approve {approval_id}[/]")


async def _list() -> list[ToolEntry]:
    s = get_settings()
    client = ToolsClient(
        base_url=s.tools_base_url,
        service_token=s.tools_service_token.get_secret_value(),
        contract_version=s.contract_version,
        timeout_s=s.tools_timeout_s,
    )
    try:
        return await client.list_tools()
    finally:
        await client.aclose()


@tools_app.command("ls")
def tools_ls(json_out: bool = typer.Option(False, "--json")) -> None:
    """List tools from `GET /tools` with status and cost hints."""
    try:
        tools = run_async(_list())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]tools error:[/] {type(exc).__name__}: {exc}")
        err_console.print("[dim]Is the tools backend reachable (via Traefik)?[/]")
        raise typer.Exit(code=1) from None

    if json_out:
        emit_json({"tools": [t.model_dump() for t in tools]})
        return

    table = Table(title="tools")
    table.add_column("name")
    table.add_column("v")
    table.add_column("status")
    table.add_column("cost")
    table.add_column("timeout")
    table.add_column("probation")
    for t in tools:
        color = {"active": "green", "probation": "yellow", "disabled": "red"}.get(
            t.status, "white"
        )
        prob = ""
        if t.probation is not None:
            prob = f"{t.probation.invocations}/{t.probation.required} (f{t.probation.failures})"
        table.add_row(
            t.name,
            str(t.version),
            f"[{color}]{t.status}[/]",
            t.cost_hint,
            f"{t.timeout_ms}ms",
            prob,
        )
    console.print(table)
