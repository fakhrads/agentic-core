"""Skill registry — CRUD + the gating invariant.

Invariant (spec §8): a gating benchmark must be external. Enforced both by a DB
CHECK constraint and here in code, so a bug can't slip a self-graded benchmark
into the gate.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent.db.models import (
    BM_EXTERNAL,
    SKILL_PROBATION,
    Benchmark,
    Skill,
)


class SkillError(Exception):
    pass


async def create_skill(
    session: AsyncSession,
    *,
    name: str,
    body: str,
    created_from_trace: str | None = None,
    embedding: list[float] | None = None,
) -> Skill:
    skill = Skill(
        name=name,
        body=body,
        created_from_trace=created_from_trace,
        embedding=embedding,
        status=SKILL_PROBATION,
    )
    session.add(skill)
    await session.flush()
    return skill


async def get_skill_by_name(session: AsyncSession, name: str) -> Skill | None:
    stmt = (
        select(Skill).where(Skill.name == name).options(selectinload(Skill.benchmarks))
    )
    skill: Skill | None = await session.scalar(stmt)
    return skill


async def list_skills(
    session: AsyncSession, *, status: str | None = None, limit: int = 100
) -> list[Skill]:
    stmt = select(Skill).order_by(Skill.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Skill.status == status)
    result = await session.scalars(stmt)
    return list(result.all())


async def set_skill_status(
    session: AsyncSession, skill_id: int, status: str
) -> Skill | None:
    skill = await session.get(Skill, skill_id)
    if skill is None:
        return None
    skill.status = status
    await session.flush()
    return skill


async def add_benchmark(
    session: AsyncSession,
    *,
    skill_id: int | None,
    prompt: str,
    expected: str,
    checker: str,
    origin: str,
    gating: bool = False,
) -> Benchmark:
    if gating and origin != BM_EXTERNAL:
        raise SkillError("gating=true requires origin='external' (spec §8)")
    bench = Benchmark(
        skill_id=skill_id,
        prompt=prompt,
        expected=expected,
        checker=checker,
        origin=origin,
        gating=gating,
    )
    session.add(bench)
    await session.flush()
    return bench


async def list_benchmarks(
    session: AsyncSession, skill_id: int, *, gating_only: bool = False
) -> list[Benchmark]:
    stmt = select(Benchmark).where(Benchmark.skill_id == skill_id)
    if gating_only:
        stmt = stmt.where(Benchmark.gating.is_(True))
    result = await session.scalars(stmt)
    return list(result.all())
