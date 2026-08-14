"""Skill commands: `agent skills ls|show|test|disable`."""

from __future__ import annotations

import redis.asyncio as redis_asyncio
import typer
from rich.table import Table

from agent.autonomy.budget import BudgetManager
from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.models import SKILL_RETIRED, Skill
from agent.llm.base import BudgetedLLM, ChatMessage
from agent.llm.deepseek import DeepSeekProvider
from agent.llm.recorder import DBCostRecorder
from agent.skills.benchmark import evaluate_and_maybe_promote
from agent.skills.registry import get_skill_by_name, list_skills, set_skill_status

skills_app = typer.Typer(help="Skills: list, show, test (benchmark), disable.")


def _guard(exc: Exception) -> None:
    err_console.print(f"[red]skills error:[/] {type(exc).__name__}: {exc}")
    raise typer.Exit(code=1) from None


@skills_app.command("ls")
def skills_ls(
    status: str | None = typer.Option(None, "--status"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List skills with status and pass rate."""
    s = get_settings()

    async def _run() -> list[dict[str, object]]:
        async with session_scope(s.postgres_dsn) as session:
            skills = await list_skills(session, status=status)
            return [
                {
                    "id": sk.id,
                    "name": sk.name,
                    "status": sk.status,
                    "pass_rate": round(sk.pass_rate, 3),
                    "runs": sk.runs,
                }
                for sk in skills
            ]

    try:
        rows = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if json_out:
        emit_json({"skills": rows})
        return
    table = Table(title="skills")
    for col in ("id", "name", "status", "pass_rate", "runs"):
        table.add_column(col)
    for r in rows:
        color = {"active": "green", "probation": "yellow", "retired": "red"}.get(
            str(r["status"]), "white"
        )
        table.add_row(
            str(r["id"]), str(r["name"]), f"[{color}]{r['status']}[/]",
            str(r["pass_rate"]), str(r["runs"]),
        )
    console.print(table)


@skills_app.command("show")
def skills_show(
    name: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show a skill: body, stats, and benchmarks."""
    s = get_settings()

    async def _run() -> dict[str, object] | None:
        async with session_scope(s.postgres_dsn) as session:
            sk = await get_skill_by_name(session, name)
            if sk is None:
                return None
            return {
                "id": sk.id,
                "name": sk.name,
                "status": sk.status,
                "pass_rate": round(sk.pass_rate, 3),
                "runs": sk.runs,
                "created_from_trace": sk.created_from_trace,
                "body": sk.body,
                "benchmarks": [
                    {"id": b.id, "origin": b.origin, "gating": b.gating, "checker": b.checker}
                    for b in sk.benchmarks
                ],
            }

    try:
        data = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if data is None:
        err_console.print(f"[red]no skill '{name}'[/]")
        raise typer.Exit(code=1)
    if json_out:
        emit_json(data)
        return
    for k, v in data.items():
        console.print(f"[cyan]{k}[/]: {v}")


@skills_app.command("test")
def skills_test(
    name: str = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run a skill's benchmarks; promote only if external ones pass."""
    s = get_settings()

    async def _run() -> dict[str, object] | None:
        redis: redis_asyncio.Redis[str] = redis_asyncio.from_url(
            s.redis_url, decode_responses=True
        )
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

        async def skill_solver(skill: Skill, prompt: str) -> str:
            result = await llm.complete(
                [
                    ChatMessage(
                        role="system",
                        content=f"Apply this skill:\n{skill.body}\n"
                        "Answer with ONLY the requested value.",
                    ),
                    ChatMessage(role="user", content=prompt),
                ],
                max_tokens=128,
            )
            return result.text

        try:
            async with session_scope(s.postgres_dsn) as session:
                sk = await get_skill_by_name(session, name)
                if sk is None:
                    return None
                pr = await evaluate_and_maybe_promote(session, sk, skill_solver)
                return {
                    "skill": sk.name,
                    "status": sk.status,
                    "promoted": pr.promoted,
                    "reason": pr.reason,
                    "overall_pass_rate": round(pr.overall_pass_rate, 3),
                    "external_pass_rate": round(pr.external_pass_rate, 3),
                    "external_count": pr.external_count,
                }
        finally:
            await provider.aclose()
            await redis.aclose()  # type: ignore[attr-defined]

    try:
        data = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if data is None:
        err_console.print(f"[red]no skill '{name}'[/]")
        raise typer.Exit(code=1)
    if json_out:
        emit_json(data)
        return
    console.print(data)


@skills_app.command("disable")
def skills_disable(name: str = typer.Argument(...)) -> None:
    """Disable a skill (status=retired — not deleted)."""
    s = get_settings()

    async def _run() -> str | None:
        async with session_scope(s.postgres_dsn) as session:
            sk = await get_skill_by_name(session, name)
            if sk is None:
                return None
            await set_skill_status(session, sk.id, SKILL_RETIRED)
            return sk.name

    try:
        got = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if got is None:
        err_console.print(f"[red]no skill '{name}'[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]disabled[/] skill '{got}' → retired")
