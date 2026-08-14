"""Tools backend contract v1 — data models (spec §2 of the interface contract).

ToolEntry mirrors `GET /tools`; InvokeResult mirrors the sync invoke response,
including the *controlled failure* shape (`ok:false`) which is a signal for the
LLM, not an exception.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

STATUS_ACTIVE = "active"
STATUS_PROBATION = "probation"
STATUS_DISABLED = "disabled"

# Controlled failure codes (200 ok:false).
ERR_TIMEOUT = "TIMEOUT"
ERR_BAD_INPUT = "BAD_INPUT"
ERR_RUNTIME = "RUNTIME"
ERR_SANDBOX_DENIED = "SANDBOX_DENIED"


class ProbationInfo(BaseModel):
    invocations: int
    required: int
    failures: int


class ToolEntry(BaseModel):
    name: str
    version: int
    description: str
    params_schema: dict[str, Any]
    status: str
    timeout_ms: int
    cost_hint: str = "moderate"
    probation: ProbationInfo | None = None

    @property
    def is_disabled(self) -> bool:
        return self.status == STATUS_DISABLED

    @property
    def is_probation(self) -> bool:
        return self.status == STATUS_PROBATION

    def to_function_def(self) -> dict[str, Any]:
        """OpenAI/DeepSeek function-calling tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_schema,
            },
        }


class InvokeError(BaseModel):
    code: str
    message: str
    retryable: bool = False


class InvokeResult(BaseModel):
    ok: bool
    output: dict[str, Any] | None = None
    error: InvokeError | None = None
    tool_version: int | None = None
    duration_ms: int | None = None
    sandboxed: bool | None = None
    # Set by the caller from the tool's registry status — probation output must
    # not be promoted to fact in memory (spec §2.1).
    from_probation: bool = False

    def as_tool_message(self) -> str:
        """Render for feeding back to the LLM as a tool-role message."""
        if self.ok:
            return _json(self.output or {})
        err = self.error
        assert err is not None
        return _json({"error": {"code": err.code, "message": err.message}})


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str, ensure_ascii=False)
