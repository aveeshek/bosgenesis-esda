from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import httpx

from backend.app.config import Settings
from backend.app.logging.redaction import redact
from backend.app.tools.mop_agents import redact_sensitive

logger = logging.getLogger("bosgenesis_esda.mop_execution_agent")


def _debug_json(value: Any, *, max_chars: int = 20_000) -> str:
    try:
        rendered = json.dumps(redact_sensitive(redact(value)), default=str, sort_keys=True)
    except Exception:
        rendered = str(redact(value))
    if len(rendered) > max_chars:
        return rendered[:max_chars] + f"...<truncated {len(rendered) - max_chars} chars>"
    return rendered


class MopExecutionAgentError(Exception):
    def __init__(self, *, method: str, url: str, status_code: int | None, payload: Any) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.payload = payload
        message = self._message(payload) or f"MoP Execution Agent request failed: {method} {url}"
        if status_code:
            message = f"{message} (HTTP {status_code})"
        super().__init__(message)

    @staticmethod
    def _message(payload: Any) -> str:
        if isinstance(payload, dict):
            return str(
                payload.get("message") or payload.get("detail") or payload.get("error") or ""
            )
        if isinstance(payload, str):
            return payload[:500]
        return ""


@dataclass(frozen=True)
class MopExecutionAgentResponse:
    method: str
    url: str
    status_code: int
    payload: Any
    redacted_request: dict[str, Any] = field(default_factory=dict)
    redacted_response: Any = field(default_factory=dict)

    def audit_payload(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "request": self.redacted_request,
            "response": self.redacted_response,
        }


class MopExecutionAgentClient:
    """REST client for the governed BOS Genesis MoP Execution Agent.

    ESDA uses this client as an orchestration boundary. Actual dry-run, mutation,
    validation, rollback, cleanup, and report generation remain inside the agent.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        base_url_override: str | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.base_url_override = str(base_url_override or "").strip()

    @property
    def configured(self) -> bool:
        return bool(self._base_url())

    def _base_url(self) -> str:
        if self.base_url_override:
            return self.base_url_override.rstrip("/") + "/"
        for attribute in ("mop_execution_agent_url", "mop_execution_agent_mcp_url"):
            value = str(getattr(self.settings, attribute, "") or "").strip()
            if value:
                return value.rstrip("/") + "/"
        return ""

    def _headers(self) -> dict[str, str]:
        headers = {"accept": "application/json"}
        api_key = str(getattr(self.settings, "mop_execution_agent_api_key", "") or "").strip()
        if api_key:
            header_name = str(
                getattr(self.settings, "mop_execution_agent_auth_header", "x-api-key")
                or "x-api-key"
            )
            if header_name.lower() == "authorization":
                headers[header_name] = (
                    api_key if api_key.lower().startswith("bearer ") else f"Bearer {api_key}"
                )
            else:
                headers[header_name] = api_key
        return headers

    def _timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            float(getattr(self.settings, "mop_execution_agent_timeout_seconds", 300))
        )

    def report_download_url(self, *, job_id: str, report_id: str, artifact: str = "pdf") -> str:
        query = urlencode({"artifact": artifact})
        return urljoin(
            self._base_url(),
            f"v1/execution-jobs/{quote(job_id, safe='')}/reports/{quote(report_id, safe='')}/download?{query}",
        )

    async def download_report(
        self, *, job_id: str, report_id: str, artifact: str = "pdf"
    ) -> tuple[bytes, str, str]:
        url = self.report_download_url(job_id=job_id, report_id=report_id, artifact=artifact)
        if not url:
            raise MopExecutionAgentError(
                method="GET",
                url="report-download",
                status_code=None,
                payload="MoP Execution Agent URL is not configured",
            )
        headers = self._headers()
        headers["accept"] = "*/*"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(), transport=self.transport
            ) as client:
                response = await client.get(url, headers=headers)
            if response.status_code >= 400:
                raise MopExecutionAgentError(
                    method="GET",
                    url=str(response.url),
                    status_code=response.status_code,
                    payload=self._response_payload(response),
                )
            content_type = response.headers.get("content-type") or "application/octet-stream"
            extension = {"markdown": "md", "pdf": "pdf", "html": "html"}.get(
                artifact, artifact or "bin"
            )
            filename = f"{report_id}.{extension}"
            return response.content, content_type, filename
        except httpx.HTTPError as exc:
            raise MopExecutionAgentError(
                method="GET", url=url, status_code=None, payload=str(exc)
            ) from exc

    async def health(self) -> MopExecutionAgentResponse:
        return await self._request("GET", "healthz")

    async def readiness(self) -> MopExecutionAgentResponse:
        return await self._request("GET", "readyz")

    async def capabilities(self) -> MopExecutionAgentResponse:
        return await self._request("GET", "v1/capabilities")

    async def effective_config(self) -> MopExecutionAgentResponse:
        return await self._request("GET", "v1/config/effective")

    async def create_namespace_twin(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        return await self._request("POST", "v1/namespace-twins", json_body=payload)

    async def list_namespace_twins(
        self, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request("GET", "v1/namespace-twins", params=params)

    async def get_namespace_twin(self, twin_id: str) -> MopExecutionAgentResponse:
        return await self._request("GET", f"v1/namespace-twins/{quote(twin_id, safe='')}")

    async def get_namespace_twin_overview(self, twin_id: str) -> MopExecutionAgentResponse:
        return await self._request("GET", f"v1/namespace-twins/{quote(twin_id, safe='')}/overview")

    async def get_namespace_twin_release_delta(
        self, twin_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/release-delta",
            params=params,
        )

    async def get_namespace_twin_dependency_graph(
        self, twin_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/dependency-graph",
            params=params,
        )

    async def get_namespace_twin_policy(
        self, twin_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/policy",
            params=params,
        )

    async def attach_namespace_twin_dry_run_evidence(
        self,
        twin_id: str,
        payload: dict[str, Any],
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/dry-run-evidence",
            json_body=payload,
        )

    async def get_namespace_twin_dry_run(
        self,
        twin_id: str,
        params: dict[str, Any] | None = None,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/dry-run",
            params=params,
        )

    async def get_namespace_twin_rollback(
        self,
        twin_id: str,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/rollback",
        )

    async def get_namespace_twin_drift(
        self,
        twin_id: str,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/drift",
        )

    async def refresh_namespace_twin_drift(
        self,
        twin_id: str,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/drift/refresh",
            json_body={},
        )

    async def get_namespace_twin_runtime_behavior(
        self,
        twin_id: str,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/runtime-behavior",
        )

    async def refresh_namespace_twin_runtime_behavior(
        self,
        twin_id: str,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/runtime-behavior/refresh",
            json_body={},
        )

    async def get_namespace_twin_mop_replay(
        self,
        twin_id: str,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/mop-replay",
        )

    async def record_namespace_twin_mop_replay(
        self,
        twin_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str | None = None,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/mop-replay",
            json_body=payload,
            extra_headers={"x-esda-actor": actor_id} if actor_id else None,
        )

    async def get_namespace_twin_release_note_validation(
        self,
        twin_id: str,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/release-note-validation",
        )

    async def validate_namespace_twin_release_note(
        self,
        twin_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str | None = None,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/release-note-validation",
            json_body=payload,
            extra_headers={"x-esda-actor": actor_id} if actor_id else None,
        )

    async def record_namespace_twin_execution_link(
        self,
        twin_id: str,
        payload: dict[str, Any],
        *,
        actor_id: str | None = None,
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/namespace-twins/{quote(twin_id, safe='')}/execution-links",
            json_body=payload,
            extra_headers={"x-esda-actor": actor_id} if actor_id else None,
        )

    async def get_namespace_twin_actions(self, twin_id: str) -> MopExecutionAgentResponse:
        return await self._request("GET", f"v1/namespace-twins/{quote(twin_id, safe='')}/actions")

    async def get_namespace_twin_events(
        self, twin_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/namespace-twins/{quote(twin_id, safe='')}/events", params=params
        )

    async def get_namespace_twin_audit(
        self, twin_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/namespace-twins/{quote(twin_id, safe='')}/audit", params=params
        )

    async def get_namespace_twin_report(self, twin_id: str) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/namespace-twins/{quote(twin_id, safe='')}/reports/json"
        )

    async def download_namespace_twin_report(
        self, twin_id: str, report_format: str
    ) -> tuple[bytes, str, str]:
        normalized = "markdown" if report_format in {"markdown", "md"} else "json"
        url = urljoin(
            self._base_url(),
            f"v1/namespace-twins/{quote(twin_id, safe='')}/reports/{normalized}",
        )
        headers = self._headers()
        headers["accept"] = "text/markdown" if normalized == "markdown" else "application/json"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(), transport=self.transport
            ) as client:
                response = await client.get(url, headers=headers)
            if response.status_code >= 400:
                raise MopExecutionAgentError(
                    method="GET",
                    url=str(response.url),
                    status_code=response.status_code,
                    payload=self._response_payload(response),
                )
            extension = "md" if normalized == "markdown" else "json"
            content_type = response.headers.get("content-type") or headers["accept"]
            return response.content, content_type, f"{twin_id}-audit-report.{extension}"
        except httpx.HTTPError as exc:
            raise MopExecutionAgentError(
                method="GET", url=url, status_code=None, payload=str(exc)
            ) from exc

    async def cancel_namespace_twin(self, twin_id: str) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/namespace-twins/{quote(twin_id, safe='')}/cancel", json_body={}
        )

    async def validate_bundle(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        bundle_id = str(payload.get("bundle_id") or "").strip()
        if not bundle_id:
            raise MopExecutionAgentError(
                method="POST",
                url="v1/artifact-bundles/{bundle_id}/validate",
                status_code=None,
                payload="bundle_id is required before bundle validation",
            )
        body = dict(payload)
        body.pop("bundle_id", None)
        return await self._request(
            "POST", f"v1/artifact-bundles/{quote(bundle_id, safe='')}/validate", json_body=body
        )

    async def register_bundle(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        return await self._request("POST", "v1/artifact-bundles", json_body=payload)

    async def create_job(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        return await self._request("POST", "v1/execution-jobs", json_body=payload)

    async def list_jobs(self, params: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        return await self._request("GET", "v1/execution-jobs", params=params)

    async def get_job(self, job_id: str) -> MopExecutionAgentResponse:
        return await self._request("GET", f"v1/execution-jobs/{quote(job_id, safe='')}")

    async def start_job(
        self, job_id: str, payload: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/start", json_body=payload or {}
        )

    async def pause_job(
        self, job_id: str, payload: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/pause", json_body=payload or {}
        )

    async def resume_job(
        self, job_id: str, payload: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/resume", json_body=payload or {}
        )

    async def cancel_job(
        self, job_id: str, payload: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/cancel", json_body=payload or {}
        )

    async def get_plan(self, job_id: str) -> MopExecutionAgentResponse:
        return await self._request("GET", f"v1/execution-jobs/{quote(job_id, safe='')}/plan")

    async def get_dry_run_evidence(self, job_id: str) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/execution-jobs/{quote(job_id, safe='')}/dry-run-evidence"
        )

    async def get_observations(
        self, job_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/execution-jobs/{quote(job_id, safe='')}/observations", params=params
        )

    async def get_events(
        self, job_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/execution-jobs/{quote(job_id, safe='')}/events", params=params
        )

    async def get_audit_events(
        self, job_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/execution-jobs/{quote(job_id, safe='')}/audit-events", params=params
        )

    async def get_memory_context(
        self, job_id: str, params: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/execution-jobs/{quote(job_id, safe='')}/memory-context", params=params
        )

    async def get_decision_required_context(self, job_id: str) -> MopExecutionAgentResponse:
        return await self._request(
            "GET", f"v1/execution-jobs/{quote(job_id, safe='')}/decision-required"
        )

    async def submit_instruction(
        self, job_id: str, payload: dict[str, Any]
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/instructions", json_body=payload
        )

    async def submit_approval(
        self, job_id: str, payload: dict[str, Any]
    ) -> MopExecutionAgentResponse:
        try:
            return await self._request(
                "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/approvals", json_body=payload
            )
        except MopExecutionAgentError as rest_exc:
            if not rest_exc.status_code or rest_exc.status_code < 500:
                raise
            try:
                return await self._mcp_tool_call(
                    "mop_execution_submit_approval",
                    {"job_id": job_id, "approval": payload},
                )
            except MopExecutionAgentError as mcp_exc:
                raise MopExecutionAgentError(
                    method="APPROVAL",
                    url=f"{rest_exc.url} -> {mcp_exc.url}",
                    status_code=mcp_exc.status_code or rest_exc.status_code,
                    payload={
                        "message": "MoP Execution Agent approval service failed through both REST and MCP. Redeploy or repair bosgenesis-mop-execution-agent before mutation can proceed.",
                        "rest_status_code": rest_exc.status_code,
                        "rest_error": rest_exc.payload,
                        "mcp_status_code": mcp_exc.status_code,
                        "mcp_error": mcp_exc.payload,
                    },
                ) from mcp_exc

    async def list_reports(self, job_id: str) -> MopExecutionAgentResponse:
        return await self._request("GET", f"v1/execution-jobs/{quote(job_id, safe='')}/reports")

    async def get_report_metadata(self, job_id: str, report_id: str) -> MopExecutionAgentResponse:
        return await self._request(
            "GET",
            f"v1/execution-jobs/{quote(job_id, safe='')}/reports/{quote(report_id, safe='')}",
        )

    async def generate_report(self, job_id: str, report_type: str) -> MopExecutionAgentResponse:
        endpoint = {
            "dry_run": "execution-summary",
            "dry-run": "execution-summary",
            "execution": "execution-summary",
            "execution_summary": "execution-summary",
            "execution-summary": "execution-summary",
            "validation": "validation",
            "validation_report": "validation",
            "rollback": "rollback",
            "rollback_report": "rollback",
            "change": "change-summary",
            "change_report": "change-summary",
            "change-summary": "change-summary",
        }.get(
            str(report_type or "").strip().lower(),
            str(report_type or "execution-summary").strip().replace("_", "-"),
        )
        return await self._request(
            "POST",
            f"v1/execution-jobs/{quote(job_id, safe='')}/reports/{quote(endpoint, safe='')}",
            json_body={},
        )

    async def generate_release_notes(
        self, job_id: str, payload: dict[str, Any] | None = None
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/execution-jobs/{quote(job_id, safe='')}/reports/release-notes",
            json_body=payload or {},
        )

    async def run_validation(self, job_id: str) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/validate", json_body={}
        )

    async def request_rollback(
        self, job_id: str, payload: dict[str, Any]
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST", f"v1/execution-jobs/{quote(job_id, safe='')}/rollback", json_body=payload
        )

    async def execute_rollback(
        self, job_id: str, payload: dict[str, Any]
    ) -> MopExecutionAgentResponse:
        return await self._request(
            "POST",
            f"v1/execution-jobs/{quote(job_id, safe='')}/rollback/execute",
            json_body=payload,
        )

    async def request_cleanup(
        self, job_id: str, payload: dict[str, Any]
    ) -> MopExecutionAgentResponse:
        namespace = str(payload.get("target_namespace") or payload.get("namespace") or "").strip()
        if namespace:
            return await self.revert_namespace({"target_namespace": namespace, **payload})
        return await self._request(
            "POST",
            f"v1/execution-jobs/{quote(job_id, safe='')}/rollback",
            json_body={"cleanup_requested": True, **payload},
        )

    async def revert_namespace(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        namespace = str(payload.get("target_namespace") or payload.get("namespace") or "").strip()
        if not namespace:
            raise MopExecutionAgentError(
                method="POST",
                url="v1/namespaces/{namespace}/revert",
                status_code=None,
                payload="target_namespace is required before namespace revert",
            )
        return await self._request(
            "POST", f"v1/namespaces/{quote(namespace, safe='')}/revert", json_body=payload
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> MopExecutionAgentResponse:
        base_url = self._base_url()
        if not base_url:
            raise MopExecutionAgentError(
                method=method,
                url=path,
                status_code=None,
                payload="MoP Execution Agent URL is not configured",
            )
        url = urljoin(base_url, path.lstrip("/"))
        redacted_request = self._redacted_payload({"json": json_body or {}, "params": params or {}})
        logger.debug(
            "mop_execution_agent_request method=%s url=%s payload=%s",
            method,
            url,
            _debug_json(redacted_request),
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(), transport=self.transport
            ) as client:
                headers = self._headers()
                headers.update(extra_headers or {})
                response = await client.request(
                    method, url, json=json_body, params=params, headers=headers
                )
            payload = self._response_payload(response)
            redacted_response = self._redacted_payload(payload)
            logger.debug(
                "mop_execution_agent_response method=%s url=%s status=%s payload=%s",
                method,
                str(response.url),
                response.status_code,
                _debug_json(redacted_response),
            )
            if response.status_code >= 400:
                logger.error(
                    "mop_execution_agent_error_response method=%s url=%s status=%s payload=%s",
                    method,
                    str(response.url),
                    response.status_code,
                    _debug_json(redacted_response),
                )
                raise MopExecutionAgentError(
                    method=method,
                    url=str(response.url),
                    status_code=response.status_code,
                    payload=payload,
                )
            return MopExecutionAgentResponse(
                method=method,
                url=str(response.url),
                status_code=response.status_code,
                payload=payload,
                redacted_request=redacted_request,
                redacted_response=redacted_response,
            )
        except httpx.HTTPError as exc:
            logger.exception("mop_execution_agent_http_error method=%s url=%s", method, url)
            raise MopExecutionAgentError(
                method=method, url=url, status_code=None, payload=str(exc)
            ) from exc

    async def _mcp_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> MopExecutionAgentResponse:
        base_url = self._base_url()
        if not base_url:
            raise MopExecutionAgentError(
                method="MCP",
                url="mcp",
                status_code=None,
                payload="MoP Execution Agent URL is not configured",
            )
        url = urljoin(base_url, "mcp")
        body = {
            "jsonrpc": "2.0",
            "id": f"{tool_name}-fallback",
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        redacted_request = self._redacted_payload({"json": body, "params": {}})
        logger.debug(
            "mop_execution_agent_mcp_request tool=%s url=%s payload=%s",
            tool_name,
            url,
            _debug_json(redacted_request),
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(), transport=self.transport
            ) as client:
                response = await client.post(url, json=body, headers=self._headers())
            payload = self._response_payload(response)
            logger.debug(
                "mop_execution_agent_mcp_response tool=%s url=%s status=%s payload=%s",
                tool_name,
                str(response.url),
                response.status_code,
                _debug_json(payload),
            )
            if response.status_code >= 400:
                logger.error(
                    "mop_execution_agent_mcp_error_response tool=%s url=%s status=%s payload=%s",
                    tool_name,
                    str(response.url),
                    response.status_code,
                    _debug_json(payload),
                )
                raise MopExecutionAgentError(
                    method="MCP",
                    url=str(response.url),
                    status_code=response.status_code,
                    payload=payload,
                )
            if isinstance(payload, dict) and payload.get("error"):
                logger.error(
                    "mop_execution_agent_mcp_tool_error tool=%s url=%s payload=%s",
                    tool_name,
                    str(response.url),
                    _debug_json(payload["error"]),
                )
                raise MopExecutionAgentError(
                    method="MCP",
                    url=str(response.url),
                    status_code=response.status_code,
                    payload=payload["error"],
                )
            result_payload = self._coerce_mcp_result(payload)
            redacted_response = self._redacted_payload(result_payload)
            return MopExecutionAgentResponse(
                method="MCP",
                url=str(response.url),
                status_code=response.status_code,
                payload=result_payload,
                redacted_request=redacted_request,
                redacted_response=redacted_response,
            )
        except httpx.HTTPError as exc:
            logger.exception("mop_execution_agent_mcp_http_error tool=%s url=%s", tool_name, url)
            raise MopExecutionAgentError(
                method="MCP", url=url, status_code=None, payload=str(exc)
            ) from exc

    @staticmethod
    def _redacted_payload(value: Any) -> Any:
        return redact_sensitive(redact(value))

    @staticmethod
    def _response_payload(response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            return response.json()
        text = response.text
        return {"text": text} if text else {}

    @staticmethod
    def _coerce_mcp_result(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        result = payload.get("result", payload)
        if not isinstance(result, dict):
            return result
        structured = result.get("structuredContent") or result.get("structured_content")
        if isinstance(structured, dict):
            return structured
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                text = item.get("text") if isinstance(item, dict) else None
                if not text:
                    continue
                try:
                    return json.loads(text)
                except (TypeError, ValueError):
                    return {"text": text, "mcp_result": result}
        return result
