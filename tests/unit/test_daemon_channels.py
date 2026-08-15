"""Channel registration wiring — constructing a Daemon does no I/O (all
clients are lazy), so this exercises the real `Daemon.__init__` wiring for
`extra_channels` (agent chat) and conditional WhatsApp registration (mirrors
the existing Telegram guard) without needing redis/postgres/network.
"""

from agent.channels.local import LocalChannel
from agent.config import Settings
from agent.daemon import Daemon


def _settings(**overrides: object) -> Settings:
    return Settings(postgres_dsn="sqlite+aiosqlite:///:memory:", **overrides)  # type: ignore[arg-type]


def test_extra_channels_are_registered() -> None:
    daemon = Daemon(_settings(), extra_channels=[LocalChannel()])
    assert "local" in daemon.channels.names()


def test_whatsapp_registered_only_when_bridge_secret_set() -> None:
    daemon = Daemon(_settings())
    assert daemon.whatsapp is None
    assert "whatsapp" not in daemon.channels.names()

    configured = Daemon(_settings(whatsapp_bridge_secret="bridge-secret"))
    assert configured.whatsapp is not None
    assert "whatsapp" in configured.channels.names()
