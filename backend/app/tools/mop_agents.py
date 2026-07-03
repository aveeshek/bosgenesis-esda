import asyncio
from time import perf_counter
from urllib.parse import quote, urljoin

import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult

SECRET_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "archive_base64",
    "content_base64",
    "bundle_base64",
)


class MopMcpError(Exception):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        message = str(payload.get("message") or payload.get("error") or "MCP tool call failed")
        super().__init__(message)


def redact_sensitive(value):
    if isinstance(value, dict):
        kind = str(value.get("kind") or "").lower()
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in SECRET_KEY_PARTS):
                redacted[key] = "***"
            elif kind == "secret" and lowered in {"data", "stringdata"} and isinstance(item, dict):
                redacted[key] = {str(secret_key): "***" for secret_key in item}
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("bearer ", "basic ")):
        return "***"
    return value


class BaseMopMcpTool:
    name = "mop.mcp"
    default_tool_name = "status"
    allowed_tool_names: set[str] = set()
    configured_url_attributes: tuple[str, ...] = ()
    timeout_attribute = "mop_creation_agent_timeout_seconds"
    missing_config_code = "MOP_MCP_NOT_CONFIGURED"
    unsupported_tool_code = "MOP_MCP_TOOL_NOT_ALLOWED"
    failed_code = "MOP_MCP_CALL_FAILED"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    def _base_url(self) -> str:
        for attribute in self.configured_url_attributes:
            value = str(getattr(self.settings, attribute, "") or "").strip()
            if value:
                return value.rstrip("/") + "/"
        return ""

    def _headers(self) -> dict[str, str]:
        return {}

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(float(getattr(self.settings, self.timeout_attribute, 120)))

    def _mcp_tool_url(self, tool_name: str) -> str:
        base = self._base_url()
        if base.rstrip("/").endswith("/mcp"):
            return urljoin(base, f"tools/{tool_name}")
        return urljoin(base, f"mcp/tools/{tool_name}")

    def _tool_arguments(self, request: ToolExecutionRequest) -> tuple[str, dict]:
        tool_name = str(request.arguments.get("tool_name") or self.default_tool_name)
        arguments = request.arguments.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {
                key: value
                for key, value in request.arguments.items()
                if key not in {"tool_name", "arguments"}
            }
        if request.namespace and "namespace" not in arguments:
            arguments["namespace"] = request.namespace
        if request.environment and "environment" not in arguments:
            arguments["environment"] = request.environment
        return tool_name, arguments

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        base_url = self._base_url()
        if not base_url:
            return self._failed(start, self.missing_config_code, f"{self.name} MCP URL is not configured", retryable=False)

        tool_name, arguments = self._tool_arguments(request)
        if tool_name not in self.allowed_tool_names:
            return self._blocked(start, self.unsupported_tool_code, f"Unsupported {self.name} tool: {tool_name}")

        try:
            async with httpx.AsyncClient(timeout=self._timeout(), transport=self.transport) as client:
                response = await client.post(self._mcp_tool_url(tool_name), json=arguments, headers=self._headers())
            response.raise_for_status()
            result = self._extract_mcp_result(response.json())
            redacted_result = redact_sensitive(result)
            output = {
                "transport": "mcp",
                "agent": self.name,
                "tool_name": tool_name,
                "url": str(response.url),
                "request": redact_sensitive(arguments),
                "result": redacted_result,
            }
            return (
                ToolExecutionResult(
                    status="success",
                    output=output,
                    evidence_refs=[str(response.url)],
                    validation_result={"valid": True, "message": f"HTTP status {response.status_code}"},
                ),
                int((perf_counter() - start) * 1000),
            )
        except MopMcpError as exc:
            return self._failed(start, str(exc.payload.get("code") or self.failed_code), str(exc.payload.get("message") or exc), retryable=bool(exc.payload.get("retryable", False)))
        except httpx.HTTPStatusError as exc:
            return self._failed(start, self.failed_code, self._http_error_message(exc), retryable=exc.response.status_code >= 500)
        except Exception as exc:
            return self._failed(start, self.failed_code, str(exc), retryable=True)

    @staticmethod
    def _extract_mcp_result(payload: object) -> dict:
        if isinstance(payload, dict) and payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            raise MopMcpError(error)
        if isinstance(payload, dict):
            result = payload.get("result", payload)
            return result if isinstance(result, dict) else {"result": result}
        return {"result": payload}

    @staticmethod
    def _http_error_message(exc: httpx.HTTPStatusError) -> str:
        try:
            payload = exc.response.json()
            if isinstance(payload, dict):
                return str(payload.get("message") or payload.get("detail") or exc)
        except ValueError:
            pass
        return str(exc)

    def _failed(self, start: float, code: str, message: str, *, retryable: bool) -> tuple[ToolExecutionResult, int]:
        return (
            ToolExecutionResult(status="failed", error={"code": code, "message": message, "retryable": retryable}),
            int((perf_counter() - start) * 1000),
        )

    def _blocked(self, start: float, code: str, message: str) -> tuple[ToolExecutionResult, int]:
        return (
            ToolExecutionResult(status="blocked", error={"code": code, "message": message, "retryable": False}),
            int((perf_counter() - start) * 1000),
        )


class BaseMopRestTool(BaseMopMcpTool):
    """Adapter for BOS Genesis agents that expose REST routes rather than MCP tool routes."""

    route_map: dict[str, tuple[str, str]] = {}

    def _rest_path(self, tool_name: str, arguments: dict) -> tuple[str, str]:
        try:
            method, path_template = self.route_map[tool_name]
        except KeyError:
            raise MopMcpError({"code": self.unsupported_tool_code, "message": f"Unsupported {self.name} tool: {tool_name}", "retryable": False}) from None
        release_name = str(arguments.get("release_name") or arguments.get("name") or "").strip()
        path = path_template.replace("{release_name}", quote(release_name, safe=""))
        return method, path

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        base_url = self._base_url()
        if not base_url:
            return self._failed(start, self.missing_config_code, f"{self.name} URL is not configured", retryable=False)

        tool_name, arguments = self._tool_arguments(request)
        if tool_name not in self.allowed_tool_names:
            return self._blocked(start, self.unsupported_tool_code, f"Unsupported {self.name} tool: {tool_name}")

        try:
            method, path = self._rest_path(tool_name, arguments)
            url = urljoin(base_url, path.lstrip("/"))
            query = self._query_arguments(tool_name, arguments)
            async with httpx.AsyncClient(timeout=self._timeout(), transport=self.transport) as client:
                if method == "GET":
                    response = await client.get(url, params=query, headers=self._headers())
                else:
                    response = await client.request(method, url, json=arguments, headers=self._headers())
            response.raise_for_status()
            result = response.json()
            redacted_result = redact_sensitive(result)
            output = {
                "transport": "rest",
                "agent": self.name,
                "tool_name": tool_name,
                "url": str(response.url),
                "request": redact_sensitive(arguments),
                "result": redacted_result,
            }
            return (
                ToolExecutionResult(
                    status="success",
                    output=output,
                    evidence_refs=[str(response.url)],
                    validation_result={"valid": True, "message": f"HTTP status {response.status_code}"},
                ),
                int((perf_counter() - start) * 1000),
            )
        except MopMcpError as exc:
            return self._failed(start, str(exc.payload.get("code") or self.failed_code), str(exc.payload.get("message") or exc), retryable=bool(exc.payload.get("retryable", False)))
        except httpx.HTTPStatusError as exc:
            return self._failed(start, self.failed_code, self._http_error_message(exc), retryable=exc.response.status_code >= 500)
        except Exception as exc:
            return self._failed(start, self.failed_code, str(exc), retryable=True)

    def _query_arguments(self, tool_name: str, arguments: dict) -> dict:
        excluded = {"release_name", "name", "include_events", "include_configmaps", "change_intent", "include_history", "environment"}
        query = {key: value for key, value in arguments.items() if value is not None and key not in excluded}
        query.setdefault("actor", "esda")
        return query


class K8sInspectorEvidenceTool(BaseMopRestTool):
    name = "mop.k8s_inspector"
    default_tool_name = "k8s_get_namespace_summary"
    allowed_tool_names = {
        "namespace_summary",
        "k8s_get_namespace",
        "k8s_get_namespace_summary",
        "workload_inventory",
        "k8s_list_deployments",
        "k8s_list_pods",
        "k8s_list_pvcs",
        "list_services",
        "k8s_list_services",
        "list_ingresses",
        "k8s_list_ingresses",
        "list_events",
        "k8s_events",
        "describe_resource",
    }
    route_map = {
        "namespace_summary": ("GET", "/namespace/summary"),
        "k8s_get_namespace_summary": ("GET", "/namespace/summary"),
        "k8s_get_namespace": ("GET", "/namespace"),
        "workload_inventory": ("GET", "/deployments"),
        "k8s_list_deployments": ("GET", "/deployments"),
        "k8s_list_pods": ("GET", "/pods"),
        "k8s_list_pvcs": ("GET", "/pvcs"),
        "list_services": ("GET", "/services"),
        "k8s_list_services": ("GET", "/services"),
        "list_ingresses": ("GET", "/ingresses"),
        "k8s_list_ingresses": ("GET", "/ingresses"),
        "list_events": ("GET", "/events"),
        "k8s_events": ("GET", "/events"),
    }
    configured_url_attributes = ("k8s_inspector_agent_mcp_url", "mcp_k8s_inspector_url")
    timeout_attribute = "k8s_inspector_agent_timeout_seconds"
    missing_config_code = "K8S_INSPECTOR_MCP_NOT_CONFIGURED"
    unsupported_tool_code = "K8S_INSPECTOR_TOOL_NOT_ALLOWED"
    failed_code = "K8S_INSPECTOR_MCP_CALL_FAILED"

    def _headers(self) -> dict[str, str]:
        api_key = getattr(self.settings, "mcp_k8s_inspector_api_key", "")
        return {"x-api-key": api_key} if api_key else {}


class HelmManagerEvidenceTool(BaseMopRestTool):
    name = "mop.helm_manager"
    default_tool_name = "helm_list_releases"
    allowed_tool_names = {
        "list_releases",
        "helm_list_releases",
        "release_status",
        "helm_release_status",
        "release_history",
        "helm_release_history",
        "values_summary",
        "helm_get_values",
    }
    route_map = {
        "list_releases": ("GET", "/releases"),
        "helm_list_releases": ("GET", "/releases"),
        "release_status": ("GET", "/releases/{release_name}/status"),
        "helm_release_status": ("GET", "/releases/{release_name}/status"),
        "release_history": ("GET", "/releases/{release_name}/history"),
        "helm_release_history": ("GET", "/releases/{release_name}/history"),
        "values_summary": ("GET", "/releases/{release_name}/values"),
        "helm_get_values": ("GET", "/releases/{release_name}/values"),
    }
    configured_url_attributes = ("helm_manager_agent_mcp_url",)
    timeout_attribute = "helm_manager_agent_timeout_seconds"
    missing_config_code = "HELM_MANAGER_MCP_NOT_CONFIGURED"
    unsupported_tool_code = "HELM_MANAGER_TOOL_NOT_ALLOWED"
    failed_code = "HELM_MANAGER_MCP_CALL_FAILED"

    def _headers(self) -> dict[str, str]:
        api_key = getattr(self.settings, "helm_manager_agent_api_key", "")
        return {"x-api-key": api_key} if api_key else {}


class MopCreationAgentTool(BaseMopMcpTool):
    name = "mop.creation_agent"
    default_tool_name = "mop_creation_generate"
    allowed_tool_names = {
        "mop_create_draft",
        "create_mop_draft",
        "generate_mop",
        "mop_creation_generate",
        "mop_creation_set_namespace",
        "mop_creation_artifacts",
        "mop_creation_latest",
        "validate_mop_draft",
    }
    configured_url_attributes = ("mop_creation_agent_mcp_url", "mop_creation_agent_url")
    timeout_attribute = "mop_creation_agent_timeout_seconds"
    missing_config_code = "MOP_CREATION_AGENT_MCP_NOT_CONFIGURED"
    unsupported_tool_code = "MOP_CREATION_AGENT_TOOL_NOT_ALLOWED"
    failed_code = "MOP_CREATION_AGENT_MCP_CALL_FAILED"

    def _headers(self) -> dict[str, str]:
        api_key = getattr(self.settings, "mop_creation_agent_api_key", "")
        return {"x-api-key": api_key} if api_key else {}

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        result, _duration = await super().execute(request)
        if result.status != "success" or not result.output:
            return result, int((perf_counter() - start) * 1000)
        if result.output.get("tool_name") != "mop_creation_generate":
            return result, int((perf_counter() - start) * 1000)

        body = result.output.get("result") if isinstance(result.output.get("result"), dict) else {}
        mop_id = str(body.get("mop_id") or "").strip()
        if not mop_id:
            return result, int((perf_counter() - start) * 1000)

        final_body = await self._poll_generation(mop_id)
        if final_body:
            artifact_listing = await self._artifact_listing(mop_id) if self._is_generated(final_body) else None
            final_body = dict(final_body)
            if artifact_listing:
                final_body["artifact_listing"] = artifact_listing
                final_body["artifact_files"] = artifact_listing.get("files") or []
            output = dict(result.output)
            output["result"] = redact_sensitive(final_body)
            if self._is_generated(final_body):
                return (
                    ToolExecutionResult(
                        status="success",
                        output=output,
                        evidence_refs=result.evidence_refs,
                        validation_result={"valid": True, "message": f"MoP Creation Agent generated artifacts for {mop_id}"},
                    ),
                    int((perf_counter() - start) * 1000),
                )
            if str(final_body.get("status") or "").lower() == "failed":
                return (
                    ToolExecutionResult(
                        status="failed",
                        output=output,
                        evidence_refs=result.evidence_refs,
                        validation_result={"valid": False, "message": f"MoP Creation Agent failed for {mop_id}"},
                        error={
                            "code": "MOP_CREATION_AGENT_GENERATION_FAILED",
                            "message": self._generation_failure_message(final_body),
                            "retryable": False,
                        },
                    ),
                    int((perf_counter() - start) * 1000),
                )
        return result, int((perf_counter() - start) * 1000)

    async def _poll_generation(self, mop_id: str) -> dict | None:
        base_url = self._base_url()
        if not base_url:
            return None
        interval = float(getattr(self.settings, "mop_creation_agent_poll_interval_seconds", 5))
        attempts = int(getattr(self.settings, "mop_creation_agent_poll_attempts", 36))
        url = urljoin(base_url, f"mop-creation/{quote(mop_id, safe='')}")
        pending = {"accepted", "pending", "processing", "running", "queued"}
        latest: dict | None = None
        async with httpx.AsyncClient(timeout=self._timeout(), transport=self.transport) as client:
            for attempt in range(max(1, attempts)):
                if attempt:
                    await asyncio.sleep(interval)
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                payload = response.json()
                latest = payload if isinstance(payload, dict) else {"result": payload}
                if str(latest.get("status") or "").lower() not in pending:
                    return latest
        return latest

    async def _artifact_listing(self, mop_id: str) -> dict | None:
        base_url = self._base_url()
        if not base_url:
            return None
        url = urljoin(base_url, f"mop-creation/{quote(mop_id, safe='')}/artifacts")
        try:
            async with httpx.AsyncClient(timeout=self._timeout(), transport=self.transport) as client:
                response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"result": payload}
        except Exception:
            return None

    @staticmethod
    def _is_generated(payload: dict) -> bool:
        return str(payload.get("status") or "").lower() in {"generated", "completed", "success", "succeeded"}

    @staticmethod
    def _generation_failure_message(payload: dict) -> str:
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        if warnings:
            return "; ".join(str(item) for item in warnings[:4])
        return str(payload.get("error") or payload.get("message") or "MoP Creation Agent did not generate artifacts")

    def _tool_arguments(self, request: ToolExecutionRequest) -> tuple[str, dict]:
        tool_name, arguments = super()._tool_arguments(request)
        alias_map = {
            "mop_create_draft": "mop_creation_generate",
            "create_mop_draft": "mop_creation_generate",
            "generate_mop": "mop_creation_generate",
        }
        tool_name = alias_map.get(tool_name, tool_name)
        if tool_name == "mop_creation_generate":
            arguments = self._generation_arguments(arguments, request)
        return tool_name, arguments

    @staticmethod
    def _generation_arguments(arguments: dict, request: ToolExecutionRequest) -> dict:
        source_namespace = arguments.get("source_namespace") or arguments.get("namespace") or request.namespace
        target_namespace = arguments.get("target_namespace") or arguments.get("target_namespace_placeholder") or "generic-namespace"
        payload = {
            "source_namespace": source_namespace,
            "target_namespace": target_namespace,
            "source_snapshot_id": arguments.get("source_snapshot_id") or "latest",
            "mode": arguments.get("mode") if arguments.get("mode") in {"platform-only", "application"} else "platform-only",
            "include_helm": True,
            "include_raw_k8s": True,
            "include_validation_steps": True,
            "include_rollback_steps": True,
            "include_application_schema": False,
            "output_artifacts": arguments.get("output_artifacts") or ["human_mop_pdf", "installation_notes"],
            "helm_chart_hints": arguments.get("helm_chart_hints") or [],
            "return_content": True,
            "caller": "esda",
            "correlation_id": request.run_id,
        }
        return {key: value for key, value in payload.items() if value is not None}