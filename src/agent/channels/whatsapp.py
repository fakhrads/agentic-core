"""WhatsApp channel — talks to the local Baileys bridge (whatsapp-bridge/),
not the Meta Cloud API. Unofficial (WhatsApp Web multi-device protocol via
Baileys), so no Business account, app review, or webhook approval needed —
just run the bridge and scan a QR code once.

Inbound messages arrive over HTTP from the bridge (see
`agent.api.whatsapp_webhook`), not a long-poll loop — this class only
implements the outbound `send()` half of the Channel protocol.
"""

from __future__ import annotations

import httpx

from agent.logging import get_logger

log = get_logger("channel.whatsapp")


class WhatsAppChannel:
    name = "whatsapp"

    def __init__(
        self,
        *,
        bridge_url: str,
        bridge_secret: str,
        allowed_numbers: set[str],
        timeout_s: float = 20.0,
    ) -> None:
        self._allowed = allowed_numbers
        headers = {"Authorization": f"Bearer {bridge_secret}"} if bridge_secret else {}
        self._client = httpx.AsyncClient(
            base_url=bridge_url.rstrip("/"), timeout=timeout_s, headers=headers
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def allowed_number(self, number: str) -> bool:
        return not self._allowed or number in self._allowed

    async def send(self, chat_id: str, text: str) -> None:
        resp = await self._client.post("/send", json={"to": chat_id, "text": text})
        resp.raise_for_status()
