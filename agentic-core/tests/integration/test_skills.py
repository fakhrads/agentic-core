"""Skill registry, gating invariant, and external-only promotion (sqlite)."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent.db.base import Base
from agent.db.models import BM_EXTERNAL, BM_SELF, SKILL_ACTIVE, SKILL_PROBATION, Benchmark, Skill
from agent.skills.benchmark import evaluate_and_maybe_promote
from agent.skills.registry import (
    SkillError,
    add_benchmark,
    create_skill,
    list_benchmarks,
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # sqlite ignores CHECK constraints unless foreign_keys/enforcement is on;
    # CHECK is enforced by default in modern sqlite. Ensure it is.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _skill(session: AsyncSession, name: str = "csv_diff") -> Skill:
    return await create_skill(session, name=name, body="do the thing", created_from_trace="t")


async def test_gating_requires_external_code_guard(session: AsyncSession) -> None:
    sk = await _skill(session)
    with pytest.raises(SkillError):
        await add_benchmark(
            session, skill_id=sk.id, prompt="p", expected="e",
            checker="exact", origin=BM_SELF, gating=True,
        )


async def test_gating_requires_external_db_check(session: AsyncSession) -> None:
    sk = await _skill(session)
    # Bypass the code guard: insert directly, the DB CHECK must reject it.
    session.add(
        Benchmark(
            skill_id=sk.id, prompt="p", expected="e", checker="exact",
            origin=BM_SELF, gating=True,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def _solver_all_pass(skill: Skill, prompt: str) -> str:
    return "42"


async def _solver_all_fail(skill: Skill, prompt: str) -> str:
    return "wrong"


async def test_no_external_benchmark_stays_probation(session: AsyncSession) -> None:
    sk = await _skill(session)
    await add_benchmark(
        session, skill_id=sk.id, prompt="p", expected="42",
        checker="exact", origin=BM_SELF, gating=False,
    )
    await session.commit()

    pr = await evaluate_and_maybe_promote(session, sk, _solver_all_pass)
    assert pr.promoted is False
    assert "no external benchmark" in pr.reason
    assert sk.status == SKILL_PROBATION
    # Self benchmark still updates the pass_rate stat.
    assert sk.pass_rate == 1.0


async def test_external_pass_promotes(session: AsyncSession) -> None:
    sk = await _skill(session)
    await add_benchmark(
        session, skill_id=sk.id, prompt="p", expected="42",
        checker="exact", origin=BM_EXTERNAL, gating=True,
    )
    await session.commit()

    pr = await evaluate_and_maybe_promote(session, sk, _solver_all_pass)
    assert pr.promoted is True
    assert sk.status == SKILL_ACTIVE
    assert sk.last_tested_at is not None


async def test_external_fail_stays_probation(session: AsyncSession) -> None:
    sk = await _skill(session)
    await add_benchmark(
        session, skill_id=sk.id, prompt="p", expected="42",
        checker="exact", origin=BM_EXTERNAL, gating=True,
    )
    await session.commit()

    pr = await evaluate_and_maybe_promote(session, sk, _solver_all_fail)
    assert pr.promoted is False
    assert sk.status == SKILL_PROBATION


async def test_list_benchmarks_gating_only(session: AsyncSession) -> None:
    sk = await _skill(session)
    await add_benchmark(
        session, skill_id=sk.id, prompt="p", expected="e",
        checker="exact", origin=BM_SELF, gating=False,
    )
    await add_benchmark(
        session, skill_id=sk.id, prompt="p2", expected="e2",
        checker="exact", origin=BM_EXTERNAL, gating=True,
    )
    gating = await list_benchmarks(session, sk.id, gating_only=True)
    assert len(gating) == 1 and gating[0].gating is True
