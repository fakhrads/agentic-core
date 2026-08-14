"""Skill benchmark execution + promotion gate.

Promotion probation→active happens ONLY when the skill passes its EXTERNAL
benchmarks (gating). Self-generated benchmarks are run for a progress signal
(pass_rate) but never gate — a domain with no external benchmark keeps its skill
stuck in probation (spec §8), by design.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.base import utcnow
from agent.db.models import SKILL_ACTIVE, Benchmark, Skill
from agent.evolution.regression import check_answer
from agent.logging import get_logger
from agent.skills.registry import list_benchmarks

log = get_logger("skills.benchmark")

# A skill solver applies the skill body to a prompt and returns an answer.
SkillSolver = Callable[[Skill, str], Awaitable[str]]

PROMOTE_THRESHOLD = 0.8


@dataclass(slots=True)
class BenchRun:
    total: int = 0
    passed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


@dataclass(slots=True)
class PromotionResult:
    promoted: bool
    reason: str
    overall_pass_rate: float
    external_pass_rate: float
    external_count: int


async def _run(
    skill: Skill, solver: SkillSolver, benchmarks: list[Benchmark]
) -> BenchRun:
    run = BenchRun()
    for bm in benchmarks:
        run.total += 1
        try:
            answer = await solver(skill, bm.prompt)
        except Exception as exc:  # noqa: BLE001 — solver failure = benchmark failure
            answer = f"<error: {exc}>"
        ok = check_answer(bm.checker, bm.expected, answer)
        if ok:
            run.passed += 1
        run.results.append({"id": bm.id, "gating": bm.gating, "passed": ok})
    return run


async def evaluate_and_maybe_promote(
    session: AsyncSession,
    skill: Skill,
    solver: SkillSolver,
    *,
    threshold: float = PROMOTE_THRESHOLD,
) -> PromotionResult:
    """Run all benchmarks (stat), then gate promotion on external ones only."""
    all_bms = await list_benchmarks(session, skill.id)
    overall = await _run(skill, solver, all_bms)

    # Update skill stats from the full set.
    skill.runs += overall.total
    skill.pass_rate = overall.pass_rate
    skill.last_tested_at = utcnow()

    external = [b for b in all_bms if b.gating]
    ext_run = await _run(skill, solver, external)

    if ext_run.total == 0:
        reason = "no external benchmark — stays probation (spec §8)"
        promoted = False
    elif ext_run.pass_rate >= threshold:
        skill.status = SKILL_ACTIVE
        reason = f"external pass_rate {ext_run.pass_rate:.2f} ≥ {threshold}"
        promoted = True
    else:
        reason = f"external pass_rate {ext_run.pass_rate:.2f} < {threshold}"
        promoted = False

    await session.flush()
    log.info("skill_evaluated", skill=skill.name, promoted=promoted, reason=reason)
    return PromotionResult(
        promoted=promoted,
        reason=reason,
        overall_pass_rate=overall.pass_rate,
        external_pass_rate=ext_run.pass_rate,
        external_count=ext_run.total,
    )
