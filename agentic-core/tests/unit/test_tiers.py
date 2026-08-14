from agent.autonomy.tiers import Tier, classify, gate


def test_classify_known_and_unknown() -> None:
    assert classify("chat.reply") is Tier.AUTO
    assert classify("memory.promote") is Tier.NOTIFY
    assert classify("tool.register") is Tier.APPROVE
    # Unknown actions fail safe to APPROVE.
    assert classify("something.new") is Tier.APPROVE


async def test_gate_auto_allows() -> None:
    dec = await gate("chat.send")
    assert dec.allowed is True
    assert dec.tier is Tier.AUTO


async def test_gate_notify_allows_and_flags_notify() -> None:
    dec = await gate("memory.promote")
    assert dec.allowed is True
    assert dec.notify is True


async def test_gate_notify_blocked_under_drift_pause() -> None:
    dec = await gate("memory.promote", drift_paused=True)
    assert dec.allowed is False
    assert "drift-pause" in dec.reason


async def test_gate_approve_blocks() -> None:
    dec = await gate("tool.register")
    assert dec.allowed is False
    assert dec.tier is Tier.APPROVE
