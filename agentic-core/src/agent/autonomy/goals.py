"""Goal stack + feasibility probe (spec §6).

Self-generated goals are cheap to generate and expensive to pursue blindly:
many are already mastered or plainly infeasible. Each non-user goal runs one
cheap probe first, then lands in:
    done        — already mastered, do not enter the curriculum
    active      — partial progress → the real learning zone
    infeasible  — no correct progress (optionally broken into sub-goals)

The probe uses a self-assessment marker. That is fine because a probe is an
exploration signal, not a gate — gating uses only signals the agent cannot
fabricate (spec Prinsip 6), which is why probes never unlock skills.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.db.models import (
    GOAL_ORIGIN_FOLLOWUP,
    GOAL_ORIGIN_USER,
    GSTATUS_ACTIVE,
    GSTATUS_DONE,
    GSTATUS_DROPPED,
    GSTATUS_INFEASIBLE,
    GSTATUS_OPEN,
    GSTATUS_PROBING,
    Goal,
)

MAX_SUBGOAL_DEPTH = 2

Prober = Callable[[str], Awaitable[str]]

PROBE_PROMPT = (
    "Attempt the following task as best you can, briefly.\n"
    "Task: {goal}\n\n"
    "After your attempt, on the FINAL line output exactly one word:\n"
    "SOLVED if you fully solved it, PARTIAL if you made partial but real "
    "progress, or STUCK if you could not make any correct progress."
)

_MARKER_TO_STATUS = {
    "SOLVED": GSTATUS_DONE,
    "PARTIAL": GSTATUS_ACTIVE,
    "STUCK": GSTATUS_INFEASIBLE,
}


@dataclass(slots=True)
class ProbeClass:
    marker: str
    status: str
    note: str


def classify_probe(answer: str) -> ProbeClass:
    """Parse the trailing self-assessment marker. Default STUCK if absent."""
    marker = "STUCK"
    for line in reversed(answer.strip().splitlines()):
        token = line.strip().upper()
        for candidate in ("SOLVED", "PARTIAL", "STUCK"):
            if candidate in token:
                marker = candidate
                break
        else:
            continue
        break
    status = _MARKER_TO_STATUS[marker]
    notes = {
        "SOLVED": "already mastered — excluded from curriculum",
        "PARTIAL": "partial progress — real learning zone",
        "STUCK": "no correct progress",
    }
    return ProbeClass(marker=marker, status=status, note=notes[marker])


async def create_goal(
    session: AsyncSession,
    *,
    text: str,
    origin: str,
    parent_goal_id: int | None = None,
    depth: int = 0,
    priority: int = 0,
) -> Goal:
    """User goals skip the probe (straight to active); others start open."""
    status = GSTATUS_ACTIVE if origin == GOAL_ORIGIN_USER else GSTATUS_OPEN
    goal = Goal(
        text=text,
        origin=origin,
        parent_goal_id=parent_goal_id,
        depth=depth,
        priority=priority,
        status=status,
    )
    session.add(goal)
    await session.flush()
    return goal


async def get_goal(session: AsyncSession, goal_id: int) -> Goal | None:
    return await session.get(Goal, goal_id)


async def list_goals(
    session: AsyncSession, *, status: str | None = None, limit: int = 100
) -> list[Goal]:
    stmt = select(Goal).order_by(Goal.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Goal.status == status)
    result = await session.scalars(stmt)
    return list(result.all())


async def open_self_goals(session: AsyncSession, limit: int) -> list[Goal]:
    """Open, self-generated goals awaiting a probe (origin != user)."""
    stmt = (
        select(Goal)
        .where(Goal.status == GSTATUS_OPEN, Goal.origin != GOAL_ORIGIN_USER)
        .order_by(Goal.priority.desc(), Goal.created_at.asc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def drop_goal(session: AsyncSession, goal_id: int) -> Goal | None:
    goal = await session.get(Goal, goal_id)
    if goal is None:
        return None
    goal.status = GSTATUS_DROPPED
    await session.flush()
    return goal


async def probe_goal(session: AsyncSession, goal: Goal, prober: Prober) -> Goal:
    """Run one cheap attempt and classify the goal's feasibility."""
    goal.status = GSTATUS_PROBING
    await session.flush()

    answer = await prober(PROBE_PROMPT.format(goal=goal.text))
    pc = classify_probe(answer)
    goal.probe_result = {
        "marker": pc.marker,
        "note": pc.note,
        "excerpt": answer.strip()[:500],
    }
    goal.status = pc.status
    await session.flush()
    return goal


async def break_into_subgoals(
    session: AsyncSession, goal: Goal, subtexts: list[str]
) -> list[Goal]:
    """Spawn child goals for an infeasible goal, respecting the depth cap."""
    if goal.depth >= MAX_SUBGOAL_DEPTH:
        return []
    children: list[Goal] = []
    for text in subtexts:
        child = await create_goal(
            session,
            text=text,
            origin=GOAL_ORIGIN_FOLLOWUP,
            parent_goal_id=goal.id,
            depth=goal.depth + 1,
        )
        children.append(child)
    return children
