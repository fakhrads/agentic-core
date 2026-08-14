"""Channel abstraction — inbound messages and outbound replies.

The loop is channel-agnostic: it reads InboundMessage events off the bus and
sends replies via whichever channel the message arrived on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(slots=True)
class InboundMessage:
    channel: str
    chat_id: str
    text: str
    user_id: str | None = None
    message_id: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> InboundMessage:
        return cls(
            channel=str(payload["channel"]),
            chat_id=str(payload["chat_id"]),
            text=str(payload["text"]),
            user_id=(str(payload["user_id"]) if payload.get("user_id") else None),
            message_id=(str(payload["message_id"]) if payload.get("message_id") else None),
        )


class Channel(Protocol):
    name: str

    async def send(self, chat_id: str, text: str) -> None: ...


class ChannelRegistry:
    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        self._channels[channel.name] = channel

    def get(self, name: str) -> Channel:
        if name not in self._channels:
            raise KeyError(f"no channel registered: {name}")
        return self._channels[name]

    def names(self) -> list[str]:
        return list(self._channels)
