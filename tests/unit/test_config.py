from agent.config import Settings


def test_secrets_are_masked_in_redacted() -> None:
    s = Settings(deepseek_api_key="sk-secret", tools_service_token="svc_abc")  # type: ignore[arg-type]
    red = s.redacted()
    assert red["deepseek_api_key"] == "***set***"
    assert red["tools_service_token"] == "***set***"
    # Non-secret fields pass through untouched.
    assert red["deepseek_model"] == s.deepseek_model


def test_empty_secret_reported_as_empty() -> None:
    s = Settings(deepseek_api_key="")  # type: ignore[arg-type]
    assert s.redacted()["deepseek_api_key"] == "***empty***"


def test_unredacted_reveals_secret() -> None:
    s = Settings(deepseek_api_key="sk-secret")  # type: ignore[arg-type]
    assert s.unredacted()["deepseek_api_key"] == "sk-secret"


def test_allowed_chat_ids_parsing() -> None:
    s = Settings(telegram_allowed_chat_ids="1, 2 ,3")
    assert s.allowed_chat_ids() == {1, 2, 3}
    assert Settings(telegram_allowed_chat_ids="").allowed_chat_ids() == set()


def test_allowed_whatsapp_numbers_parsing() -> None:
    s = Settings(whatsapp_allowed_numbers="+62812, +1555 ")
    assert s.allowed_whatsapp_numbers() == {"+62812", "+1555"}
    assert Settings(whatsapp_allowed_numbers="").allowed_whatsapp_numbers() == set()


def test_llm_provider_defaults_to_deepseek() -> None:
    assert Settings().llm_provider == "deepseek"
