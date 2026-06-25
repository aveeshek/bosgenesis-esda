from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_name: str = "bosgenesis-esda"
    app_base_url: str = "http://localhost:8080"
    secret_key: str = "change-me-in-real-env"

    admin_username: str = "admin"
    admin_password: str = "admin"

    database_url: str = "postgresql+psycopg://esda:esda@localhost:5432/esda"
    postgres_log_schema: str = "public"
    artifact_storage_dir: str = "var/artifacts"

    qdrant_url: str = "http://localhost:6333"
    redis_url: str = ""

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_auth_mode: str = "api_key"
    azure_openai_gpt5_deployment: str = ""
    azure_openai_api_version: str = ""
    azure_openai_use_v1_api: bool = True
    azure_openai_reasoning_effort: str = "medium"
    azure_openai_reasoning_summary: str = "auto"
    azure_openai_temperature: float = 0.2
    azure_openai_max_tokens: int = 2000
    azure_openai_timeout_seconds: int = 120
    azure_openai_max_retries: int = 2

    # Compatibility aliases for existing Azure OpenAI examples/scripts.
    openai_deployment: str = ""
    openai_api_version: str = ""

    langgraph_checkpointer: str = "memory"
    langmem_enabled: bool = False
    llm_review_logging_enabled: bool = True

    release_note_agent_url: str = ""
    release_note_agent_mcp_url: str = ""
    release_note_agent_transport: str = "auto"
    release_note_agent_timeout_seconds: int = 300
    allowed_github_hosts: str = Field(default="github.com")

    mcp_k8s_inspector_url: str = ""
    mcp_k8s_inspector_api_key: str = ""
    powershell_runner_url: str = ""

    allowed_rest_hosts: str = Field(default="localhost,127.0.0.1")
    default_namespace: str = "bosgenesis"
    policy_rules_path: str = "knowledge-base/policy_rules.yaml"
    approval_expiration_minutes: int = 60

    @property
    def allowed_rest_host_set(self) -> set[str]:
        return {item.strip().lower() for item in self.allowed_rest_hosts.split(",") if item.strip()}

    @property
    def allowed_github_host_set(self) -> set[str]:
        return {item.strip().lower() for item in self.allowed_github_hosts.split(",") if item.strip()}

    @property
    def azure_deployment_name(self) -> str:
        return self.azure_openai_gpt5_deployment or self.openai_deployment

    @property
    def azure_api_version(self) -> str:
        return self.azure_openai_api_version or self.openai_api_version

    @property
    def azure_configured(self) -> bool:
        common_configured = bool(
            self.azure_openai_endpoint
            and self.azure_deployment_name
            and self.azure_api_version
        )
        if not common_configured:
            return False
        if self.azure_openai_auth_mode == "azure_cli":
            return True
        return bool(self.azure_openai_api_key)

    @field_validator("release_note_agent_transport")
    @classmethod
    def validate_release_note_agent_transport(cls, value: str) -> str:
        normalized = value.lower()
        allowed = {"auto", "mcp", "rest"}
        if normalized not in allowed:
            raise ValueError(f"RELEASE_NOTE_AGENT_TRANSPORT must be one of {sorted(allowed)}")
        return normalized

    @field_validator("azure_openai_auth_mode")
    @classmethod
    def validate_azure_openai_auth_mode(cls, value: str) -> str:
        allowed = {"api_key", "azure_cli"}
        if value not in allowed:
            raise ValueError(f"AZURE_OPENAI_AUTH_MODE must be one of {sorted(allowed)}")
        return value

    @field_validator("azure_openai_reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str) -> str:
        allowed = {"minimal", "low", "medium", "high"}
        if value not in allowed:
            raise ValueError(f"AZURE_OPENAI_REASONING_EFFORT must be one of {sorted(allowed)}")
        return value

    @field_validator("azure_openai_reasoning_summary")
    @classmethod
    def validate_reasoning_summary(cls, value: str) -> str:
        allowed = {"auto", "concise", "detailed", "none"}
        if value not in allowed:
            raise ValueError(f"AZURE_OPENAI_REASONING_SUMMARY must be one of {sorted(allowed)}")
        return value

    @field_validator("langgraph_checkpointer")
    @classmethod
    def validate_langgraph_checkpointer(cls, value: str) -> str:
        allowed = {"memory", "postgres", "disabled"}
        if value not in allowed:
            raise ValueError(f"LANGGRAPH_CHECKPOINTER must be one of {sorted(allowed)}")
        return value

    def redacted_summary(self) -> dict:
        summary = self.model_dump()
        summary["azure_deployment_name"] = self.azure_deployment_name
        summary["azure_api_version"] = self.azure_api_version
        for key, value in list(summary.items()):
            lowered = key.lower()
            if "password" in lowered or "secret" in lowered or "api_key" in lowered or "token" in lowered:
                summary[key] = "***"
            elif lowered.endswith("url") and isinstance(value, str):
                summary[key] = self._redact_url_password(value)
        return summary

    @staticmethod
    def _redact_url_password(value: str) -> str:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return value
        if not parsed.password:
            return value
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        username = parsed.username or ""
        netloc = f"{username}:***@{host}" if username else host
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


@lru_cache
def get_settings() -> Settings:
    return Settings()
