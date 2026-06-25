from time import perf_counter
from urllib.parse import urlparse

import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult


class RestGetTool:
    name = "rest.get"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        hostname = (parsed.hostname or "").lower()
        for allowed in self.settings.allowed_rest_host_set:
            if allowed == "*" or hostname == allowed:
                return True
            if allowed.startswith("*.") and hostname.endswith(allowed[1:]):
                return True
            if allowed.startswith(".") and hostname.endswith(allowed):
                return True
        return False

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        url = str(request.arguments.get("url", ""))
        if not self._allowed(url):
            return (
                ToolExecutionResult(
                    status="blocked",
                    error={"code": "REST_HOST_DENIED", "message": "URL host is not allowlisted"},
                ),
                int((perf_counter() - start) * 1000),
            )
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(url)
            body_preview = response.text[:4000]
            output = {
                "url": url,
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body_preview": body_preview,
            }
            validation = {
                "valid": 200 <= response.status_code < 400,
                "message": f"HTTP status {response.status_code}",
            }
            return (
                ToolExecutionResult(status="success", output=output, validation_result=validation),
                int((perf_counter() - start) * 1000),
            )
        except Exception as exc:
            return (
                ToolExecutionResult(
                    status="failed",
                    error={"code": "REST_GET_FAILED", "message": str(exc), "retryable": True},
                ),
                int((perf_counter() - start) * 1000),
            )
