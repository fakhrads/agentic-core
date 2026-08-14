"""Tools backend HTTP client (contract v1).

Separate from the DeepSeek client — different auth, different timeout (spec §12).
Carries the service Bearer token + X-Contract-Version. Behind Traefik the token
is verified by ForwardAuth; the backend only ever sees the injected X-* headers.

Error policy:
- Transport/auth problems raise ToolTransportError: 401/403 (scope), 404 (no tool),
  422 (schema), 429 (quota), 503 (backend full).
- A controlled failure (200 `ok:false`) is NOT an error — it returns an
  InvokeResult so the LLM can read the reason.
- Retry only connect/timeout/429/502/503. Never retry 4xx (except 429) or ok:false.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from agent.logging import get_logger
from agent.tools.models import InvokeError, InvokeResult, ToolEntry

log = get_logger("tools.client")

_RETRYABLE_STATUS = {429, 502, 503}
_MAX_ATTEMPTS = 3


class ToolClientError(Exception):
    pass


class ToolTransportError(ToolClientError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class ContractMismatch(ToolClientError):
    def __init__(self, ours: int, theirs: str) -> None:
        super().__init__(f"contract version mismatch: ours={ours} theirs={theirs}")
        self.ours = ours
        self.theirs = theirs


class ToolsClient:
    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        contract_version: int,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._contract_version = contract_version
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers={
                "Authorization": f"Bearer {service_token}",
                "X-Contract-Version": str(contract_version),
            },
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _check_contract(self, resp: httpx.Response) -> None:
        theirs = resp.headers.get("X-Contract-Version")
        if theirs is not None and theirs != str(self._contract_version):
            raise ContractMismatch(self._contract_version, theirs)

    async def _request(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        last_error = "unknown"
        for attempt_no in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.request(method, url, json=json)
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt_no < _MAX_ATTEMPTS:
                    await asyncio.sleep(0.3 * 2 ** (attempt_no - 1))
                    continue
                raise ToolTransportError(0, last_error) from exc

            if resp.status_code in _RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                if attempt_no < _MAX_ATTEMPTS:
                    await asyncio.sleep(0.3 * 2 ** (attempt_no - 1))
                    continue
                raise ToolTransportError(resp.status_code, resp.text[:200])

            self._check_contract(resp)
            return resp
        raise ToolTransportError(0, last_error)

    async def list_tools(self) -> list[ToolEntry]:
        resp = await self._request("GET", "/tools")
        if resp.status_code >= 400:
            raise ToolTransportError(resp.status_code, resp.text[:200])
        data = resp.json()
        return [ToolEntry.model_validate(t) for t in data.get("tools", [])]

    async def invoke(
        self,
        name: str,
        *,
        input: dict[str, Any],
        trace_id: str,
        idempotency_key: str | None = None,
        mode: str = "sync",
    ) -> InvokeResult:
        body = {
            "input": input,
            "trace_id": trace_id,
            "idempotency_key": idempotency_key,
            "mode": mode,
        }
        resp = await self._request("POST", f"/tools/{name}/invoke", json=body)

        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return InvokeResult(
                    ok=True,
                    output=data.get("output"),
                    tool_version=data.get("tool_version"),
                    duration_ms=data.get("duration_ms"),
                    sandboxed=data.get("sandboxed"),
                )
            # Controlled failure — a signal, not an exception.
            err = data.get("error", {})
            return InvokeResult(
                ok=False,
                error=InvokeError(
                    code=err.get("code", "RUNTIME"),
                    message=err.get("message", ""),
                    retryable=bool(err.get("retryable", False)),
                ),
            )

        # 401/403/404/422 and any other non-200 → transport error.
        raise ToolTransportError(resp.status_code, resp.text[:200])

    async def feedback(
        self, name: str, *, trace_id: str, helpful: bool, note: str = ""
    ) -> None:
        resp = await self._request(
            "POST",
            f"/tools/{name}/feedback",
            json={"trace_id": trace_id, "helpful": helpful, "note": note},
        )
        if resp.status_code >= 400:
            raise ToolTransportError(resp.status_code, resp.text[:200])
