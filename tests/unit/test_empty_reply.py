"""Regression: a reasoning model that used its whole token budget on internal
reasoning returns content="" with finish_reason="length". The executor passed
that straight through and the channel sent an empty message — from the user's
side the bot simply never answered.
"""

from agent.llm.base import LLMResult
from agent.loop.executor import _EMPTY_REPLY_FALLBACK, _reply_text


def _result(text: str, finish_reason: str = "stop") -> LLMResult:
    return LLMResult(
        text=text,
        provider="deepseek",
        model="deepseek-v4-flash",
        tok_in=100,
        tok_out=512,
        cost_usd=0.001,
        finish_reason=finish_reason,
    )


def test_normal_reply_passes_through_unchanged() -> None:
    assert _reply_text(_result("halo!")) == "halo!"


def test_reasoning_model_truncated_before_answering_gets_fallback() -> None:
    # The exact shape observed from deepseek-v4-flash at max_tokens=512:
    # all budget spent reasoning, empty content, finish_reason="length".
    assert _reply_text(_result("", finish_reason="length")) == _EMPTY_REPLY_FALLBACK


def test_empty_reply_for_any_other_reason_also_gets_fallback() -> None:
    assert _reply_text(_result("", finish_reason="stop")) == _EMPTY_REPLY_FALLBACK


def test_whitespace_only_reply_counts_as_empty() -> None:
    assert _reply_text(_result("   \n  ")) == _EMPTY_REPLY_FALLBACK


def test_fallback_is_not_blank() -> None:
    # The whole point is that something reaches the user.
    assert _EMPTY_REPLY_FALLBACK.strip()
