import httpx
import pytest

from agent.tools.client import ContractMismatch, ToolsClient, ToolTransportError


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fast(_s: float) -> None:
        return None

    monkeypatch.setattr("agent.tools.client.asyncio.sleep", fast)


def _client(handler: object, contract: int = 1) -> ToolsClient:
    return ToolsClient(
        base_url="http://tools",
        service_token="svc_test",
        contract_version=contract,
        timeout_s=5,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


async def test_list_tools_parses_entries() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "tools": [
                    {
                        "name": "regex_explainer",
                        "version": 3,
                        "description": "explain regex",
                        "params_schema": {"type": "object", "properties": {}},
                        "status": "active",
                        "timeout_ms": 15000,
                        "cost_hint": "cheap",
                    }
                ]
            },
        )

    client = _client(handler)
    tools = await client.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "regex_explainer"
    assert tools[0].to_function_def()["function"]["name"] == "regex_explainer"
    await client.aclose()


async def test_invoke_ok_true() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/tools/echo/invoke"
        return httpx.Response(
            200,
            json={"ok": True, "output": {"echoed": 1}, "tool_version": 2,
                  "duration_ms": 12, "sandboxed": True},
        )

    client = _client(handler)
    res = await client.invoke("echo", input={"x": 1}, trace_id="t")
    assert res.ok is True
    assert res.output == {"echoed": 1}
    assert res.sandboxed is True
    await client.aclose()


async def test_invoke_ok_false_is_signal_not_exception() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": False, "error": {"code": "BAD_INPUT", "message": "nope",
                                         "retryable": False}},
        )

    client = _client(handler)
    res = await client.invoke("echo", input={}, trace_id="t")
    assert res.ok is False
    assert res.error is not None
    assert res.error.code == "BAD_INPUT"
    await client.aclose()


async def test_404_and_422_raise_transport_error() -> None:
    for status in (404, 422):
        def handler(_req: httpx.Request, _s: int = status) -> httpx.Response:
            return httpx.Response(_s, text="nope")

        client = _client(handler)
        with pytest.raises(ToolTransportError) as ei:
            await client.invoke("x", input={}, trace_id="t")
        assert ei.value.status_code == status
        await client.aclose()


async def test_retries_on_503_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True, "output": {}})

    client = _client(handler)
    res = await client.invoke("x", input={}, trace_id="t")
    assert res.ok is True
    assert calls["n"] == 3
    await client.aclose()


async def test_contract_mismatch_raises() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tools": []}, headers={"X-Contract-Version": "2"})

    client = _client(handler, contract=1)
    with pytest.raises(ContractMismatch):
        await client.list_tools()
    await client.aclose()
