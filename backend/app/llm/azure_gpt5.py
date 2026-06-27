import hashlib
import json
from dataclasses import dataclass
from urllib.parse import urlparse

from backend.app.config import Settings
from backend.app.logging.redaction import redact


@dataclass(frozen=True)
class LlmModelProfile:
    profile_id: str
    label: str
    short_label: str
    provider: str
    endpoint: str = ""
    deployment: str = ""
    model_name: str = ""
    api_version: str = ""
    auth_mode: str = ""
    use_v1_api: bool = False
    api_key: str = ""

    @property
    def model_display(self) -> str:
        return self.deployment or self.model_name or "not_configured"


class AzureGpt5Service:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def model_profiles(self) -> list[dict]:
        default_profile = self._resolve_profile(None).profile_id
        return [
            self._public_profile(profile, is_default=profile.profile_id == default_profile)
            for profile in self._profiles().values()
        ]

    def describe_model_profile(self, model_profile: str | None = None) -> dict:
        return self._public_profile(self._resolve_profile(model_profile), is_default=False)

    async def chat(self, *, message: str, model_profile: str | None = None) -> dict:
        clean_message = message.strip()
        profile = self._resolve_profile(model_profile)
        if not clean_message:
            return self._response_base(
                profile,
                ok=False,
                configured=self._profile_configured(profile),
                used_fallback=True,
                message="Please enter a message for the LLM.",
            )
        if not self._profile_configured(profile):
            return self._response_base(
                profile,
                ok=False,
                configured=False,
                used_fallback=True,
                message=f"Model profile '{profile.label}' is not configured for this app instance.",
            )
        try:
            model = self._model(model_profile)
            response = await model.ainvoke(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are the BOS Genesis ESDA assistant. Answer concisely and safely. "
                            "Do not claim to have performed tool actions unless a tool result is supplied."
                        ),
                    },
                    {"role": "user", "content": clean_message},
                ]
            )
            content = str(response.content).strip()
            return self._response_base(
                profile,
                ok=True,
                configured=True,
                used_fallback=False,
                message=content[:4000] or "The model returned an empty response.",
            )
        except Exception as exc:
            return self._response_base(
                profile,
                ok=False,
                configured=True,
                used_fallback=True,
                message=str(exc),
            )

    async def smoke_test(self, model_profile: str | None = None) -> dict:
        profile = self._resolve_profile(model_profile)
        if not self._profile_configured(profile):
            return self._response_base(
                profile,
                ok=False,
                configured=False,
                used_fallback=True,
                message=f"Model profile '{profile.label}' is not configured for this app instance.",
            )
        try:
            model = self._model(model_profile)
            response = await model.ainvoke(
                [
                    {
                        "role": "system",
                        "content": "Return a one-sentence plain text health confirmation for BOS Genesis ESDA.",
                    },
                    {"role": "user", "content": "Say the LLM connection is working."},
                ]
            )
            content = str(response.content).strip()
            return self._response_base(
                profile,
                ok=True,
                configured=True,
                used_fallback=False,
                message=content[:500] or "LLM connection is working.",
            )
        except Exception as exc:
            return self._response_base(
                profile,
                ok=False,
                configured=True,
                used_fallback=True,
                message=str(exc),
            )

    async def diagnostic_plan(
        self,
        *,
        goal: str,
        target_url: str,
        namespace: str | None,
        model_profile: str | None = None,
    ) -> dict:
        fallback = {
            "prompt_version": "planner_v1",
            "prompt_hash": hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            "reasoning_summary": (
                "Create a read-only health-check plan using REST GET, PowerShell GET template, "
                "and Kubernetes MCP inspection when configured."
            ),
            "steps": [
                {"title": "Run REST GET", "tool": "rest.get", "risk": "low"},
                {
                    "title": "Run PowerShell GET template",
                    "tool": "powershell.ps_http_get",
                    "risk": "low",
                },
                {
                    "title": "Inspect Kubernetes via MCP",
                    "tool": "mcp.k8s_inspector",
                    "risk": "medium",
                },
                {"title": "Validate and report", "tool": "validation", "risk": "low"},
            ],
        }
        return await self._json_response(
            system=(
                "You are BOS Genesis ESDA Assistant. Create a concise read-only diagnostic plan. "
                "Use only REST GET, PowerShell GET template, and MCP Kubernetes inspection. "
                "Return JSON with reasoning_summary and steps."
            ),
            user_payload={"goal": goal, "target_url": target_url, "namespace": namespace},
            fallback=fallback,
            model_profile=model_profile,
        )

    async def release_note_plan(
        self,
        *,
        github_url: str,
        release_name: str | None,
        branch: str | None,
        tag: str | None,
        commit_sha: str | None,
        model_profile: str | None = None,
    ) -> dict:
        seed = json.dumps(
            {
                "github_url": github_url,
                "release_name": release_name,
                "branch": branch,
                "tag": tag,
                "commit_sha": commit_sha,
            },
            sort_keys=True,
        )
        fallback = {
            "prompt_version": "release_note_planner_v1",
            "prompt_hash": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
            "reasoning_summary": (
                "Create a read-only release-note plan: validate the GitHub source, call "
                "release-note-agent for commit/PR evidence, draft from evidence only, validate format, "
                "and save a Markdown artifact."
            ),
            "steps": [
                {"title": "Validate GitHub URL", "tool": "policy.github_url", "risk": "low"},
                {
                    "title": "Collect release evidence",
                    "tool": "release_notes.agent_scan",
                    "risk": "low",
                },
                {
                    "title": "Draft Markdown release notes",
                    "tool": "llm.selected_model",
                    "risk": "low",
                },
                {
                    "title": "Validate source evidence and sections",
                    "tool": "validation",
                    "risk": "low",
                },
            ],
        }
        return await self._json_response(
            system=(
                "You are BOS Genesis ESDA Assistant. Plan a read-only release-note generation run. "
                "The user will provide a GitHub URL. The backend can call release-note-agent and then "
                "draft a Markdown artifact. Return JSON with reasoning_summary and steps. Do not include "
                "hidden chain-of-thought."
            ),
            user_payload={
                "github_url": github_url,
                "release_name": release_name,
                "branch": branch,
                "tag": tag,
                "commit_sha": commit_sha,
            },
            fallback=fallback,
            model_profile=model_profile,
        )

    async def release_note_draft(
        self,
        *,
        github_url: str,
        release_name: str | None,
        plan: dict,
        agent_result: dict,
        model_profile: str | None = None,
    ) -> dict:
        fallback = self._fallback_release_note_draft(
            github_url=github_url,
            release_name=release_name,
            plan=plan,
            agent_result=agent_result,
        )
        profile = self._resolve_profile(model_profile)
        if not self._profile_configured(profile):
            return fallback
        result = await self._json_response(
            system=(
                "You are BOS Genesis ESDA Assistant. Draft concise Markdown release notes from the "
                "provided release-note-agent evidence. Use only available evidence. If evidence is thin, "
                "say so explicitly. Return JSON with reasoning_summary and markdown. Do not include hidden "
                "chain-of-thought."
            ),
            user_payload={
                "github_url": github_url,
                "release_name": release_name,
                "plan": plan,
                "agent_result": agent_result,
            },
            fallback=fallback,
            model_profile=model_profile,
        )
        if "markdown" not in result:
            result["markdown"] = fallback["markdown"]
        return result

    def tool_binding_placeholder(self, tools: list[dict]) -> dict:
        return {
            "enabled": False,
            "tool_count": len(tools),
            "reason": "Direct model tool binding is reserved for the registered Phase 2 tool layer.",
        }

    async def _json_response(
        self,
        *,
        system: str,
        user_payload: dict,
        fallback: dict,
        model_profile: str | None = None,
    ) -> dict:
        profile = self._resolve_profile(model_profile)
        if not self._profile_configured(profile):
            return fallback
        user = json.dumps(redact(user_payload), default=str)
        try:
            model = self._model(model_profile)
            response = await model.ainvoke(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            )
            content = str(response.content)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                parsed = fallback | {"reasoning_summary": content[:2000]}
            parsed.setdefault("prompt_version", fallback.get("prompt_version", "planner_v1"))
            parsed.setdefault(
                "prompt_hash", hashlib.sha256((system + user).encode("utf-8")).hexdigest()
            )
            parsed.setdefault("model_profile", profile.profile_id)
            parsed.setdefault("model_label", profile.label)
            return parsed
        except Exception as exc:
            return fallback | {"reasoning_summary": f"Planner fallback used: {exc}"}

    async def structured_response(
        self,
        *,
        system: str,
        user_payload: dict,
        fallback: dict,
        model_profile: str | None = None,
    ) -> dict:
        return await self._json_response(
            system=system,
            user_payload=user_payload,
            fallback=fallback,
            model_profile=model_profile,
        )

    def _model(self, model_profile: str | None = None):
        profile = self._resolve_profile(model_profile)
        if profile.provider == "ollama":
            return self._openai_compatible_chat_model(profile)
        if profile.auth_mode == "azure_cli":
            return self._azure_chat_openai_with_cli_token(profile)
        if profile.auth_mode == "default_azure_credential":
            return self._azure_chat_openai_with_default_credential(profile)
        if profile.use_v1_api:
            return self._v1_chat_openai_with_api_key(profile)
        return self._azure_chat_openai_with_api_key(profile)

    def _v1_chat_openai_with_api_key(self, profile: LlmModelProfile):
        from langchain_openai import ChatOpenAI

        base_url = profile.endpoint.rstrip("/") + "/openai/v1/"
        kwargs = self._common_model_kwargs()
        return ChatOpenAI(
            model=profile.deployment,
            api_key=profile.api_key,
            base_url=base_url,
            **kwargs,
        )

    def _azure_chat_openai_with_api_key(self, profile: LlmModelProfile):
        from langchain_openai import AzureChatOpenAI

        kwargs = self._common_model_kwargs()
        return AzureChatOpenAI(
            azure_endpoint=profile.endpoint,
            azure_deployment=profile.deployment,
            model=profile.model_name or None,
            api_key=profile.api_key,
            openai_api_version=profile.api_version,
            **kwargs,
        )

    def _azure_chat_openai_with_cli_token(self, profile: LlmModelProfile):
        from azure.identity import AzureCliCredential, get_bearer_token_provider

        credential = AzureCliCredential()
        token_provider = get_bearer_token_provider(
            credential,
            "https://cognitiveservices.azure.com/.default",
        )
        return self._azure_chat_openai_with_token_provider(profile, token_provider)

    def _azure_chat_openai_with_default_credential(self, profile: LlmModelProfile):
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential,
            "https://cognitiveservices.azure.com/.default",
        )
        return self._azure_chat_openai_with_token_provider(profile, token_provider)

    def _azure_chat_openai_with_token_provider(self, profile: LlmModelProfile, token_provider):
        from langchain_openai import AzureChatOpenAI

        kwargs = self._common_model_kwargs()
        return AzureChatOpenAI(
            azure_ad_token_provider=token_provider,
            azure_endpoint=profile.endpoint,
            azure_deployment=profile.deployment,
            model=profile.model_name or None,
            openai_api_version=profile.api_version,
            **kwargs,
        )

    def _openai_compatible_chat_model(self, profile: LlmModelProfile):
        from langchain_openai import ChatOpenAI

        kwargs = self._common_model_kwargs()
        return ChatOpenAI(
            model=profile.model_name,
            api_key=profile.api_key or "ollama",
            base_url=profile.endpoint.rstrip("/"),
            **kwargs,
        )

    def _common_model_kwargs(self) -> dict:
        return {
            "temperature": self.settings.azure_openai_temperature,
            "max_tokens": self.settings.azure_openai_max_tokens,
            "timeout": self.settings.azure_openai_timeout_seconds,
            "max_retries": self.settings.azure_openai_max_retries,
        }

    def _profiles(self) -> dict[str, LlmModelProfile]:
        azure_endpoint = (
            self.settings.azure_openai_endpoint or self.settings.azure_openai_gpt5_endpoint
        )
        azure_api_version = (
            self.settings.azure_api_version or self.settings.azure_openai_gpt5_api_version
        )
        gpt5_auth_mode = self.settings.azure_openai_auth_mode
        if gpt5_auth_mode == "api_key" and not self.settings.azure_openai_api_key:
            gpt5_auth_mode = "default_azure_credential"
        return {
            "azure_gpt5_pro": LlmModelProfile(
                profile_id="azure_gpt5_pro",
                label="GPT 5 Pro",
                short_label="GPT-5",
                provider="azure_openai",
                endpoint=self.settings.azure_openai_gpt5_endpoint or azure_endpoint,
                deployment=(
                    self.settings.azure_openai_gpt5_deployment
                    or self.settings.azure_openai_gpt5_pro_deployment
                ),
                model_name=self.settings.azure_openai_gpt5_model_name,
                api_version=self.settings.azure_openai_gpt5_api_version or azure_api_version,
                auth_mode=gpt5_auth_mode,
                api_key=self.settings.azure_openai_api_key,
            ),
            "azure_gpt41_mini": LlmModelProfile(
                profile_id="azure_gpt41_mini",
                label="GPT-4.1 mini",
                short_label="GPT-4.1",
                provider="azure_openai",
                endpoint=azure_endpoint,
                deployment=self.settings.openai_deployment
                or self.settings.azure_openai_gpt41_mini_deployment,
                model_name=self.settings.azure_openai_gpt41_mini_model_name,
                api_version=azure_api_version,
                auth_mode=self.settings.azure_openai_auth_mode,
                use_v1_api=self.settings.azure_openai_use_v1_api,
                api_key=self.settings.azure_openai_api_key,
            ),
            "ollama_llama70b": LlmModelProfile(
                profile_id="ollama_llama70b",
                label="Llama 3.3 70B",
                short_label="Llama70B",
                provider="ollama",
                endpoint=self.settings.ollama_llama70b_base_url,
                model_name=self.settings.ollama_llama70b_model,
                auth_mode="none",
            ),
            "ollama_gemma4": LlmModelProfile(
                profile_id="ollama_gemma4",
                label="Gemma4 26B",
                short_label="Gemma4",
                provider="ollama",
                endpoint=self.settings.ollama_gemma_base_url,
                model_name=self.settings.ollama_gemma_model,
                auth_mode="none",
            ),
            "azure_configured": LlmModelProfile(
                profile_id="azure_configured",
                label="Configured Azure OpenAI",
                short_label="Azure",
                provider="azure_openai",
                endpoint=self.settings.azure_openai_endpoint,
                deployment=self.settings.azure_deployment_name,
                model_name=self.settings.azure_deployment_name,
                api_version=self.settings.azure_api_version,
                auth_mode=self.settings.azure_openai_auth_mode,
                use_v1_api=self.settings.azure_openai_use_v1_api,
                api_key=self.settings.azure_openai_api_key,
            ),
        }

    def _resolve_profile(self, model_profile: str | None) -> LlmModelProfile:
        profiles = self._profiles()
        requested = model_profile or self.settings.llm_default_model_profile
        return profiles.get(requested) or profiles["azure_configured"]

    def _profile_configured(self, profile: LlmModelProfile) -> bool:
        if profile.provider == "ollama":
            return bool(profile.endpoint and profile.model_name)
        common_configured = bool(profile.endpoint and profile.deployment and profile.api_version)
        if not common_configured:
            return False
        if profile.auth_mode in {"azure_cli", "default_azure_credential"}:
            return True
        return bool(profile.api_key)

    def _public_profile(self, profile: LlmModelProfile, *, is_default: bool) -> dict:
        return {
            "profile_id": profile.profile_id,
            "label": profile.label,
            "short_label": profile.short_label,
            "provider": profile.provider,
            "deployment": profile.deployment,
            "model_name": profile.model_name,
            "model_display": profile.model_display,
            "endpoint": profile.endpoint,
            "auth_mode": profile.auth_mode,
            "api_version": profile.api_version,
            "configured": self._profile_configured(profile),
            "is_default": is_default,
        }

    def _response_base(
        self,
        profile: LlmModelProfile,
        *,
        ok: bool,
        configured: bool,
        used_fallback: bool,
        message: str,
    ) -> dict:
        return {
            "ok": ok,
            "configured": configured,
            "used_fallback": used_fallback,
            "deployment": profile.model_display,
            "auth_mode": profile.auth_mode,
            "provider": profile.provider,
            "model_profile": profile.profile_id,
            "model_label": profile.label,
            "message": message,
        }

    def _fallback_release_note_draft(
        self,
        *,
        github_url: str,
        release_name: str | None,
        plan: dict,
        agent_result: dict,
    ) -> dict:
        repo_name = self._repo_name(github_url)
        agent_status = agent_result.get("status", "unknown")
        output = agent_result.get("output") or {}
        artifacts = output.get("artifacts") or []
        evidence_note = "release-note-agent returned no hydrated artifact content yet."
        if artifacts:
            evidence_note = f"release-note-agent returned {len(artifacts)} artifact record(s)."
        markdown = "\n".join(
            [
                f"# Release Notes: {release_name or repo_name or 'Draft'}",
                "",
                "## Summary",
                f"Hello-world release-note draft for `{github_url}`.",
                f"Source analysis status: `{agent_status}`; {evidence_note}",
                "",
                "## Features",
                "- Review commit and pull request evidence from release-note-agent before publishing.",
                "",
                "## Fixes",
                "- Review bug-fix evidence from release-note-agent before publishing.",
                "",
                "## Operational Changes",
                "- Confirm deployment, configuration, and migration notes from source evidence.",
                "",
                "## Known Issues",
                "- None identified by the hello-world fallback draft.",
                "",
                "## Deployment Notes",
                "- Generated as a read-only draft. Publishing requires a later approval workflow.",
                "",
                "## Source Evidence",
                f"- GitHub URL: {github_url}",
                f"- Planner summary: {plan.get('reasoning_summary', '')}",
            ]
        )
        return {
            "prompt_version": "release_note_draft_v1",
            "prompt_hash": hashlib.sha256(
                (github_url + json.dumps(agent_result, default=str)).encode()
            ).hexdigest(),
            "reasoning_summary": (
                "Drafted a safe hello-world release note using available source evidence "
                "and explicit gaps."
            ),
            "markdown": markdown,
        }

    @staticmethod
    def _repo_name(github_url: str) -> str:
        path = urlparse(github_url).path.strip("/")
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return path or "release"
