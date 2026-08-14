"""Dev channel — replies are published to the audit stream (visible in
`agent tail`) instead of going to a real user. Lets the loop be driven locally
without a Telegram token.
"""

from __future__ import annotations

from agent.bus.events import STREAM_AUDIT, Event
from agent.bus.streams import EventBus
from agent.logging import get_logger

log = get_logger("channel.dev")


class DevChannel:
    name = "dev"

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def send(self, chat_id: str, text: str) -> None:
        log.info("dev_reply", chat_id=chat_id, text=text)
        await self._bus.publish(
            STREAM_AUDIT,
            Event(
                type="reply",
                component="channel.dev",
                message=text,
                payload={"chat_id": chat_id},
            ),
        )
