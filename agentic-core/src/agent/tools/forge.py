"""Tool forge (spec §2.4 + M11).

Generate a single-module Python tool plus mandatory pytest, then register it —
but registration is APPROVE-tier: it goes through the approval queue, and only a
human `agent approve` submits it to the tools backend. The forge uses a SEPARATE
register-scoped token (never the agent's invoke token).

Backend flow on register: build sandbox → run tests → all pass → probation;
any test fails → 422, tool not registered. There is no register-without-tests.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from agent.autonomy.approvals import request_approval
from agent.db.models import Approval
from agent.logging import get_logger
from agent.tools.client import ToolTransportError

log = get_logger("tools.forge")

ACTION_TOOL_REGISTER = "tool.register"


class ForgeError(Exception):
    pass


@dataclass(slots=True)
class ForgeArtifact:
    name: str
    description: str
    params_schema: dict[str, Any]
    code: str  # Python source (single module)
    tests: str  # pytest source (mandatory)
    runtime: str = "python3.12"

    def to_submission(self, requested_by_trace: str) -> dict[str, Any]:
        """Exact POST /tools body (contract §2.4)."""
        return {
            "name": self.name,
            "description": self.description,
            "params_schema": self.params_schema,
            "runtime": self.runtime,
            "code": base64.b64encode(self.code.encode()).decode(),
            "tests": base64.b64encode(self.tests.encode()).decode(),
            "requested_by_trace": requested_by_trace,
        }


def parse_forge_json(text: str) -> ForgeArtifact:
    """Extract a ForgeArtifact from an LLM response containing a JSON object."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ForgeError("no JSON object found in generator output")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ForgeError(f"invalid JSON: {exc}") from exc

    required = ("name", "description", "params_schema", "code", "tests")
    missing = [k for k in required if k not in data]
    if missing:
        raise ForgeError(f"generator output missing keys: {missing}")
    if not str(data["tests"]).strip():
        raise ForgeError("tests are mandatory — refusing to forge without tests")
    return ForgeArtifact(
        name=str(data["name"]),
        description=str(data["description"]),
        params_schema=dict(data["params_schema"]),
        code=str(data["code"]),
        tests=str(data["tests"]),
        runtime=str(data.get("runtime", "python3.12")),
    )


# A generator turns a need description into a forge artifact (LLM in prod).
Generator = Callable[[str], Awaitable[ForgeArtifact]]

FORGE_PROMPT = (
    "Design a small Python tool for this need. Respond with ONLY a JSON object "
    "with keys: name (snake_case), description, params_schema (JSON Schema), "
    "code (a single Python module as a string), tests (pytest source as a "
    "string, MANDATORY). Need:\n{need}"
)


class ToolForge:
    def __init__(self, generator: Generator) -> None:
        self._generator = generator

    async def forge_and_request(
        self, session: AsyncSession, *, need: str, trace_id: str
    ) -> tuple[Approval, ForgeArtifact]:
        """Generate an artifact and enqueue an APPROVE request (never auto-registers)."""
        artifact = await self._generator(need)
        approval = await request_approval(
            session,
            action_kind=ACTION_TOOL_REGISTER,
            payload={"need": need, "submission": artifact.to_submission(trace_id)},
        )
        return approval, artifact


@dataclass(slots=True)
class RegisterResult:
    ok: bool
    status: str = ""
    name: str = ""
    reason: str = ""
    detail: str = ""


class ToolForgeClient:
    """Register-scoped client for POST /tools. Separate token from invoke."""

    def __init__(
        self,
        *,
        base_url: str,
        forge_token: str,
        contract_version: int,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers={
                "Authorization": f"Bearer {forge_token}",
                "X-Contract-Version": str(contract_version),
            },
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def register(self, submission: dict[str, Any]) -> RegisterResult:
        resp = await self._client.post("/tools", json=submission)
        if resp.status_code in (200, 201):
            data = resp.json()
            return RegisterResult(
                ok=True, status=data.get("status", "probation"), name=data.get("name", "")
            )
        if resp.status_code == 422:
            # Tests failed — tool not registered (contract §2.4).
            return RegisterResult(ok=False, reason="tests_failed", detail=resp.text[:500])
        # 401/403 (scope) and anything else → transport error.
        raise ToolTransportError(resp.status_code, resp.text[:200])
