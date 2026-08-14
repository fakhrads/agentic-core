"""Single autonomy gate (spec §10).

Every action passes through `gate()`. A path that acts without it is an
architectural bug. Tiers:
  AUTO    → run immediately (reads, retrieval, replies, benchmarks, probes)
  NOTIFY  → run, then enqueue a notification (promote memory, distill, revise)
  APPROVE → block until `agent approve` (register tool, message others, spend, infra)

During drift-pause (M10) NOTIFY is treated as APPROVE. M3 only exercises AUTO.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Tier(StrEnum):
    AUTO = "AUTO"
    NOTIFY = "NOTIFY"
    APPROVE = "APPROVE"


# Known action kinds → tier. Unknown kinds default to APPROVE (fail-safe).
_ACTION_TIERS: dict[str, Tier] = {
    "chat.reply": Tier.AUTO,
    "chat.send": Tier.AUTO,
    "memory.write_quarantine": Tier.AUTO,
    "benchmark.run": Tier.AUTO,
    "goal.probe": Tier.AUTO,
    "tool.invoke": Tier.AUTO,
    "memory.promote": Tier.NOTIFY,
    "skill.distill": Tier.NOTIFY,
    "playbook.revise": Tier.NOTIFY,
    "archive.resample": Tier.NOTIFY,
    "tool.register": Tier.APPROVE,
    "message.external": Tier.APPROVE,
    "infra.touch": Tier.APPROVE,
}


def classify(action_kind: str) -> Tier:
    return _ACTION_TIERS.get(action_kind, Tier.APPROVE)


@dataclass(slots=True)
class GateDecision:
    allowed: bool
    tier: Tier
    action_kind: str
    reason: str = ""
    notify: bool = False


async def gate(action_kind: str, *, drift_paused: bool = False) -> GateDecision:
    """Decide whether an action may proceed now."""
    tier = classify(action_kind)
    if tier is Tier.AUTO:
        return GateDecision(True, tier, action_kind)
    if tier is Tier.NOTIFY:
        if drift_paused:
            return GateDecision(
                False, tier, action_kind, reason="drift-pause: NOTIFY held as APPROVE"
            )
        return GateDecision(True, tier, action_kind, notify=True)
    # APPROVE — blocked until a human approves (approval queue arrives M4+).
    return GateDecision(False, tier, action_kind, reason="requires approval")
