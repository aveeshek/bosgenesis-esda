from time import perf_counter

import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult


class PowerShellGetTemplateTool:
    name = "powershell.ps_http_get"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        url = str(request.arguments.get("url", ""))
        payload = {
            "template_id": "ps_http_get",
            "parameters": {
                "url": url,
                "timeout_seconds": 20,
            },
            "run_id": request.run_id,
            "step_id": request.step_id,
            "user_id": request.user_id,
        }
        try:
            if self.settings.powershell_runner_url:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(self.settings.powershell_runner_url, json=payload)
                response.raise_for_status()
                output = response.json()
            else:
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.get(url)
                output = {
                    "template_id": "ps_http_get",
                    "runner": "backend_http_fallback",
                    "status_code": response.status_code,
                    "body_preview": response.text[:4000],
                }
            validation = {
                "valid": 200 <= int(output.get("status_code", 200)) < 400,
                "message": "PowerShell GET template completed",
            }
            return (
                ToolExecutionResult(status="success", output=output, validation_result=validation),
                int((perf_counter() - start) * 1000),
            )
        except Exception as exc:
            return (
                ToolExecutionResult(
                    status="failed",
                    error={"code": "PS_HTTP_GET_FAILED", "message": str(exc), "retryable": True},
                ),
                int((perf_counter() - start) * 1000),
            )
