"""Event model + stream names.

Events are the audit trail (spec §3): `agent tail`/`agent watch` are consumers
of `events:audit`, not file readers, so monitoring works from another machine.
Every event carries a trace_id (Prinsip 4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

# Stream names (contract §2.2 / spec §3).
STREAM_AUDIT = "events:audit"
STREAM_TOOL_RESULTS = "events:tool_results"
STREAM_INBOUND = "events:inbound"
STREAM_DLQ = "events:dlq"


class EventType:
    """Well-known audit event types. Free-form strings are allowed too."""

    EPISODE_STARTED = "episode.started"
    EPISODE_ENDED = "episode.ended"
    STEP_STARTED = "step.started"
    STEP_FINISHED = "step.finished"
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    NOTE = "note"
    ERROR = "error"


def _now() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    """A single audit/bus event. Serialized as JSON into one stream field."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    trace_id: str | None = None
    episode_id: str | None = None
    component: str = "agent"
    ts: datetime = Field(default_factory=_now)
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_fields(self) -> dict[str, str]:
        """Flatten to Redis stream fields (single JSON blob)."""
        return {"data": self.model_dump_json()}

    @classmethod
    def from_fields(cls, fields: dict[str, str]) -> Event:
        return cls.model_validate_json(fields["data"])

    def matches(self, trace: str | None, grep: str | None) -> bool:
        """Filter predicate used by `agent tail`."""
        if trace is not None and self.trace_id != trace:
            return False
        if grep is not None:
            haystack = f"{self.type} {self.message} {self.component} {self.payload}"
            if grep.lower() not in haystack.lower():
                return False
        return True
