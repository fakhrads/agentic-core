"""Shared LLM-provider presets and interactive prompt for `setup` and `model`.

DeepSeekProvider (src/agent/llm/deepseek.py) is OpenAI-wire-compatible, so
switching "provider" here just means pointing the same AGENT_DEEPSEEK_* env
keys at a different base_url/model/key — no new provider classes needed for
OpenAI/OpenRouter/custom-compatible endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.prompt import Prompt

from agent.cli._output import console


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    key: str
    label: str
    base_url: str
    default_model: str


PRESETS: dict[str, ProviderPreset] = {
    "deepseek": ProviderPreset("deepseek", "DeepSeek", "https://api.deepseek.com", "deepseek-chat"),
    "openai": ProviderPreset("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    "openrouter": ProviderPreset(
        "openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"
    ),
    "custom": ProviderPreset("custom", "Custom (OpenAI-compatible)", "", ""),
}


def prompt_llm_provider(current: dict[str, str]) -> dict[str, str]:
    """Interactively pick a provider/model/key; returns AGENT_* env updates."""
    console.print("\n[bold]LLM provider[/] — the primary chat model the agent thinks with.")
    choice = Prompt.ask(
        "Provider",
        choices=list(PRESETS),
        default=current.get("AGENT_LLM_PROVIDER", "deepseek"),
    )
    preset = PRESETS[choice]

    base_url = preset.base_url
    if choice == "custom":
        base_url = Prompt.ask(
            "Base URL (OpenAI-compatible, e.g. http://localhost:8000/v1)",
            default=current.get("AGENT_DEEPSEEK_BASE_URL", ""),
        )

    model = Prompt.ask(
        "Model",
        default=current.get("AGENT_DEEPSEEK_MODEL") or preset.default_model,
    )

    existing_key = current.get("AGENT_DEEPSEEK_API_KEY", "")
    key_prompt = "API key" + (" (blank = keep current)" if existing_key else "")
    api_key = Prompt.ask(key_prompt, password=True, default="", show_default=False)
    if not api_key:
        api_key = existing_key

    return {
        "AGENT_LLM_PROVIDER": choice,
        "AGENT_DEEPSEEK_BASE_URL": base_url,
        "AGENT_DEEPSEEK_MODEL": model,
        "AGENT_DEEPSEEK_API_KEY": api_key,
    }
