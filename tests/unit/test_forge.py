import base64

import httpx
import pytest

from agent.tools.client import ToolTransportError
from agent.tools.forge import (
    ForgeArtifact,
    ForgeError,
    RegisterResult,
    ToolForgeClient,
    parse_forge_json,
)


def _artifact() -> ForgeArtifact:
    return ForgeArtifact(
        name="csv_diff",
        description="diff two csvs",
        params_schema={"type": "object", "properties": {}},
        code="def run(x): return x",
        tests="def test_run(): assert True",
    )


def test_to_submission_base64_encodes_code_and_tests() -> None:
    sub = _artifact().to_submission("trace-1")
    assert sub["name"] == "csv_diff"
    assert base64.b64decode(sub["code"]).decode() == "def run(x): return x"
    assert base64.b64decode(sub["tests"]).decode() == "def test_run(): assert True"
    assert sub["requested_by_trace"] == "trace-1"


def test_parse_forge_json_extracts_artifact() -> None:
    text = (
        'here you go:\n{"name": "n", "description": "d", '
        '"params_schema": {"type": "object"}, "code": "c", "tests": "t"}\nthanks'
    )
    art = parse_forge_json(text)
    assert art.name == "n" and art.code == "c"


def test_parse_forge_json_requires_tests() -> None:
    text = '{"name": "n", "description": "d", "params_schema": {}, "code": "c", "tests": ""}'
    with pytest.raises(ForgeError):
        parse_forge_json(text)


def test_parse_forge_json_rejects_missing_keys() -> None:
    with pytest.raises(ForgeError):
        parse_forge_json('{"name": "n"}')


def test_parse_forge_json_rejects_non_json() -> None:
    with pytest.raises(ForgeError):
        parse_forge_json("no json here")


def _client(handler: object) -> ToolForgeClient:
    return ToolForgeClient(
        base_url="http://tools",
        forge_token="svc_forge",
        contract_version=1,
        timeout_s=5,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


async def test_register_success_probation() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer svc_forge"
        return httpx.Response(201, json={"name": "csv_diff", "status": "probation"})

    client = _client(handler)
    res = await client.register(_artifact().to_submission("t"))
    assert res == RegisterResult(ok=True, status="probation", name="csv_diff")
    await client.aclose()


async def test_register_tests_failed_422_not_registered() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="2 tests failed")

    client = _client(handler)
    res = await client.register(_artifact().to_submission("t"))
    assert res.ok is False and res.reason == "tests_failed"
    await client.aclose()


async def test_register_scope_denied_raises() -> None:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="scope missing")

    client = _client(handler)
    with pytest.raises(ToolTransportError):
        await client.register(_artifact().to_submission("t"))
    await client.aclose()
