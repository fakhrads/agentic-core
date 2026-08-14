"""Evolution CLI: `agent regression run|history`.

`run` executes the fixed suite through the real agent LLM, records the result,
detects drift against history, and — per spec §7 — flips drift-pause on a
significant drop (≥2 tasks, or a 3x-streak regression).
"""

from __future__ import annotations

import redis.asyncio as redis_asyncio
import typer
from rich.table import Table

from agent.autonomy.budget import BudgetManager
from agent.bus.streams import EventBus
from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.repo import list_regression_runs
from agent.evolution.drift import DriftState
from agent.evolution.regression import (
    DriftVerdict,
    SuiteResult,
    execute_regression,
)
from agent.llm.base import BudgetedLLM, ChatMessage
from agent.llm.deepseek import DeepSeekProvider
from agent.llm.recorder import DBCostRecorder

regression_app = typer.Typer(help="Regression suite: run and inspect history.")

_EVAL_SYSTEM_PROMPT = (
    "You are being evaluated on a fixed benchmark. Answer with ONLY the exact "
    "requested value — no explanation, no punctuation, no extra words."
)


async def _do_run(suite_name: str) -> tuple[SuiteResult, DriftVerdict]:
    s = get_settings()
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
        redis,
        default_tokens=s.budget_tokens,
        default_cost_usd=s.budget_cost_usd,
        default_actions=s.budget_actions,
    )
    llm = BudgetedLLM(provider, budget, DBCostRecorder(s.postgres_dsn))
    bus = EventBus(redis)
    drift = DriftState(redis)

    async def solver(prompt: str) -> str:
        result = await llm.complete(
            [
                ChatMessage(role="system", content=_EVAL_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ],
            max_tokens=64,
        )
        return result.text

    try:
        async with session_scope(s.postgres_dsn) as session:
            return await execute_regression(
                session, solver, suite=suite_name, drift_state=drift, bus=bus
            )
    finally:
        await provider.aclose()
        await redis.aclose()  # type: ignore[attr-defined]


@regression_app.command("run")
def regression_run(
    suite: str = typer.Option("regression", "--suite", help="regression|domain"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run the fixed suite through the agent and record the result."""
    try:
        result, verdict = run_async(_do_run(suite))
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]regression error:[/] {type(exc).__name__}: {exc}")
        err_console.print("[dim]Needs redis, postgres, and the LLM reachable.[/]")
        raise typer.Exit(code=1) from None

    if json_out:
        emit_json(
            {
                "suite": result.suite,
                "passed": result.passed,
                "total": result.total,
                "dropped": verdict.dropped,
                "newly_failing": verdict.newly_failing,
                "should_pause": verdict.should_pause,
            }
        )
    else:
        color = "green" if verdict.dropped == 0 else "red"
        console.print(
            f"[{color}]{result.passed}/{result.total}[/] passed on '{result.suite}'"
        )
        if verdict.newly_failing:
            console.print(f"[red]newly failing:[/] {', '.join(verdict.newly_failing)}")
        if verdict.should_pause:
            console.print("[bold red]⚠ drift-pause engaged[/] (NOTIFY/APPROVE held)")
        elif verdict.note:
            console.print(f"[yellow]{verdict.note}[/]")

    if verdict.should_pause:
        raise typer.Exit(code=1)


@regression_app.command("history")
def regression_history(
    suite: str = typer.Option("regression", "--suite"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show past runs, flagging drops."""
    s = get_settings()

    async def _run() -> list[dict[str, object]]:
        async with session_scope(s.postgres_dsn) as session:
            runs = await list_regression_runs(session, suite=suite, limit=20)
            return [
                {
                    "at": r.at.isoformat() if r.at else None,
                    "passed": r.passed,
                    "total": r.total,
                    "dropped": (r.detail or {}).get("dropped", 0),
                    "should_pause": (r.detail or {}).get("should_pause", False),
                }
                for r in runs
            ]

    try:
        rows = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]regression error:[/] {type(exc).__name__}: {exc}")
        raise typer.Exit(code=1) from None

    if json_out:
        emit_json({"runs": rows})
        return
    table = Table(title=f"regression history: {suite}")
    for col in ("at", "score", "dropped", "pause"):
        table.add_column(col)
    for r in rows:
        flag = "⚠" if r["should_pause"] else ""
        table.add_row(
            str(r["at"]), f"{r['passed']}/{r['total']}", str(r["dropped"]), flag
        )
    console.print(table)
