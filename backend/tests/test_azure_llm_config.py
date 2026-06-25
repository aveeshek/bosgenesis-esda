from backend.app.config import Settings
from backend.app.llm.azure_gpt5 import AzureGpt5Service


def test_azure_settings_support_openai_env_aliases() -> None:
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_auth_mode="azure_cli",
        openai_deployment="bos-trainium-sigma-gpt-4.1-mini",
        openai_api_version="2024-12-01-preview",
    )

    assert settings.azure_configured
    assert settings.azure_deployment_name == "bos-trainium-sigma-gpt-4.1-mini"
    assert settings.azure_api_version == "2024-12-01-preview"


def test_azure_api_key_mode_requires_api_key() -> None:
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_auth_mode="api_key",
        azure_openai_gpt5_deployment="deployment",
        azure_openai_api_version="2024-12-01-preview",
        azure_openai_api_key="",
    )

    assert not settings.azure_configured


def test_azure_cli_auth_mode_uses_azure_chat_openai_path(monkeypatch) -> None:
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_auth_mode="azure_cli",
        openai_deployment="deployment",
        openai_api_version="2024-12-01-preview",
    )
    service = AzureGpt5Service(settings)
    called = {"azure_cli": False}

    def fake_cli_model():
        called["azure_cli"] = True
        return object()

    monkeypatch.setattr(service, "_azure_chat_openai_with_cli_token", fake_cli_model)

    assert service._model() is not None
    assert called["azure_cli"] is True