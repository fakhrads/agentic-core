"""Playbook revision + rollback.

Every revision records a diff + rationale and, per spec §7, triggers a
regression run — a playbook edit is exactly the kind of behaviour change that
can misevolve the agent. Rollback restores an exact prior content snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.models import PlaybookRev
from agent.db.repo import record_change_event
from agent.evolution.regression import DriftVerdict, Solver, execute_regression
from agent.logging import get_logger
from agent.playbook.store import PlaybookStore, unified_diff

if TYPE_CHECKING:
    from agent.bus.streams import EventBus
    from agent.evolution.drift import DriftState

log = get_logger("playbook")


async def revise(
    session: AsyncSession,
    store: PlaybookStore,
    *,
    file: str,
    new_content: str,
    rationale: str,
) -> PlaybookRev:
    old = store.read(file)
    diff = unified_diff(old, new_content, file)
    store.write(file, new_content)
    rev = PlaybookRev(
        file=file, diff=diff, rationale=rationale, content=new_content, reverted_bool=False
    )
    session.add(rev)
    await session.flush()
    await record_change_event(session, kind="playbook", ref_id=str(rev.id))
    log.info("playbook_revised", file=file, rev_id=rev.id)
    return rev


async def rollback(
    session: AsyncSession, store: PlaybookStore, rev_id: int
) -> PlaybookRev | None:
    """Restore the file to `rev_id`'s content, marking later revs reverted."""
    target = await session.get(PlaybookRev, rev_id)
    if target is None:
        return None

    old = store.read(target.file)
    store.write(target.file, target.content)

    # Mark revisions of this file newer than the target as reverted.
    newer = await session.scalars(
        select(PlaybookRev).where(
            PlaybookRev.file == target.file, PlaybookRev.id > rev_id
        )
    )
    for r in newer.all():
        r.reverted_bool = True

    new_rev = PlaybookRev(
        file=target.file,
        diff=unified_diff(old, target.content, target.file),
        rationale=f"rollback to rev {rev_id}",
        content=target.content,
        reverted_bool=False,
    )
    session.add(new_rev)
    await session.flush()
    await record_change_event(session, kind="playbook", ref_id=str(new_rev.id))
    log.info("playbook_rolled_back", file=target.file, to_rev=rev_id, new_rev=new_rev.id)
    return new_rev


async def revise_and_verify(
    session: AsyncSession,
    store: PlaybookStore,
    solver: Solver,
    *,
    file: str,
    new_content: str,
    rationale: str,
    drift_state: DriftState | None = None,
    bus: EventBus | None = None,
) -> tuple[PlaybookRev, DriftVerdict]:
    """Revise the playbook, then run the regression suite (spec §7).

    A drop engages drift-pause; recovery is a manual `agent playbook rollback`.
    """
    rev = await revise(
        session, store, file=file, new_content=new_content, rationale=rationale
    )
    _result, verdict = await execute_regression(
        session, solver, drift_state=drift_state, bus=bus
    )
    return rev, verdict


async def list_revisions(
    session: AsyncSession, *, file: str | None = None, limit: int = 20
) -> list[PlaybookRev]:
    stmt = select(PlaybookRev).order_by(PlaybookRev.at.desc()).limit(limit)
    if file is not None:
        stmt = stmt.where(PlaybookRev.file == file)
    result = await session.scalars(stmt)
    return list(result.all())
