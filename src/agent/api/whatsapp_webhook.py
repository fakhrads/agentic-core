"""WhatsApp inbound webhook — receives forwarded messages from the local
Baileys bridge (whatsapp-bridge/), not from Meta.

The bridge and this process both run on the operator's own machine/network,
so trust is established with a shared bearer secret (`AGENT_WHATSAPP_BRIDGE_SECRET`)
rather than Meta's HMAC signature scheme. There's no GET verify handshake —
that's a Meta Cloud API concept and doesn't apply here.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Request, Response

from agent.channels.base import InboundMessage
from agent.config import get_settings
from agent.logging import get_logger

log = get_logger("api.whatsapp_webhook")

router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])


def _authorized(header: str | None, secret: str) -> bool:
    if not secret:
        return True
    return header is not None and hmac.compare_digest(header, f"Bearer {secret}")


@router.post("")
async def inbound(request: Request) -> Response:
    s = get_settings()
    secret = s.whatsapp_bridge_secret.get_secret_value()
    if not _authorized(request.headers.get("Authorization"), secret):
        log.warning("whatsapp_bad_secret")
        return Response(status_code=403)

    daemon = getattr(request.app.state, "daemon", None)
    if daemon is None:
        log.warning("whatsapp_inbound_no_daemon")
        return Response(status_code=503)

    payload = await request.json()
    sender = str(payload.get("from", ""))
    text = str(payload.get("text", ""))
    if not sender or not text:
        return Response(status_code=400)

    allowed = s.allowed_whatsapp_numbers()
    if allowed and sender not in allowed:
        log.warning("whatsapp_sender_denied", sender=sender)
        return Response(status_code=200)

    await daemon.ingest(
        InboundMessage(
            channel="whatsapp",
            chat_id=sender,
            text=text,
            user_id=sender,
            message_id=str(payload.get("id", "")) or None,
        )
    )
    return Response(status_code=200)
