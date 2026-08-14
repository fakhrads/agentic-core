"""Planner.

M3 is deliberately trivial: no tools yet, so every inbound message maps to a
single `reply` step. M4 makes this LLM-driven with tool selection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PlannedStep:
    kind: str
    detail: dict[str, str]


def plan(text: str) -> list[PlannedStep]:
    return [PlannedStep(kind="reply", detail={"text": text})]
