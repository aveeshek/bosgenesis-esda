from time import perf_counter
from urllib.parse import urljoin

import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult


class K8sInspectorMcpTool:
    name = "mcp.k8s_inspector"

    _tool_routes = {
        "list_pods": "/pods",
        "namespace_summary": "/namespace/summary",
        "list_events": "/events",
        "list_ingresses": "/ingresses",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def route_for_tool(cls, tool_name: str) -> str | None:
        return cls._tool_routes.get(tool_name)

    def _headers(self) -> dict[str, str]:
        api_key = getattr(self.settings, "mcp_k8s_inspector_api_key", "")
        return {"x-api-key": api_key} if api_key else {}

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        if not self.settings.mcp_k8s_inspector_url:
            return (
                ToolExecutionResult(
                    status="failed",
                    error={
                        "code": "MCP_NOT_CONFIGURED",
                        "message": "MCP_K8S_INSPECTOR_URL is not configured",
                        "retryable": False,
                    },
                ),
                int((perf_counter() - start) * 1000),
            )

        tool_name = request.arguments.get("tool_name", "list_pods")
        route = self.route_for_tool(str(tool_name))
        if route is None:
            return (
                ToolExecutionResult(
                    status="blocked",
                    error={
                        "code": "MCP_TOOL_NOT_ALLOWED",
                        "message": f"Unsupported k8s inspector tool: {tool_name}",
                        "retryable": False,
                    },
                ),
                int((perf_counter() - start) * 1000),
            )

        tool_arguments = request.arguments.get("arguments", {})
        namespace = tool_arguments.get("namespace") or request.namespace
        params = {"actor": "bosgenesis-esda"}
        if namespace:
            params["namespace"] = namespace

        url = urljoin(self.settings.mcp_k8s_inspector_url.rstrip("/") + "/", route.lstrip("/"))
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=params, headers=self._headers())
            response.raise_for_status()
            result = response.json()
            normalized = {
                "status": "success",
                "tool_name": tool_name,
                "url": str(response.url),
                "normalized_result": result,
                "evidence_refs": [str(response.url)],
                "error": None,
            }
            return (
                ToolExecutionResult(
                    status="success",
                    output=normalized,
                    evidence_refs=normalized["evidence_refs"],
                    validation_result={"valid": True, "message": f"HTTP status {response.status_code}"},
                ),
                int((perf_counter() - start) * 1000),
            )
        except Exception as exc:
            return (
                ToolExecutionResult(
                    status="failed",
                    error={"code": "MCP_CALL_FAILED", "message": str(exc), "retryable": True},
                ),
                int((perf_counter() - start) * 1000),
            )
