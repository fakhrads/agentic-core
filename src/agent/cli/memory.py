"""Memory commands: search / show / demote / restore / archive ls / stats.

Prinsip 5: if state isn't visible from the CLI it isn't done. Demote/restore are
status changes, never deletes (Prinsip 1).
"""

from __future__ import annotations

import typer
from rich.table import Table

from agent.cli._output import console, emit_json, err_console, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.llm.ollama import OllamaProvider
from agent.memory import archive as archive_mod
from agent.memory import semantic
from agent.memory.fitness import compute_fitness
from agent.memory.models import MemoryItem
from agent.memory.retrieval import hybrid_search, mark_retrieved

memory_app = typer.Typer(help="Inspect and manage memory artefacts.")
archive_app = typer.Typer(help="Archived (permanent, non-hot) memory.")
memory_app.add_typer(archive_app, name="archive")
quarantine_app = typer.Typer(help="Quarantined external content (Prinsip 2).")
memory_app.add_typer(quarantine_app, name="quarantine")


def _guard(exc: Exception) -> None:
    err_console.print(f"[red]memory error:[/] {type(exc).__name__}: {exc}")
    err_console.print("[dim]Is postgres up + migrated? Ollama up for search?[/]")
    raise typer.Exit(code=1) from None


def _item_row(item: MemoryItem) -> dict[str, object]:
    return {
        "id": item.id,
        "tier": item.tier,
        "status": item.status,
        "source_kind": item.source_kind,
        "source": item.source,
        "fitness": round(compute_fitness(item), 3),
        "retrieval_count": item.retrieval_count,
        "content": item.content[:80],
    }


async def _search(query: str, limit: int) -> list[dict[str, object]]:
    s = get_settings()
    provider = OllamaProvider(
        base_url=s.ollama_base_url,
        model=s.ollama_probe_model,
        timeout_s=s.ollama_timeout_s,
        embed_model=s.ollama_embed_model,
    )
    try:
        vector = await provider.embed(query)
    finally:
        await provider.aclose()

    async with session_scope(s.postgres_dsn) as session:
        hits = await hybrid_search(session, vector, limit=limit)
        await mark_retrieved(session, [item for item, _ in hits])
        return [{**_item_row(item), "score": round(score, 4)} for item, score in hits]


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(...),
    limit: int = typer.Option(8, "--limit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Similarity search over active memory (embeds via Ollama)."""
    try:
        rows = run_async(_search(query, limit))
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if json_out:
        emit_json({"results": rows})
        return
    table = Table(title=f"memory search: {query}")
    for col in ("id", "score", "fitness", "status", "content"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            str(r["id"]), str(r["score"]), str(r["fitness"]),
            str(r["status"]), str(r["content"]),
        )
    console.print(table)


async def _show(item_id: int) -> dict[str, object] | None:
    s = get_settings()
    async with session_scope(s.postgres_dsn) as session:
        item = await semantic.get(session, item_id)
        if item is None:
            return None
        return {
            **_item_row(item),
            "content_full": item.content,
            "success_count": item.success_count,
            "contradiction_count": item.contradiction_count,
            "human_reward": item.human_reward,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None,
        }


@memory_app.command("show")
def memory_show(
    item_id: int = typer.Argument(...),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show a memory item: content, source, fitness, retrieval history."""
    try:
        data = run_async(_show(item_id))
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if data is None:
        err_console.print(f"[red]no memory item {item_id}[/]")
        raise typer.Exit(code=1)
    if json_out:
        emit_json(data)
        return
    for k, v in data.items():
        console.print(f"[cyan]{k}[/]: {v}")


def _status_change(item_id: int, action: str) -> None:
    s = get_settings()

    async def _run() -> str | None:
        async with session_scope(s.postgres_dsn) as session:
            fn = semantic.demote if action == "demote" else semantic.restore
            item = await fn(session, item_id)
            return item.status if item else None

    try:
        status = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if status is None:
        err_console.print(f"[red]no memory item {item_id}[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]{action}d[/] item {item_id} → status={status}")


@memory_app.command("demote")
def memory_demote(item_id: int = typer.Argument(...)) -> None:
    """active → retired (not deleted)."""
    _status_change(item_id, "demote")


@memory_app.command("restore")
def memory_restore(item_id: int = typer.Argument(...)) -> None:
    """retired → active."""
    _status_change(item_id, "restore")


@archive_app.command("ls")
def archive_ls(json_out: bool = typer.Option(False, "--json")) -> None:
    """List archived items (including previously retired)."""
    s = get_settings()

    async def _run() -> list[dict[str, object]]:
        async with session_scope(s.postgres_dsn) as session:
            items = await archive_mod.list_archived(session)
            return [_item_row(i) for i in items]

    try:
        rows = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if json_out:
        emit_json({"archived": rows})
        return
    table = Table(title="archived memory")
    for col in ("id", "tier", "fitness", "content"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), str(r["tier"]), str(r["fitness"]), str(r["content"]))
    console.print(table)


@quarantine_app.command("ls")
def quarantine_ls(json_out: bool = typer.Option(False, "--json")) -> None:
    """List quarantined external content awaiting distillation."""
    from agent.memory.quarantine import list_quarantine

    s = get_settings()

    async def _run() -> list[dict[str, object]]:
        async with session_scope(s.postgres_dsn) as session:
            items = await list_quarantine(session)
            return [
                {"id": i.id, "source": i.source, "content": i.content[:80]} for i in items
            ]

    try:
        rows = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if json_out:
        emit_json({"quarantine": rows})
        return
    table = Table(title="quarantine (external, not yet trusted)")
    for col in ("id", "source", "content"):
        table.add_column(col)
    for r in rows:
        table.add_row(str(r["id"]), str(r["source"]), str(r["content"]))
    console.print(table)


@memory_app.command("stats")
def memory_stats(json_out: bool = typer.Option(False, "--json")) -> None:
    """Counts by status/tier and average active fitness."""
    s = get_settings()

    async def _run() -> dict[str, object]:
        async with session_scope(s.postgres_dsn) as session:
            st = await semantic.stats(session)
            return {
                "total": st.total,
                "by_status": st.by_status,
                "by_tier": st.by_tier,
                "avg_fitness_active": st.avg_fitness_active,
            }

    try:
        data = run_async(_run())
    except Exception as exc:  # noqa: BLE001
        _guard(exc)
        return
    if json_out:
        emit_json(data)
        return
    console.print(data)
