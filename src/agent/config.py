"""Central configuration via pydantic-settings.

Every I/O boundary reads its timeout from here — no magic numbers scattered in
clients. Secrets are never logged unless explicitly requested via the CLI.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"
    log_level: str = "INFO"
    log_dir: str = "./logs"

    # HTTP health/metrics surface.
    http_host: str = "127.0.0.1"
    http_port: int = 8099

    # Redis — event bus, quota, revocation cache.
    redis_url: str = "redis://localhost:6380/0"

    # Postgres + pgvector.
    postgres_dsn: str = "postgresql+asyncpg://agent:agent@localhost:5433/agent"

    # Primary chat provider — DeepSeekProvider is OpenAI-wire-compatible, so
    # these fields double as the generic "primary LLM" slot for any
    # OpenAI-compatible endpoint (OpenAI, OpenRouter, custom). `llm_provider`
    # is a cosmetic label used for cost-table lookup and display only; switch
    # it (and the fields below) via `agent model set`.
    llm_provider: str = "deepseek"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_s: float = 60.0

    # Ollama — embeddings + cheap probe model (local).
    ollama_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_probe_model: str = "qwen2.5:3b"
    ollama_timeout_s: float = 30.0

    # Tools backend — contract v1, behind Traefik ForwardAuth.
    tools_base_url: str = "http://localhost:8080"
    tools_service_token: SecretStr = SecretStr("svc_replace_me")
    # Separate token with tools:register scope — NEVER the agent's invoke token
    # (a compromised loop must not be able to plant tools). Contract §1.
    tools_forge_token: SecretStr = SecretStr("")
    tools_timeout_s: float = 20.0
    contract_version: int = 1

    # External content fetch — must NEVER carry a service token.
    fetch_timeout_s: float = 20.0
    # Comma-separated URLs the night shift fetches into quarantine (research).
    research_sources: str = ""

    # Telegram.
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_allowed_chat_ids: str = ""

    # WhatsApp — unofficial, via a local Baileys (Node.js) bridge sidecar
    # (whatsapp-bridge/). No Meta Business account/API needed: the bridge logs
    # in by QR-code scan and talks to this process over localhost HTTP.
    whatsapp_bridge_url: str = "http://127.0.0.1:8098"
    whatsapp_bridge_secret: SecretStr = SecretStr("")
    whatsapp_allowed_numbers: str = ""

    # Per-reply completion cap. Reasoning models (deepseek-v4-*, o-series, …)
    # spend this same budget on their internal reasoning *before* emitting any
    # answer, so a tight cap makes them return empty content with
    # finish_reason="length" — the agent then has nothing to reply with.
    max_reply_tokens: int = 2048

    # Daily autonomy budget.
    budget_tokens: int = 500_000
    budget_cost_usd: float = 2.00
    budget_actions: int = 200

    # Backpressure.
    max_queue: int = 100

    # Playbook (MEMORY.md / USER.md / SELF.md).
    playbook_dir: str = "./playbook"

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"

    def allowed_chat_ids(self) -> set[int]:
        raw = self.telegram_allowed_chat_ids.strip()
        if not raw:
            return set()
        return {int(x) for x in raw.split(",") if x.strip()}

    def research_source_list(self) -> list[str]:
        return [u.strip() for u in self.research_sources.split(",") if u.strip()]

    def allowed_whatsapp_numbers(self) -> set[str]:
        raw = self.whatsapp_allowed_numbers.strip()
        if not raw:
            return set()
        return {x.strip() for x in raw.split(",") if x.strip()}

    def redacted(self) -> dict[str, object]:
        """Dict view with secrets masked — safe for `agent config show`."""
        out: dict[str, object] = {}
        for name, value in self.model_dump().items():
            if isinstance(getattr(self, name), SecretStr):
                secret = getattr(self, name).get_secret_value()
                out[name] = "***set***" if secret else "***empty***"
            else:
                out[name] = value
        return out

    def unredacted(self) -> dict[str, object]:
        """Dict view with secrets revealed — only for `--secrets`."""
        out: dict[str, object] = {}
        for name in self.model_dump():
            attr = getattr(self, name)
            out[name] = attr.get_secret_value() if isinstance(attr, SecretStr) else attr
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper — drop the memoized settings instance."""
    get_settings.cache_clear()
