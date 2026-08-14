"""Core ORM models.

M2 defines the audit spine — `episode` and `step`. Later milestones add their
own models (memory_item, skill, benchmark, ...) against the same `Base`, so
Alembic and `create_all` see the full metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent.db.base import Base, EmbeddingType, JSONType, utcnow

# Episode lifecycle states.
EPISODE_RUNNING = "running"
EPISODE_DONE = "done"
EPISODE_FAILED = "failed"


class Episode(Base):
    """One episode == one trace_id == one autonomous "unit of work"."""

    __tablename__ = "episode"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Business key: one UUID per episode (contract §4). Unique + indexed.
    trace_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=EPISODE_RUNNING)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Graded human feedback (spec §9) — reward signal, not just approve/reject.
    human_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    steps: Mapped[list[Step]] = relationship(
        back_populates="episode",
        order_by="Step.idx",
        cascade="all, delete-orphan",
    )


class Step(Base):
    """A single action within an episode: plan, tool call, reply, etc."""

    __tablename__ = "step"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    input: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    episode: Mapped[Episode] = relationship(back_populates="steps")


class LLMCall(Base):
    """One LLM API attempt — recorded even when it failed, so budget accounting
    never under-reports retries (spec §3)."""

    __tablename__ = "llm_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    tok_in: Mapped[int] = mapped_column(Integer, default=0)
    tok_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RegressionRun(Base):
    """One execution of a fixed suite. History is how misevolution is detected
    (spec §7) — never auto-modified, never fed to the LLM as learning material."""

    __tablename__ = "regression_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    suite: Mapped[str] = mapped_column(String(32), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    passed: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class ChangeEvent(Base):
    """A behaviour-affecting change. drift.py (M10) correlates regression drops
    with these to rank suspects."""

    __tablename__ = "change_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # playbook|skill|tool|memory
    ref_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class PlaybookRev(Base):
    """One playbook revision. `content` (full post-image) is kept so rollback is
    exact and reliable — an extension over the spec's diff-only schema."""

    __tablename__ = "playbook_rev"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file: Mapped[str] = mapped_column(String(32), index=True)
    diff: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reverted_bool: Mapped[bool] = mapped_column(Boolean, default=False)


# Goal origins + statuses (spec §6).
GOAL_ORIGIN_USER = "user"
GOAL_ORIGIN_SELF = "self"
GOAL_ORIGIN_FOLLOWUP = "followup"

GSTATUS_OPEN = "open"
GSTATUS_PROBING = "probing"
GSTATUS_ACTIVE = "active"
GSTATUS_DONE = "done"
GSTATUS_DROPPED = "dropped"
GSTATUS_INFEASIBLE = "infeasible"


class Goal(Base):
    """A goal in the stack. Self-generated goals pass a feasibility probe before
    entering the curriculum (spec §6); user goals go straight to active."""

    __tablename__ = "goal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16))  # user|self|followup
    parent_goal_id: Mapped[int | None] = mapped_column(
        ForeignKey("goal.id", ondelete="SET NULL"), nullable=True
    )
    # Sub-goal recursion guard (max depth 2, spec §6).
    depth: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default=GSTATUS_OPEN, index=True)
    probe_result: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Skill statuses + benchmark origins (spec §8/§9).
SKILL_PROBATION = "probation"
SKILL_ACTIVE = "active"
SKILL_RETIRED = "retired"
SKILL_ARCHIVED = "archived"

BM_EXTERNAL = "external"
BM_SELF = "self"


class Skill(Base):
    """A distilled reusable procedure. Promotion to `active` requires passing an
    EXTERNAL benchmark suite (spec §8) — self-benchmarks never gate."""

    __tablename__ = "skill"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    body: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType, nullable=True)
    created_from_trace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=SKILL_PROBATION, index=True)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    benchmarks: Mapped[list[Benchmark]] = relationship(
        back_populates="skill", cascade="all, delete-orphan"
    )


class Benchmark(Base):
    """A checkable task. INVARIANT (spec §8): gating=true ONLY for external
    origin — enforced as a CHECK constraint, not just convention."""

    __tablename__ = "benchmark"
    __table_args__ = (
        # Naming convention prepends "ck_benchmark_" → ck_benchmark_gating_external.
        CheckConstraint("NOT gating OR origin = 'external'", name="gating_external"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skill_id: Mapped[int | None] = mapped_column(
        ForeignKey("skill.id", ondelete="CASCADE"), nullable=True, index=True
    )
    prompt: Mapped[str] = mapped_column(Text)
    expected: Mapped[str] = mapped_column(Text)
    checker: Mapped[str] = mapped_column(String(16))  # exact|regex|pytest
    origin: Mapped[str] = mapped_column(String(16))  # external|self
    gating: Mapped[bool] = mapped_column(Boolean, default=False)

    skill: Mapped[Skill | None] = relationship(back_populates="benchmarks")


ARTEFACT_MEMORY = "memory"
ARTEFACT_SKILL = "skill"


class EpisodeArtefact(Base):
    """Records which memory/skill artefacts an episode used, so graded human
    review (spec §9) can distribute reward to exactly those artefacts."""

    __tablename__ = "episode_artefact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # memory|skill
    ref_id: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"


class Approval(Base):
    """A blocked APPROVE-tier action awaiting a human decision (spec §10).
    The first real user is tool registration (tool forge, M11)."""

    __tablename__ = "approval"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_kind: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    tier: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=APPROVAL_PENDING, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


NEED_OPEN = "open"
NEED_FORGED = "forged"
NEED_DISMISSED = "dismissed"


class ToolNeed(Base):
    """A capability gap surfaced by a trajectory — the model called a tool that
    doesn't exist (contract §2.4 `requested_by_trace`). The night shift forges
    open needs into APPROVE-gated registrations."""

    __tablename__ = "tool_need"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text)
    args_sample: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    requested_by_trace: Mapped[str | None] = mapped_column(String(64), nullable=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=NEED_OPEN, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
