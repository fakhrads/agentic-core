"""Dev-only helpers to exercise the bus + episode store without the real loop.

`agent dev seed` writes a dummy episode with steps to Postgres AND publishes the
matching audit events to `events:audit`, so `agent trace <id>` and `agent tail`
can be demonstrated end-to-end before M3 brings a real loop online.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import typer

from agent.bus.events import STREAM_AUDIT, Event, EventType
from agent.bus.streams import EventBus
from agent.cli._output import console, emit_json, run_async
from agent.config import get_settings
from agent.db.base import session_scope
from agent.db.models import EPISODE_DONE
from agent.db.repo import add_step, create_episode, end_episode
from agent.trace import new_trace_id

dev_app = typer.Typer(help="Dev-only utilities (not part of the operator surface).")

_DUMMY_STEPS: list[tuple[str, dict[str, Any], dict[str, Any], int, bool]] = [
    ("plan", {"goal": "explain a regex"}, {"steps": 3}, 120, True),
    ("tool_call", {"tool": "regex_explainer"}, {"ok": True}, 812, True),
    ("reply", {"text": "here is the explanation"}, {"sent": True}, 40, True),
]


async def _seed(
    fail: bool,
    *,
    bus_factory: Callable[[], EventBus] | None = None,
    dsn: str | None = None,
) -> str:
    """Seed one dummy episode. Deps are injectable so tests drive the real path.

    The bus is built inside the coroutine (single event loop) — never pass a
    live client created on a different loop.
    """
    s = get_settings()
    dsn = dsn or s.postgres_dsn
    make_bus = bus_factory or (lambda: EventBus.from_url(s.redis_url))
    trace_id = new_trace_id()
    bus = make_bus()
    try:
        async with session_scope(dsn) as session:
            ep = await create_episode(session, trace_id=trace_id, source="dev-seed")
            await bus.publish(
                STREAM_AUDIT,
                Event(
                    type=EventType.EPISODE_STARTED,
                    trace_id=trace_id,
                    episode_id=str(ep.id),
                    component="dev",
                    message="episode started",
                ),
            )
            for kind, inp, out, dur, ok in _DUMMY_STEPS:
                step_ok = ok if not fail else False
                await add_step(
                    session,
                    ep,
                    kind=kind,
                    input=inp,
                    output=out if step_ok else {"error": "seeded failure"},
                    duration_ms=dur,
                    ok=step_ok,
                )
                await bus.publish(
                    STREAM_AUDIT,
                    Event(
                        type=EventType.STEP_FINISHED,
                        trace_id=trace_id,
                        episode_id=str(ep.id),
                        component="dev",
                        message=f"{kind} {'ok' if step_ok else 'failed'}",
                        payload={"kind": kind, "duration_ms": dur},
                    ),
                )
            status = "failed" if fail else EPISODE_DONE
            await end_episode(
                session, trace_id, status=status, summary="seeded dummy episode"
            )
            await bus.publish(
                STREAM_AUDIT,
                Event(
                    type=EventType.EPISODE_ENDED,
                    trace_id=trace_id,
                    episode_id=str(ep.id),
                    component="dev",
                    message=f"episode {status}",
                ),
            )
        return trace_id
    finally:
        await bus.aclose()


async def _send_inbound(text: str, chat_id: str, channel: str) -> str:
    from agent.bus.events import STREAM_INBOUND
    from agent.channels.base import InboundMessage

    s = get_settings()
    trace_id = new_trace_id()
    bus = EventBus.from_url(s.redis_url)
    inbound = InboundMessage(channel=channel, chat_id=chat_id, text=text)
    try:
        await bus.publish(
            STREAM_INBOUND,
            Event(
                type="inbound",
                trace_id=trace_id,
                component="dev",
                payload=dict(inbound.as_dict()),
            ),
        )
    finally:
        await bus.aclose()
    return trace_id


@dev_app.command("send")
def send(
    text: str = typer.Argument(..., help="Message text to inject."),
    chat_id: str = typer.Option("1", "--chat", help="Chat id."),
    channel: str = typer.Option("dev", "--channel", help="Channel name."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Inject an inbound message onto events:inbound (a running daemon handles it)."""
    trace_id = run_async(_send_inbound(text, chat_id, channel))
    if json_out:
        emit_json({"trace_id": trace_id})
    else:
        console.print(f"[green]queued[/] trace_id=[bold]{trace_id}[/]")
        console.print(f"watch it: [cyan]agent tail --trace {trace_id}[/]")


@dev_app.command("seed")
def seed(
    fail: bool = typer.Option(False, "--fail", help="Seed a failed episode."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Create one dummy episode (DB + audit events) and print its trace_id."""
    trace_id = run_async(_seed(fail))
    if json_out:
        emit_json({"trace_id": trace_id})
    else:
        console.print(f"[green]seeded[/] trace_id=[bold]{trace_id}[/]")
        console.print(f"try: [cyan]agent trace {trace_id} --tree[/]")
