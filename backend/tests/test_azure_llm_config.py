from backend.app.config import Settings
from backend.app.llm.azure_gpt5 import AzureGpt5Service


def test_azure_settings_support_openai_env_aliases() -> None:
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_auth_mode="azure_cli",
        azure_openai_gpt5_deployment="",
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


def test_default_azure_credential_mode_does_not_require_api_key() -> None:
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_auth_mode="default_azure_credential",
        azure_openai_gpt5_deployment="deployment",
        azure_openai_api_version="2024-12-01-preview",
        azure_openai_api_key="",
    )

    assert settings.azure_configured


def test_model_profiles_include_requested_azure_and_ollama_profiles() -> None:
    service = AzureGpt5Service(Settings(azure_openai_auth_mode="default_azure_credential"))
    profiles = {profile["profile_id"]: profile for profile in service.model_profiles()}

    assert profiles["azure_gpt5_pro"]["deployment"] == "bos-trainium-gpt-5.0"
    assert profiles["azure_gpt5_pro"]["model_name"] == "gpt-5"
    assert profiles["azure_gpt5_pro"]["auth_mode"] == "default_azure_credential"
    assert profiles["azure_gpt41_mini"]["deployment"] == "bos-trainium-sigma-gpt-4.1-mini"
    assert profiles["ollama_llama70b"]["endpoint"] == "http://ollama-llama70b.bosgenesis.local/v1"
    assert profiles["ollama_llama70b"]["model_name"] == "llama3.3:70b"
    assert profiles["ollama_gemma4"]["endpoint"] == "http://ollama.bosgenesis.local/v1"
    assert profiles["ollama_gemma4"]["model_name"] == "gemma4:26b"
    assert profiles["azure_gpt5_pro"]["label"] == "SIGMA 5 PRO"
    assert profiles["azure_gpt5_pro"]["short_label"] == "SIGMA 5 PRO"
    assert profiles["azure_gpt41_mini"]["label"] == "SIGMA 4.1"
    assert profiles["azure_gpt41_mini"]["short_label"] == "SIGMA 4.1"
    assert profiles["ollama_llama70b"]["label"] == "TRAINIUM BEHEMOTH"
    assert profiles["ollama_llama70b"]["short_label"] == "TRAINIUM BEHEMOTH"
    assert profiles["ollama_gemma4"]["label"] == "TRAINIUM GEMMA"
    assert profiles["ollama_gemma4"]["short_label"] == "TRAINIUM GEMMA"
    assert profiles["azure_configured"]["label"] == "CUSTOM"
    assert profiles["azure_configured"]["short_label"] == "CUSTOM"


def test_azure_cli_auth_mode_uses_azure_chat_openai_path(monkeypatch) -> None:
    settings = Settings(
        azure_openai_endpoint="https://example.openai.azure.com/",
        azure_openai_auth_mode="azure_cli",
        openai_deployment="deployment",
        openai_api_version="2024-12-01-preview",
        llm_default_model_profile="azure_configured",
    )
    service = AzureGpt5Service(settings)
    called = {"azure_cli": False}

    def fake_cli_model(profile):
        called["azure_cli"] = True
        assert profile.profile_id == "azure_configured"
        return object()

    monkeypatch.setattr(service, "_azure_chat_openai_with_cli_token", fake_cli_model)

    assert service._model("azure_configured") is not None
    assert called["azure_cli"] is True


def test_gpt5_profile_honors_azure_cli_auth_mode(monkeypatch) -> None:
    settings = Settings(azure_openai_auth_mode="azure_cli")
    service = AzureGpt5Service(settings)
    profiles = {profile["profile_id"]: profile for profile in service.model_profiles()}
    called = {"azure_cli": False}

    def fake_cli_model(profile):
        called["azure_cli"] = True
        assert profile.profile_id == "azure_gpt5_pro"
        assert profile.auth_mode == "azure_cli"
        return object()

    monkeypatch.setattr(service, "_azure_chat_openai_with_cli_token", fake_cli_model)

    assert profiles["azure_gpt5_pro"]["auth_mode"] == "azure_cli"
    assert service._model("azure_gpt5_pro") is not None
    assert called["azure_cli"] is True


def test_gpt5_profile_uses_default_azure_credential_path(monkeypatch) -> None:
    service = AzureGpt5Service(Settings(azure_openai_auth_mode="default_azure_credential"))
    called = {"default_credential": False}

    def fake_default_model(profile):
        called["default_credential"] = True
        assert profile.profile_id == "azure_gpt5_pro"
        assert profile.deployment == "bos-trainium-gpt-5.0"
        return object()

    monkeypatch.setattr(service, "_azure_chat_openai_with_default_credential", fake_default_model)

    assert service._model("azure_gpt5_pro") is not None
    assert called["default_credential"] is True


def test_ollama_profile_uses_openai_compatible_path(monkeypatch) -> None:
    service = AzureGpt5Service(Settings())
    called = {"ollama": False}

    def fake_ollama_model(profile):
        called["ollama"] = True
        assert profile.profile_id == "ollama_llama70b"
        assert profile.endpoint == "http://ollama-llama70b.bosgenesis.local/v1"
        assert profile.model_name == "llama3.3:70b"
        return object()

    monkeypatch.setattr(service, "_openai_compatible_chat_model", fake_ollama_model)

    assert service._model("ollama_llama70b") is not None
    assert called["ollama"] is True
