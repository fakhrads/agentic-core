"""Cost recorders — persist every LLM attempt.

DBCostRecorder writes each attempt in its own short transaction, so cost is
recorded even if the surrounding episode later fails or rolls back.
"""

from __future__ import annotations

from agent.db.base import session_scope
from agent.db.repo import record_llm_call
from agent.llm.base import Attempt


class DBCostRecorder:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def record(self, attempt: Attempt, trace_id: str | None) -> None:
        async with session_scope(self._dsn) as session:
            await record_llm_call(
                session,
                trace_id=trace_id,
                provider=attempt.provider,
                model=attempt.model,
                tok_in=attempt.tok_in,
                tok_out=attempt.tok_out,
                cost_usd=attempt.cost_usd,
                ok=attempt.ok,
                error=attempt.error,
            )


class NullCostRecorder:
    """No-op recorder for contexts without a DB (e.g. some tests)."""

    async def record(self, attempt: Attempt, trace_id: str | None) -> None:
        return None
