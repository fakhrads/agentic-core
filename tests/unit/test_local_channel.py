import pytest

from agent.channels.local import LocalChannel


async def test_send_prints_reply(capsys: pytest.CaptureFixture[str]) -> None:
    await LocalChannel().send("cli", "hello there")
    out = capsys.readouterr().out
    assert "hello there" in out
