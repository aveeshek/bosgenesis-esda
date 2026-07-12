from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from time import perf_counter
from typing import Any, Literal
from urllib.parse import quote, urljoin

import httpx
from pydantic import BaseModel, Field

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult
from backend.app.tools.mop_agents import redact_sensitive

LOG_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|password|passwd|token|secret|api[_-]?key|credential)\b\s*[:=]\s*([^\s,;]+)"
)
AUTH_HEADER_PATTERN = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*(?:bearer|basic)\s+[^\s,;]+")


class EnvAgentEvidenceRecord(BaseModel):
    evidence_id: str
    workflow_type: str = "env_agent"
    tool_name: str
    source_agent: str
    source_endpoint: str | None = None
    source_type: str
    namespace: str | None = None
    resource_kind: str | None = None
    resource_name: str | None = None
    action: str
    risk_level: Literal["low", "medium", "high", "critical"]
    status: Literal["success", "failed", "blocked"]
    confidence: float = Field(ge=0, le=1)
    summary: str
    request_redacted: dict = Field(default_factory=dict)
    observation_redacted: Any = None
    metadata: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class EnvAgentRoute:
    method: str
    path: str
    resource_kind: str | None
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    required_arguments: tuple[str, ...] = ()
    summary: str = ""


def redact_env_payload(value: Any, *, max_string_chars: int = 4000, max_list_items: int = 200) -> Any:
    redacted = redact_sensitive(value)
    return _truncate_and_redact_logs(redacted, max_string_chars=max_string_chars, max_list_items=max_list_items)


def _truncate_and_redact_logs(value: Any, *, max_string_chars: int, max_list_items: int) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in ("password", "secret", "token", "api_key", "apikey", "credential")):
                output[key] = "***"
            else:
                output[key] = _truncate_and_redact_logs(
                    item,
                    max_string_chars=max_string_chars,
                    max_list_items=max_list_items,
                )
        return output
    if isinstance(value, list):
        items = [
            _truncate_and_redact_logs(item, max_string_chars=max_string_chars, max_list_items=max_list_items)
            for item in value[:max_list_items]
        ]
        if len(value) > max_list_items:
            items.append({"truncated_items": len(value) - max_list_items})
        return items
    if isinstance(value, str):
        cleaned = AUTH_HEADER_PATTERN.sub("Authorization=***", value)
        cleaned = LOG_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=***", cleaned)
        if len(cleaned) > max_string_chars:
            return cleaned[:max_string_chars] + f"...<truncated {len(cleaned) - max_string_chars} chars>"
        return cleaned
    return value


class EnvAgentBaseTool:
    name = "env.base"
    source_type = "env_base"
    configured_url_attributes: tuple[str, ...] = ()
    timeout_attribute = "env_agent_tool_timeout_seconds"
    missing_config_code = "ENV_AGENT_TOOL_NOT_CONFIGURED"
    unsupported_tool_code = "ENV_AGENT_TOOL_NOT_ALLOWED"
    failed_code = "ENV_AGENT_TOOL_CALL_FAILED"
    route_map: dict[str, EnvAgentRoute] = {}

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

    def _max_string_chars(self) -> int:
        return int(getattr(self.settings, "env_agent_max_observation_chars", 4000))

    def _tool_arguments(self, request: ToolExecutionRequest) -> tuple[str, dict]:
        tool_name = str(request.arguments.get("tool_name") or "").strip()
        if not tool_name:
            tool_name = self.default_tool_name()
        arguments = request.arguments.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {
                key: value
                for key, value in request.arguments.items()
                if key not in {"tool_name", "arguments"}
            }
        arguments = dict(arguments)
        if request.namespace and "namespace" not in arguments:
            arguments["namespace"] = request.namespace
        if request.environment and "environment" not in arguments:
            arguments["environment"] = request.environment
        return tool_name, arguments

    def default_tool_name(self) -> str:
        return next(iter(self.route_map))

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        base_url = self._base_url()
        if not base_url:
            return self._failed(start, self.missing_config_code, f"{self.name} URL is not configured", retryable=False)

        tool_name, arguments = self._tool_arguments(request)
        route = self.route_map.get(tool_name)
        if not route:
            return self._blocked(start, self.unsupported_tool_code, f"Unsupported {self.name} tool: {tool_name}", request, tool_name, arguments)
        missing = [argument for argument in route.required_arguments if not str(arguments.get(argument) or "").strip()]
        if missing:
            return self._blocked(
                start,
                "ENV_AGENT_TOOL_ARGUMENT_MISSING",
                f"Missing required argument(s) for {tool_name}: {', '.join(missing)}",
                request,
                tool_name,
                arguments,
                route=route,
            )

        try:
            method, url, query = self._request_parts(base_url, route, arguments)
            async with httpx.AsyncClient(timeout=self._timeout(), transport=self.transport) as client:
                if method == "GET":
                    response = await client.get(url, params=query, headers=self._headers())
                else:
                    response = await client.request(method, url, json=arguments, headers=self._headers())
            response.raise_for_status()
            payload = self._decode_response(response)
            duration_ms = int((perf_counter() - start) * 1000)
            evidence = self._evidence_record(
                request=request,
                tool_name=tool_name,
                route=route,
                arguments=arguments,
                status="success",
                source_endpoint=str(response.url),
                observation=payload,
                confidence=self._confidence(tool_name, payload),
                duration_ms=duration_ms,
            )
            return (
                ToolExecutionResult(
                    status="success",
                    output={"evidence": evidence.model_dump(), "raw": evidence.observation_redacted},
                    evidence_refs=[str(response.url)],
                    validation_result={"valid": True, "message": f"HTTP status {response.status_code}"},
                ),
                duration_ms,
            )
        except httpx.HTTPStatusError as exc:
            return self._failed(start, self.failed_code, self._http_error_message(exc), retryable=exc.response.status_code >= 500, request=request, tool_name=tool_name, arguments=arguments, route=route)
        except Exception as exc:
            return self._failed(start, self.failed_code, str(exc), retryable=True, request=request, tool_name=tool_name, arguments=arguments, route=route)

    def _request_parts(self, base_url: str, route: EnvAgentRoute, arguments: dict) -> tuple[str, str, dict]:
        path = route.path
        for key, value in arguments.items():
            path = path.replace("{" + key + "}", quote(str(value), safe=""))
        query = self._query_arguments(arguments, route)
        return route.method, urljoin(base_url, path.lstrip("/")), query

    def _query_arguments(self, arguments: dict, route: EnvAgentRoute) -> dict:
        excluded = set(route.required_arguments) | {"environment", "resource_kind", "resource_name", "pod_name", "release_name", "name"}
        query = {key: value for key, value in arguments.items() if value is not None and key not in excluded}
        query.setdefault("actor", "esda")
        return query

    @staticmethod
    def _decode_response(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    @staticmethod
    def _http_error_message(exc: httpx.HTTPStatusError) -> str:
        try:
            payload = exc.response.json()
            if isinstance(payload, dict):
                return str(payload.get("message") or payload.get("detail") or exc)
        except ValueError:
            pass
        return str(exc)

    def _confidence(self, tool_name: str, payload: Any) -> float:
        if isinstance(payload, dict) and payload.get("ok") is False:
            return 0.15
        if tool_name in {"logs", "pod_logs"}:
            return 0.74
        return 0.86

    def _summary(self, tool_name: str, route: EnvAgentRoute, namespace: str | None, resource_name: str | None) -> str:
        if route.summary:
            target = resource_name or namespace
            return route.summary.format(namespace=namespace or "unknown", resource_name=target or "selected target")
        return f"Collected {tool_name} evidence from {self.name}."

    def _evidence_record(
        self,
        *,
        request: ToolExecutionRequest,
        tool_name: str,
        route: EnvAgentRoute | None,
        arguments: dict,
        status: Literal["success", "failed", "blocked"],
        source_endpoint: str | None,
        observation: Any,
        confidence: float,
        duration_ms: int,
        message: str | None = None,
    ) -> EnvAgentEvidenceRecord:
        namespace = str(arguments.get("namespace") or request.namespace or "") or None
        resource_name = str(
            arguments.get("resource_name")
            or arguments.get("pod_name")
            or arguments.get("release_name")
            or arguments.get("name")
            or ""
        ) or None
        route = route or EnvAgentRoute("GET", "", None, "low")
        observation_redacted = redact_env_payload(
            observation,
            max_string_chars=self._max_string_chars(),
        )
        summary = message or self._summary(tool_name, route, namespace, resource_name)
        return EnvAgentEvidenceRecord(
            evidence_id=f"evi_{request.run_id}_{request.step_id}_{tool_name}",
            tool_name=self.name,
            source_agent=self.name,
            source_endpoint=source_endpoint,
            source_type=self.source_type,
            namespace=namespace,
            resource_kind=route.resource_kind,
            resource_name=resource_name,
            action=tool_name,
            risk_level=route.risk_level,
            status=status,
            confidence=confidence,
            summary=summary,
            request_redacted=redact_env_payload(arguments, max_string_chars=self._max_string_chars()),
            observation_redacted=observation_redacted,
            metadata={
                "duration_ms": duration_ms,
                "environment": request.environment,
                "autonomy_mode": request.autonomy_mode,
                "normalized": True,
            },
        )

    def _failed(
        self,
        start: float,
        code: str,
        message: str,
        *,
        retryable: bool,
        request: ToolExecutionRequest | None = None,
        tool_name: str | None = None,
        arguments: dict | None = None,
        route: EnvAgentRoute | None = None,
    ) -> tuple[ToolExecutionResult, int]:
        duration_ms = int((perf_counter() - start) * 1000)
        output = None
        if request and tool_name:
            evidence = self._evidence_record(
                request=request,
                tool_name=tool_name,
                route=route,
                arguments=arguments or {},
                status="failed",
                source_endpoint=None,
                observation={"error": {"code": code, "message": message, "retryable": retryable}},
                confidence=0.0,
                duration_ms=duration_ms,
                message=message,
            )
            output = {"evidence": evidence.model_dump(), "raw": evidence.observation_redacted}
        return (
            ToolExecutionResult(
                status="failed",
                output=output,
                error={"code": code, "message": message, "retryable": retryable},
            ),
            duration_ms,
        )

    def _blocked(
        self,
        start: float,
        code: str,
        message: str,
        request: ToolExecutionRequest,
        tool_name: str,
        arguments: dict,
        *,
        route: EnvAgentRoute | None = None,
    ) -> tuple[ToolExecutionResult, int]:
        duration_ms = int((perf_counter() - start) * 1000)
        evidence = self._evidence_record(
            request=request,
            tool_name=tool_name,
            route=route,
            arguments=arguments,
            status="blocked",
            source_endpoint=None,
            observation={"blocked": True, "code": code, "message": message},
            confidence=0.0,
            duration_ms=duration_ms,
            message=message,
        )
        return (
            ToolExecutionResult(
                status="blocked",
                output={"evidence": evidence.model_dump(), "raw": evidence.observation_redacted},
                error={"code": code, "message": message, "retryable": False},
            ),
            duration_ms,
        )


class EnvAgentK8sInspectorTool(EnvAgentBaseTool):
    name = "env.k8s_inspector"
    source_type = "k8s_inspector"
    configured_url_attributes = ("k8s_inspector_agent_mcp_url", "mcp_k8s_inspector_url")
    timeout_attribute = "k8s_inspector_agent_timeout_seconds"
    missing_config_code = "ENV_K8S_INSPECTOR_NOT_CONFIGURED"
    unsupported_tool_code = "ENV_K8S_INSPECTOR_TOOL_NOT_ALLOWED"
    failed_code = "ENV_K8S_INSPECTOR_CALL_FAILED"
    route_map = {
        "namespace_summary": EnvAgentRoute("GET", "/namespace/summary", "namespace", "low", summary="Collected namespace summary for {namespace}."),
        "pod_health": EnvAgentRoute("GET", "/pods", "pod", "low", summary="Collected pod health evidence for {namespace}."),
        "restart_analysis": EnvAgentRoute("GET", "/pods", "pod", "low", summary="Collected pod restart evidence for {namespace}."),
        "events": EnvAgentRoute("GET", "/events", "event", "low", summary="Collected namespace event evidence for {namespace}."),
        "logs": EnvAgentRoute("GET", "/pods/{pod_name}/logs", "pod", "medium", ("pod_name",), "Collected bounded pod logs for {resource_name}."),
        "deployment_status": EnvAgentRoute("GET", "/deployments", "deployment", "low", summary="Collected deployment readiness evidence for {namespace}."),
        "service_status": EnvAgentRoute("GET", "/services", "service", "low", summary="Collected service evidence for {namespace}."),
        "ingress_status": EnvAgentRoute("GET", "/ingresses", "ingress", "low", summary="Collected ingress evidence for {namespace}."),
        "pvc_checks": EnvAgentRoute("GET", "/pvcs", "pvc", "low", summary="Collected PVC evidence for {namespace}."),
        "configmap_summary": EnvAgentRoute("GET", "/configmaps", "configmap", "low", summary="Collected ConfigMap metadata for {namespace}."),
        "rollout_restart": EnvAgentRoute("POST", "/workloads/{resource_kind}/{resource_name}/restart", "deployment", "high", ("resource_kind", "resource_name"), "Submitted approved rollout restart for {resource_name}."),
        "scale": EnvAgentRoute("POST", "/workloads/{resource_kind}/{resource_name}/scale", "deployment", "high", ("resource_kind", "resource_name", "replicas"), "Submitted approved scale action for {resource_name}."),
        "patch": EnvAgentRoute("PATCH", "/resources/{resource_kind}/{resource_name}", "resource", "high", ("resource_kind", "resource_name", "patch"), "Submitted approved patch for {resource_name}."),
        "apply": EnvAgentRoute("POST", "/resources/apply", "resource", "high", ("manifest",), "Submitted approved apply for {resource_name}."),
        "delete": EnvAgentRoute("DELETE", "/resources/{resource_kind}/{resource_name}", "resource", "high", ("resource_kind", "resource_name"), "Submitted approved delete for {resource_name}."),
    }

    def _headers(self) -> dict[str, str]:
        api_key = getattr(self.settings, "mcp_k8s_inspector_api_key", "")
        return {"x-api-key": api_key} if api_key else {}

    def _query_arguments(self, arguments: dict, route: EnvAgentRoute) -> dict:
        query = super()._query_arguments(arguments, route)
        if route.path.endswith("/logs"):
            query.setdefault("tail_lines", getattr(self.settings, "env_agent_log_tail_lines", 200))
        if route.resource_kind == "configmap":
            query.setdefault("include_data", False)
        return query


class EnvAgentHelmManagerTool(EnvAgentBaseTool):
    name = "env.helm_manager"
    source_type = "helm_manager"
    configured_url_attributes = ("helm_manager_agent_mcp_url",)
    timeout_attribute = "helm_manager_agent_timeout_seconds"
    missing_config_code = "ENV_HELM_MANAGER_NOT_CONFIGURED"
    unsupported_tool_code = "ENV_HELM_MANAGER_TOOL_NOT_ALLOWED"
    failed_code = "ENV_HELM_MANAGER_CALL_FAILED"
    mutating_tools = {
        "helm_install",
        "helm_upgrade",
        "helm_uninstall",
        "helm_rollback",
        "helm_repo_add",
        "helm_repo_update",
    }
    route_map = {
        "helm_release_list": EnvAgentRoute("GET", "/releases", "helm_release", "low", summary="Collected Helm release list for {namespace}."),
        "helm_release_status": EnvAgentRoute("GET", "/releases/{release_name}/status", "helm_release", "low", ("release_name",), "Collected Helm release status for {resource_name}."),
        "helm_release_history": EnvAgentRoute("GET", "/releases/{release_name}/history", "helm_release", "low", ("release_name",), "Collected Helm release history for {resource_name}."),
        "helm_values_summary": EnvAgentRoute("GET", "/releases/{release_name}/values", "helm_release", "low", ("release_name",), "Collected redacted Helm values summary for {resource_name}."),
        "helm_rollback_candidates": EnvAgentRoute("GET", "/releases/{release_name}/history", "helm_release", "low", ("release_name",), "Collected Helm rollback candidate evidence for {resource_name}."),
        "helm_install": EnvAgentRoute("POST", "/releases/install", "helm_release", "high", ("release_name", "chart_ref"), "Submitted approved Helm install for {resource_name}."),
        "helm_upgrade": EnvAgentRoute("POST", "/releases/{release_name}/upgrade", "helm_release", "high", ("release_name", "chart_ref"), "Submitted approved Helm upgrade for {resource_name}."),
        "helm_uninstall": EnvAgentRoute("DELETE", "/releases/{release_name}", "helm_release", "high", ("release_name",), "Submitted approved Helm uninstall for {resource_name}."),
        "helm_rollback": EnvAgentRoute("POST", "/releases/{release_name}/rollback", "helm_release", "high", ("release_name",), "Submitted approved Helm rollback for {resource_name}."),
        "helm_repo_list": EnvAgentRoute("GET", "/repos", "helm_repository", "low", summary="Collected Helm repository list."),
        "helm_repo_add": EnvAgentRoute("POST", "/repos/add", "helm_repository", "high", ("name", "url"), "Submitted approved Helm repo add for {resource_name}."),
        "helm_repo_update": EnvAgentRoute("POST", "/repos/update", "helm_repository", "high", summary="Submitted approved Helm repo update."),
    }

    def _tool_arguments(self, request: ToolExecutionRequest) -> tuple[str, dict]:
        tool_name, arguments = super()._tool_arguments(request)
        api_key = str(getattr(self.settings, "helm_manager_agent_api_key", "") or "").strip()
        if api_key and tool_name in self.mutating_tools and not arguments.get("api_key"):
            arguments["api_key"] = api_key
        return tool_name, arguments

    def _headers(self) -> dict[str, str]:
        api_key = getattr(self.settings, "helm_manager_agent_api_key", "")
        return {"x-api-key": api_key} if api_key else {}


class EnvAgentDataIngestionTool(EnvAgentBaseTool):
    name = "env.data_ingestion"
    source_type = "data_ingestion"
    configured_url_attributes = ("env_agent_data_ingestion_url",)
    timeout_attribute = "env_agent_optional_tool_timeout_seconds"
    missing_config_code = "ENV_DATA_INGESTION_NOT_CONFIGURED"
    unsupported_tool_code = "ENV_DATA_INGESTION_TOOL_NOT_ALLOWED"
    failed_code = "ENV_DATA_INGESTION_CALL_FAILED"
    route_map = {
        "namespace_snapshot": EnvAgentRoute("GET", "/snapshots/namespace/{namespace}", "namespace", "low", ("namespace",), "Collected prior namespace snapshot for {namespace}."),
        "inventory_lookup": EnvAgentRoute("GET", "/inventory/namespace/{namespace}", "namespace", "low", ("namespace",), "Collected prior inventory lookup for {namespace}."),
    }


class EnvAgentObservabilityTool(EnvAgentBaseTool):
    name = "env.observability"
    source_type = "observability"
    configured_url_attributes = ("env_agent_observability_url",)
    timeout_attribute = "env_agent_optional_tool_timeout_seconds"
    missing_config_code = "ENV_OBSERVABILITY_NOT_CONFIGURED"
    unsupported_tool_code = "ENV_OBSERVABILITY_TOOL_NOT_ALLOWED"
    failed_code = "ENV_OBSERVABILITY_CALL_FAILED"
    route_map = {
        "recent_traces": EnvAgentRoute("GET", "/traces/recent", "trace", "low", summary="Collected recent trace context for {namespace}."),
        "error_spans": EnvAgentRoute("GET", "/traces/errors", "trace", "low", summary="Collected recent error span context for {namespace}."),
        "metrics_query": EnvAgentRoute("GET", "/metrics/query", "metric", "low", summary="Collected metric context for {namespace}."),
    }


