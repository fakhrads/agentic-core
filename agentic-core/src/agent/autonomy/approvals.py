"""Approval queue for APPROVE-tier actions (spec §10).

When the single gate classifies an action as APPROVE, the action is not run —
an Approval row is created and the operator decides via `agent approve/reject`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.autonomy.tiers import Tier, gate
from agent.db.base import utcnow
from agent.db.models import APPROVAL_APPROVED, APPROVAL_PENDING, APPROVAL_REJECTED, Approval
from agent.logging import get_logger

log = get_logger("approvals")


class ApprovalError(Exception):
    pass


async def request_approval(
    session: AsyncSession, *, action_kind: str, payload: dict[str, Any]
) -> Approval:
    """Create a pending approval. The caller has already decided (via `gate`)
    that this action is APPROVE-tier."""
    decision = await gate(action_kind)
    if decision.tier is not Tier.APPROVE:
        raise ApprovalError(
            f"{action_kind} is tier {decision.tier}, not APPROVE — no approval needed"
        )
    approval = Approval(
        action_kind=action_kind,
        payload=payload,
        tier=decision.tier.value,
        status=APPROVAL_PENDING,
    )
    session.add(approval)
    await session.flush()
    log.info("approval_requested", id=approval.id, action_kind=action_kind)
    return approval


async def get_approval(session: AsyncSession, approval_id: int) -> Approval | None:
    return await session.get(Approval, approval_id)


async def list_pending(session: AsyncSession, *, limit: int = 20) -> list[Approval]:
    stmt = (
        select(Approval)
        .where(Approval.status == APPROVAL_PENDING)
        .order_by(Approval.requested_at.asc())
        .limit(limit)
    )
    result = await session.scalars(stmt)
    return list(result.all())


async def decide_approval(
    session: AsyncSession, approval_id: int, *, approved: bool
) -> Approval | None:
    approval = await session.get(Approval, approval_id)
    if approval is None:
        return None
    if approval.status != APPROVAL_PENDING:
        raise ApprovalError(f"approval {approval_id} already {approval.status}")
    approval.status = APPROVAL_APPROVED if approved else APPROVAL_REJECTED
    approval.decided_at = utcnow()
    await session.flush()
    log.info("approval_decided", id=approval_id, status=approval.status)
    return approval
