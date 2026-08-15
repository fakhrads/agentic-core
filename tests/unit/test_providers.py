import pytest

from agent.cli import _providers
from agent.cli._providers import prompt_llm_provider


def _queue_answers(monkeypatch: pytest.MonkeyPatch, answers: list[str]) -> None:
    it = iter(answers)

    def fake_ask(*_args: object, **_kwargs: object) -> str:
        return next(it)

    monkeypatch.setattr(_providers.Prompt, "ask", staticmethod(fake_ask))


def test_openai_preset_fills_base_url_and_prompts_model_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Provider, Model, API key.
    _queue_answers(monkeypatch, ["openai", "gpt-4o-mini", "sk-test"])
    updates = prompt_llm_provider({})
    assert updates == {
        "AGENT_LLM_PROVIDER": "openai",
        "AGENT_DEEPSEEK_BASE_URL": "https://api.openai.com/v1",
        "AGENT_DEEPSEEK_MODEL": "gpt-4o-mini",
        "AGENT_DEEPSEEK_API_KEY": "sk-test",
    }


def test_custom_preset_prompts_for_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # Provider, Base URL, Model, API key.
    _queue_answers(
        monkeypatch, ["custom", "http://localhost:8000/v1", "local-model", "unused-key"]
    )
    updates = prompt_llm_provider({})
    assert updates["AGENT_DEEPSEEK_BASE_URL"] == "http://localhost:8000/v1"
    assert updates["AGENT_DEEPSEEK_MODEL"] == "local-model"


def test_blank_key_keeps_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    _queue_answers(monkeypatch, ["deepseek", "deepseek-chat", ""])
    updates = prompt_llm_provider({"AGENT_DEEPSEEK_API_KEY": "existing-key"})
    assert updates["AGENT_DEEPSEEK_API_KEY"] == "existing-key"


def test_malformed_current_provider_falls_back_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: a garbled AGENT_LLM_PROVIDER value (e.g. from a parser that
    # didn't strip an inline comment) must not KeyError the PRESETS lookup.
    _queue_answers(monkeypatch, ["deepseek", "deepseek-chat", ""])
    updates = prompt_llm_provider(
        {"AGENT_LLM_PROVIDER": "deepseek                          # some stale comment"}
    )
    assert updates["AGENT_LLM_PROVIDER"] == "deepseek"
