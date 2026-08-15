import json

import httpx
import pytest
from fastapi import FastAPI

from agent.api.whatsapp_webhook import _authorized, router
from agent.channels.whatsapp import WhatsAppChannel
from agent.config import Settings


async def test_send_posts_expected_payload() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        captured["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"ok": True})

    channel = WhatsAppChannel(
        bridge_url="http://127.0.0.1:8098", bridge_secret="bridge-secret", allowed_numbers=set()
    )
    channel._client = httpx.AsyncClient(  # noqa: SLF001 - swap transport for the test
        base_url="http://127.0.0.1:8098",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer bridge-secret"},
    )

    await channel.send("6281234", "hello")

    assert captured["url"] == "http://127.0.0.1:8098/send"
    assert captured["auth"] == "Bearer bridge-secret"
    assert captured["body"] == {"to": "6281234", "text": "hello"}
    await channel.aclose()


def test_allowed_number_empty_set_allows_all() -> None:
    channel = WhatsAppChannel(bridge_url="http://x", bridge_secret="s", allowed_numbers=set())
    assert channel.allowed_number("anything")


def test_allowed_number_restricts_to_set() -> None:
    channel = WhatsAppChannel(
        bridge_url="http://x", bridge_secret="s", allowed_numbers={"+62812"}
    )
    assert channel.allowed_number("+62812")
    assert not channel.allowed_number("+1555")


def test_authorized_accepts_matching_secret() -> None:
    assert _authorized("Bearer shh", "shh")


def test_authorized_rejects_mismatch() -> None:
    assert not _authorized("Bearer wrong", "shh")


def test_authorized_rejects_missing_header() -> None:
    assert not _authorized(None, "shh")


def test_authorized_allows_anything_when_no_secret_configured() -> None:
    # Matches the dev-only "insecure" mode the bridge itself warns about.
    assert _authorized(None, "")


class _FakeDaemon:
    def __init__(self) -> None:
        self.ingested: list[object] = []

    async def ingest(self, inbound: object) -> None:
        self.ingested.append(inbound)


def _app(daemon: _FakeDaemon | None) -> FastAPI:
    app = FastAPI()
    app.state.daemon = daemon
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    s = Settings(whatsapp_bridge_secret="bridge-secret")
    monkeypatch.setattr("agent.api.whatsapp_webhook.get_settings", lambda: s)
    return s


async def test_inbound_rejects_bad_secret(_patch_settings: Settings) -> None:
    daemon = _FakeDaemon()
    app = _app(daemon)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/webhooks/whatsapp",
            json={"from": "6281234", "text": "hi"},
            headers={"Authorization": "Bearer wrong-secret"},
        )
    assert resp.status_code == 403
    assert daemon.ingested == []


async def test_inbound_ingests_text_message_with_valid_secret(_patch_settings: Settings) -> None:
    daemon = _FakeDaemon()
    app = _app(daemon)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/webhooks/whatsapp",
            json={"from": "6281234", "text": "hi", "id": "wamid.1"},
            headers={"Authorization": "Bearer bridge-secret"},
        )
    assert resp.status_code == 200
    assert len(daemon.ingested) == 1
    assert daemon.ingested[0].channel == "whatsapp"
    assert daemon.ingested[0].chat_id == "6281234"
    assert daemon.ingested[0].text == "hi"
    assert daemon.ingested[0].message_id == "wamid.1"


async def test_inbound_rejects_missing_from_or_text(_patch_settings: Settings) -> None:
    daemon = _FakeDaemon()
    app = _app(daemon)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/webhooks/whatsapp",
            json={"from": "6281234"},
            headers={"Authorization": "Bearer bridge-secret"},
        )
    assert resp.status_code == 400
    assert daemon.ingested == []


async def test_inbound_without_daemon_returns_503(_patch_settings: Settings) -> None:
    app = _app(None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/webhooks/whatsapp",
            json={"from": "6281234", "text": "hi"},
            headers={"Authorization": "Bearer bridge-secret"},
        )
    assert resp.status_code == 503


async def test_inbound_denied_sender_is_dropped_but_acked(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Settings(whatsapp_bridge_secret="bridge-secret", whatsapp_allowed_numbers="+62812")
    monkeypatch.setattr("agent.api.whatsapp_webhook.get_settings", lambda: s)
    daemon = _FakeDaemon()
    app = _app(daemon)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/webhooks/whatsapp",
            json={"from": "+1555", "text": "hi"},
            headers={"Authorization": "Bearer bridge-secret"},
        )
    assert resp.status_code == 200
    assert daemon.ingested == []
