"""Telegram channel — long-polling ingest + sendMessage reply.

Long polling avoids needing a public webhook URL. `poll()` yields inbound
messages (filtered by allowed chat ids); the daemon publishes them to the bus.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from agent.channels.base import InboundMessage
from agent.logging import get_logger

log = get_logger("channel.telegram")

OnMessage = Callable[[InboundMessage], Awaitable[None]]


class TelegramChannel:
    name = "telegram"

    def __init__(
        self,
        *,
        token: str,
        allowed_chat_ids: set[int],
        timeout_s: float = 20.0,
    ) -> None:
        self._allowed = allowed_chat_ids
        self._timeout_s = timeout_s
        self._offset = 0
        self._stop = False
        self._client = httpx.AsyncClient(
            base_url=f"https://api.telegram.org/bot{token}",
            timeout=timeout_s + 10,
        )

    def stop(self) -> None:
        self._stop = True

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, chat_id: str, text: str) -> None:
        resp = await self._client.post(
            "/sendMessage", json={"chat_id": chat_id, "text": text}
        )
        resp.raise_for_status()

    def _allowed_chat(self, chat_id: int) -> bool:
        return not self._allowed or chat_id in self._allowed

    async def poll(self, on_message: OnMessage) -> None:
        """Long-poll getUpdates until stop() is called."""
        log.info("telegram_poll_started", allowed=len(self._allowed))
        while not self._stop:
            try:
                resp = await self._client.get(
                    "/getUpdates",
                    params={"offset": self._offset, "timeout": int(self._timeout_s)},
                )
                resp.raise_for_status()
                updates = resp.json().get("result", [])
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("telegram_poll_error", error=str(exc))
                continue

            for upd in updates:
                self._offset = max(self._offset, int(upd["update_id"]) + 1)
                msg = upd.get("message")
                if not msg or "text" not in msg:
                    continue
                chat_id = int(msg["chat"]["id"])
                if not self._allowed_chat(chat_id):
                    log.warning("telegram_chat_denied", chat_id=chat_id)
                    continue
                await on_message(
                    InboundMessage(
                        channel=self.name,
                        chat_id=str(chat_id),
                        text=str(msg["text"]),
                        user_id=str(msg.get("from", {}).get("id", "")) or None,
                        message_id=str(msg.get("message_id", "")) or None,
                    )
                )
        log.info("telegram_poll_stopped")
