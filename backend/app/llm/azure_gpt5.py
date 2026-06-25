import hashlib
import json
from urllib.parse import urlparse

from backend.app.config import Settings
from backend.app.logging.redaction import redact


class AzureGpt5Service:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings



    async def chat(self, *, message: str) -> dict:
        clean_message = message.strip()
        if not clean_message:
            return {
                "ok": False,
                "configured": self.settings.azure_configured,
                "used_fallback": True,
                "deployment": self.settings.azure_deployment_name or "not_configured",
                "auth_mode": self.settings.azure_openai_auth_mode,
                "message": "Please enter a message for the LLM.",
            }
        if not self.settings.azure_configured:
            return {
                "ok": False,
                "configured": False,
                "used_fallback": True,
                "deployment": self.settings.azure_deployment_name or "not_configured",
                "auth_mode": self.settings.azure_openai_auth_mode,
                "message": "Azure OpenAI is not configured for this app instance.",
            }
        try:
            model = self._model()
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
            return {
                "ok": True,
                "configured": True,
                "used_fallback": False,
                "deployment": self.settings.azure_deployment_name,
                "auth_mode": self.settings.azure_openai_auth_mode,
                "message": content[:4000] or "The model returned an empty response.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "configured": True,
                "used_fallback": True,
                "deployment": self.settings.azure_deployment_name,
                "auth_mode": self.settings.azure_openai_auth_mode,
                "message": str(exc),
            }

    async def smoke_test(self) -> dict:
        if not self.settings.azure_configured:
            return {
                "ok": False,
                "configured": False,
                "used_fallback": True,
                "deployment": self.settings.azure_deployment_name or "not_configured",
                "auth_mode": self.settings.azure_openai_auth_mode,
                "message": "Azure OpenAI is not configured for this app instance.",
            }
        try:
            model = self._model()
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
            return {
                "ok": True,
                "configured": True,
                "used_fallback": False,
                "deployment": self.settings.azure_deployment_name,
                "auth_mode": self.settings.azure_openai_auth_mode,
                "message": content[:500] or "LLM connection is working.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "configured": True,
                "used_fallback": True,
                "deployment": self.settings.azure_deployment_name,
                "auth_mode": self.settings.azure_openai_auth_mode,
                "message": str(exc),
            }

    async def diagnostic_plan(self, *, goal: str, target_url: str, namespace: str | None) -> dict:
        fallback = {
            "prompt_version": "planner_v1",
            "prompt_hash": hashlib.sha256(goal.encode("utf-8")).hexdigest(),
            "reasoning_summary": (
                "Create a read-only health-check plan using REST GET, PowerShell GET template, "
                "and Kubernetes MCP inspection when configured."
            ),
            "steps": [
                {"title": "Run REST GET", "tool": "rest.get", "risk": "low"},
                {"title": "Run PowerShell GET template", "tool": "powershell.ps_http_get", "risk": "low"},
                {"title": "Inspect Kubernetes via MCP", "tool": "mcp.k8s_inspector", "risk": "medium"},
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
        )

    async def release_note_plan(
        self,
        *,
        github_url: str,
        release_name: str | None,
        branch: str | None,
        tag: str | None,
        commit_sha: str | None,
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
                {"title": "Collect release evidence", "tool": "release_notes.agent_scan", "risk": "low"},
                {"title": "Draft Markdown release notes", "tool": "azure_gpt5", "risk": "low"},
                {"title": "Validate source evidence and sections", "tool": "validation", "risk": "low"},
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
        )

    async def release_note_draft(
        self,
        *,
        github_url: str,
        release_name: str | None,
        plan: dict,
        agent_result: dict,
    ) -> dict:
        fallback = self._fallback_release_note_draft(
            github_url=github_url,
            release_name=release_name,
            plan=plan,
            agent_result=agent_result,
        )
        if not self.settings.azure_configured:
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

    async def _json_response(self, *, system: str, user_payload: dict, fallback: dict) -> dict:
        if not self.settings.azure_configured:
            return fallback
        user = json.dumps(redact(user_payload), default=str)
        try:
            model = self._model()
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
            parsed.setdefault("prompt_hash", hashlib.sha256((system + user).encode("utf-8")).hexdigest())
            return parsed
        except Exception as exc:
            return fallback | {"reasoning_summary": f"Planner fallback used: {exc}"}

    async def structured_response(self, *, system: str, user_payload: dict, fallback: dict) -> dict:
        return await self._json_response(system=system, user_payload=user_payload, fallback=fallback)

    def _model(self):
        if self.settings.azure_openai_auth_mode == "azure_cli":
            return self._azure_chat_openai_with_cli_token()
        if self.settings.azure_openai_use_v1_api:
            return self._v1_chat_openai_with_api_key()
        return self._azure_chat_openai_with_api_key()

    def _v1_chat_openai_with_api_key(self):
        from langchain_openai import ChatOpenAI

        base_url = self.settings.azure_openai_endpoint.rstrip("/") + "/openai/v1/"
        kwargs = self._common_model_kwargs()
        return ChatOpenAI(
            model=self.settings.azure_deployment_name,
            api_key=self.settings.azure_openai_api_key,
            base_url=base_url,
            **kwargs,
        )

    def _azure_chat_openai_with_api_key(self):
        from langchain_openai import AzureChatOpenAI

        kwargs = self._common_model_kwargs()
        return AzureChatOpenAI(
            azure_endpoint=self.settings.azure_openai_endpoint,
            azure_deployment=self.settings.azure_deployment_name,
            api_key=self.settings.azure_openai_api_key,
            openai_api_version=self.settings.azure_api_version,
            **kwargs,
        )

    def _azure_chat_openai_with_cli_token(self):
        from azure.identity import AzureCliCredential, get_bearer_token_provider
        from langchain_openai import AzureChatOpenAI

        credential = AzureCliCredential()
        token_provider = get_bearer_token_provider(
            credential,
            "https://cognitiveservices.azure.com/.default",
        )
        kwargs = self._common_model_kwargs()
        return AzureChatOpenAI(
            azure_ad_token_provider=token_provider,
            azure_endpoint=self.settings.azure_openai_endpoint,
            azure_deployment=self.settings.azure_deployment_name,
            openai_api_version=self.settings.azure_api_version,
            **kwargs,
        )

    def _common_model_kwargs(self) -> dict:
        return {
            "temperature": self.settings.azure_openai_temperature,
            "max_tokens": self.settings.azure_openai_max_tokens,
            "timeout": self.settings.azure_openai_timeout_seconds,
            "max_retries": self.settings.azure_openai_max_retries,
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
            "prompt_hash": hashlib.sha256((github_url + json.dumps(agent_result, default=str)).encode()).hexdigest(),
            "reasoning_summary": "Drafted a safe hello-world release note using available source evidence and explicit gaps.",
            "markdown": markdown,
        }

    @staticmethod
    def _repo_name(github_url: str) -> str:
        path = urlparse(github_url).path.strip("/")
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return path or "release"
