"""Playbook revise/rollback + regression-on-revise on sqlite (no LLM)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base
from agent.db.models import ChangeEvent, RegressionRun
from agent.playbook.revise import list_revisions, revise, revise_and_verify, rollback
from agent.playbook.store import MEMORY_FILE, PlaybookStore


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def store(tmp_path: Path) -> PlaybookStore:
    st = PlaybookStore(tmp_path / "playbook")
    st.ensure()
    return st


async def test_revise_writes_file_diff_and_change_event(
    session: AsyncSession, store: PlaybookStore
) -> None:
    rev = await revise(
        session, store, file=MEMORY_FILE,
        new_content="# MEMORY\n\nThe user prefers Indonesian.\n",
        rationale="learned language preference",
    )
    await session.commit()

    assert "prefers Indonesian" in store.read(MEMORY_FILE)
    assert rev.diff.startswith("---") or "+++" in rev.diff
    assert rev.rationale == "learned language preference"

    # A change_event was recorded (drift.py correlates against these).
    events = (await session.scalars(select(ChangeEvent))).all()
    assert any(e.kind == "playbook" and e.ref_id == str(rev.id) for e in events)


async def test_rollback_restores_content_and_marks_reverted(
    session: AsyncSession, store: PlaybookStore
) -> None:
    r1 = await revise(session, store, file=MEMORY_FILE, new_content="v1\n", rationale="a")
    await revise(session, store, file=MEMORY_FILE, new_content="v2-bad\n", rationale="b")
    await session.commit()
    assert store.read(MEMORY_FILE) == "v2-bad\n"

    new_rev = await rollback(session, store, r1.id)
    await session.commit()
    assert new_rev is not None
    assert store.read(MEMORY_FILE) == "v1\n"  # restored to r1's content

    # The bad revision is marked reverted.
    revs = await list_revisions(session, file=MEMORY_FILE)
    bad = next(r for r in revs if r.rationale == "b")
    assert bad.reverted_bool is True


async def test_revise_and_verify_runs_regression(
    session: AsyncSession, store: PlaybookStore, tmp_path: Path
) -> None:
    # Minimal suite file so load_suite finds tasks.
    suite_dir = Path("benchmarks/regression")  # use the shipped 20-task suite

    async def solver(_prompt: str) -> str:
        return "wrong-answer"  # everything fails, but that's fine for wiring

    _ = suite_dir  # documentation; execute_regression loads the default dir

    rev, verdict = await revise_and_verify(
        session, store, solver,
        file=MEMORY_FILE, new_content="update\n", rationale="test",
    )
    await session.commit()

    # A regression run was recorded as part of the revision, over all 20 tasks.
    run_count = await session.scalar(select(func.count(RegressionRun.id)))
    assert run_count == 1
    run = (await session.scalars(select(RegressionRun))).first()
    assert run is not None and run.total == 20
    # And the revision exists.
    assert rev.id is not None
    assert verdict.dropped == 0  # no prior baseline → nothing "dropped"
