import asyncio
from time import perf_counter
from urllib.parse import urljoin, urlparse

import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult


MAX_HYDRATED_ARTIFACT_CHARS = 250000
TEXT_ARTIFACT_TYPES = {"analytics", "evidence", "json", "markdown", "metadata", "observability"}
TERMINAL_STATUSES = {"completed", "complete", "succeeded", "success", "finished", "failed", "error"}


class ReleaseNoteAgentMcpError(Exception):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        message = str(payload.get("message") or payload.get("error") or "MCP tool call failed")
        super().__init__(message)


class ReleaseNoteAgentTool:
    name = "release_notes.agent_scan"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _allowed_github_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        hostname = (parsed.hostname or "").lower()
        return hostname in self.settings.allowed_github_host_set

    def _base_url(self) -> str:
        return self.settings.release_note_agent_url.rstrip("/") + "/"

    def _mcp_base_url(self) -> str:
        return self.settings.release_note_agent_mcp_url.rstrip("/") + "/"

    def _request_timeout(self) -> httpx.Timeout:
        timeout = float(self.settings.release_note_agent_timeout_seconds)
        return httpx.Timeout(timeout, connect=min(timeout, 30.0))

    def _use_mcp_transport(self) -> bool:
        transport = self.settings.release_note_agent_transport.lower()
        if transport == "mcp":
            return True
        if transport == "rest":
            return False
        return bool(self.settings.release_note_agent_mcp_url)

    def _configured(self) -> bool:
        if self._use_mcp_transport():
            return bool(self.settings.release_note_agent_mcp_url)
        return bool(self.settings.release_note_agent_url)

    def _payload(self, request: ToolExecutionRequest) -> dict:
        arguments = self.normalize_ref_arguments(request.arguments)
        output_formats = arguments.get("output_formats") or ["markdown", "json"]
        return {
            "repo_url": str(arguments.get("github_url", "")),
            "branch": arguments.get("branch") or None,
            "tag": arguments.get("tag") or None,
            "commit_sha": arguments.get("commit_sha") or None,
            "release_name": arguments.get("release_name") or None,
            "analysis_depth": arguments.get("analysis_depth") or "fast",
            "output_formats": output_formats,
        }

    @staticmethod
    def normalize_ref_arguments(arguments: dict) -> dict:
        normalized = dict(arguments)
        refs = {
            "branch": normalized.get("branch") or None,
            "tag": normalized.get("tag") or None,
            "commit_sha": normalized.get("commit_sha") or None,
        }
        selected_type = None
        for ref_type in ("commit_sha", "tag", "branch"):
            if refs[ref_type]:
                selected_type = ref_type
                break
        omitted_refs = {}
        for ref_type, value in refs.items():
            if ref_type == selected_type:
                normalized[ref_type] = value
            else:
                normalized[ref_type] = None
                if value:
                    omitted_refs[ref_type] = value
        normalized["selected_ref"] = (
            {"type": selected_type, "value": refs[selected_type]} if selected_type else None
        )
        if omitted_refs:
            normalized["omitted_refs"] = omitted_refs
        else:
            normalized.pop("omitted_refs", None)
        return normalized

    async def execute(self, request: ToolExecutionRequest) -> tuple[ToolExecutionResult, int]:
        start = perf_counter()
        payload = self._payload(request)
        github_url = payload["repo_url"]
        if not self._allowed_github_url(github_url):
            return (
                ToolExecutionResult(
                    status="blocked",
                    error={
                        "code": "GITHUB_URL_DENIED",
                        "message": "GitHub URL must use an allowlisted host",
                    },
                ),
                int((perf_counter() - start) * 1000),
            )
        if not self._configured():
            return (
                ToolExecutionResult(
                    status="failed",
                    error={
                        "code": "RELEASE_NOTE_AGENT_NOT_CONFIGURED",
                        "message": (
                            "RELEASE_NOTE_AGENT_MCP_URL or RELEASE_NOTE_AGENT_URL "
                            "must be configured"
                        ),
                        "retryable": False,
                    },
                ),
                int((perf_counter() - start) * 1000),
            )

        if self._use_mcp_transport():
            return await self._execute_mcp(payload, github_url, start)
        return await self._execute_rest(payload, github_url, start)

    async def _execute_mcp(
        self,
        payload: dict,
        github_url: str,
        start: float,
    ) -> tuple[ToolExecutionResult, int]:
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
                created = await self._mcp_tool_call(
                    client,
                    "github_release_scan_start",
                    payload,
                )
                job_id = self._extract_job_id(created)
                scan_json = created
                if job_id and self._status(created) not in TERMINAL_STATUSES:
                    scan_json = await self._poll_mcp_scan(client, job_id)
                note_json = {}
                artifact_index = {}
                if job_id:
                    note_json = await self._mcp_tool_call(
                        client,
                        "github_release_generate_note",
                        {"job_id": job_id},
                    )
                    artifact_index = await self._mcp_tool_call(
                        client,
                        "github_release_get_artifact",
                        {"job_id": job_id},
                    )
                artifacts = self._artifact_items(note_json)
                if not artifacts:
                    artifacts = self._artifact_items(artifact_index)
                if not artifacts:
                    artifacts = self._artifact_items(created)
                artifacts = await self._hydrate_artifacts(client, job_id, artifacts) if job_id else artifacts
            output = {
                "transport": "mcp",
                "job_id": job_id,
                "request": payload,
                "scan": scan_json,
                "note": note_json,
                "artifact_index": artifact_index,
                "artifacts": artifacts,
            }
            validation = {
                "valid": True,
                "message": f"release-note-agent MCP scan status {self._status(scan_json)}",
            }
            return (
                ToolExecutionResult(
                    status="success",
                    output=output,
                    evidence_refs=[github_url],
                    validation_result=validation,
                ),
                int((perf_counter() - start) * 1000),
            )
        except httpx.HTTPStatusError as exc:
            return (
                ToolExecutionResult(status="failed", error=self._http_error_payload(exc)),
                int((perf_counter() - start) * 1000),
            )
        except ReleaseNoteAgentMcpError as exc:
            return (
                ToolExecutionResult(status="failed", error=self._mcp_error_payload(exc.payload)),
                int((perf_counter() - start) * 1000),
            )
        except Exception as exc:
            return (
                ToolExecutionResult(
                    status="failed",
                    error={
                        "code": "RELEASE_NOTE_AGENT_MCP_FAILED",
                        "message": str(exc),
                        "retryable": True,
                    },
                ),
                int((perf_counter() - start) * 1000),
            )

    async def _execute_rest(
        self,
        payload: dict,
        github_url: str,
        start: float,
    ) -> tuple[ToolExecutionResult, int]:
        try:
            async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
                created = await client.post(urljoin(self._base_url(), "api/v1/scans"), json=payload)
                created.raise_for_status()
                created_json = created.json()
                job_id = self._extract_job_id(created_json)
                scan_json = created_json
                if job_id:
                    scan_json = await self._poll_rest_scan(client, job_id)
                artifacts = await self._fetch_rest_artifacts(client, job_id) if job_id else []
            output = {
                "transport": "rest",
                "job_id": job_id,
                "request": payload,
                "scan": scan_json,
                "artifacts": artifacts,
            }
            validation = {
                "valid": True,
                "message": "release-note-agent scan accepted"
                if not job_id
                else f"release-note-agent scan status {self._status(scan_json)}",
            }
            return (
                ToolExecutionResult(
                    status="success",
                    output=output,
                    evidence_refs=[github_url],
                    validation_result=validation,
                ),
                int((perf_counter() - start) * 1000),
            )
        except httpx.HTTPStatusError as exc:
            return (
                ToolExecutionResult(status="failed", error=self._http_error_payload(exc)),
                int((perf_counter() - start) * 1000),
            )
        except Exception as exc:
            return (
                ToolExecutionResult(
                    status="failed",
                    error={
                        "code": "RELEASE_NOTE_AGENT_FAILED",
                        "message": str(exc),
                        "retryable": True,
                    },
                ),
                int((perf_counter() - start) * 1000),
            )

    def _mcp_tool_url(self, tool_name: str) -> str:
        base = self._mcp_base_url()
        if base.rstrip("/").endswith("/mcp"):
            return urljoin(base, f"tools/{tool_name}")
        return urljoin(base, f"mcp/tools/{tool_name}")

    async def _mcp_tool_call(
        self,
        client: httpx.AsyncClient,
        tool_name: str,
        arguments: dict,
    ) -> dict:
        response = await client.post(self._mcp_tool_url(tool_name), json=arguments)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            raise ReleaseNoteAgentMcpError(error)
        result = payload.get("result") if isinstance(payload, dict) else payload
        return result if isinstance(result, dict) else {"result": result}

    @staticmethod
    def _http_error_payload(exc: httpx.HTTPStatusError) -> dict:
        response = exc.response
        code = "RELEASE_NOTE_AGENT_FAILED"
        message = str(exc)
        retryable = response.status_code >= 500
        detail = None
        try:
            body = response.json()
            detail = body.get("detail") if isinstance(body, dict) else body
        except ValueError:
            detail = response.text
        if isinstance(detail, dict):
            code = str(detail.get("error_code") or detail.get("code") or code)
            message = str(detail.get("message") or message)
            retryable = bool(detail.get("retryable", retryable))
        elif detail:
            message = str(detail)
        return {
            "code": code,
            "message": message,
            "retryable": retryable,
            "status_code": response.status_code,
        }

    @staticmethod
    def _mcp_error_payload(payload: dict) -> dict:
        return {
            "code": str(payload.get("error_code") or payload.get("code") or "MCP_TOOL_ERROR"),
            "message": str(payload.get("message") or payload.get("error") or "MCP tool call failed"),
            "retryable": bool(payload.get("retryable", False)),
        }

    async def _poll_rest_scan(self, client: httpx.AsyncClient, job_id: str) -> dict:
        scan_url = urljoin(self._base_url(), f"api/v1/scans/{job_id}")
        latest: dict = {"job_id": job_id, "status": "submitted"}
        for _ in range(12):
            response = await client.get(scan_url)
            response.raise_for_status()
            latest = response.json()
            status = self._status(latest)
            if status in TERMINAL_STATUSES:
                return latest
            await asyncio.sleep(2)
        return latest | {"polling_status": "timeout"}

    async def _poll_mcp_scan(self, client: httpx.AsyncClient, job_id: str) -> dict:
        latest: dict = {"job_id": job_id, "status": "submitted"}
        for _ in range(12):
            latest = await self._mcp_tool_call(
                client,
                "github_release_scan_status",
                {"job_id": job_id},
            )
            status = self._status(latest)
            if status in TERMINAL_STATUSES:
                return latest
            await asyncio.sleep(2)
        return latest | {"polling_status": "timeout"}

    async def _fetch_rest_artifacts(self, client: httpx.AsyncClient, job_id: str) -> list[dict]:
        response = await client.get(urljoin(self._base_url(), f"api/v1/scans/{job_id}/artifacts"))
        response.raise_for_status()
        return await self._hydrate_artifacts(client, job_id, self._artifact_items(response.json()))

    async def _hydrate_artifacts(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        artifacts: list[dict],
    ) -> list[dict]:
        hydrated: list[dict] = []
        for artifact in artifacts:
            artifact_id = artifact.get("artifact_id") or artifact.get("id") or artifact.get("name")
            enriched = dict(artifact)
            if not artifact_id:
                hydrated.append(enriched)
                continue
            if self.settings.release_note_agent_url:
                await self._hydrate_rest_artifact_metadata(client, job_id, artifact_id, enriched)
                await self._hydrate_rest_artifact_content(client, job_id, artifact_id, enriched)
            hydrated.append(enriched)
        return hydrated

    async def _hydrate_rest_artifact_metadata(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        artifact_id: str,
        enriched: dict,
    ) -> None:
        try:
            detail = await client.get(
                urljoin(self._base_url(), f"api/v1/scans/{job_id}/artifacts/{artifact_id}")
            )
            detail.raise_for_status()
            detail_payload = detail.json()
            detail_artifact = self._single_artifact(detail_payload) or detail_payload
            if isinstance(detail_artifact, dict):
                enriched.update(detail_artifact)
        except Exception as exc:
            enriched["metadata_error"] = str(exc)[:500]

    async def _hydrate_rest_artifact_content(
        self,
        client: httpx.AsyncClient,
        job_id: str,
        artifact_id: str,
        enriched: dict,
    ) -> None:
        if not self._should_hydrate_artifact(enriched):
            return
        try:
            download = await client.get(
                urljoin(
                    self._base_url(),
                    f"api/v1/scans/{job_id}/artifacts/{artifact_id}/download",
                )
            )
            download.raise_for_status()
            content = download.text
            enriched["content"] = content[:MAX_HYDRATED_ARTIFACT_CHARS]
            enriched["content_truncated"] = len(content) > MAX_HYDRATED_ARTIFACT_CHARS
        except Exception as exc:
            enriched["download_error"] = str(exc)[:500]


    async def fetch_artifact_bytes(self, job_id: str, artifact_id: str) -> tuple[bytes, str]:
        if not self.settings.release_note_agent_url:
            raise RuntimeError("RELEASE_NOTE_AGENT_URL is required for artifact downloads")
        async with httpx.AsyncClient(timeout=self._request_timeout()) as client:
            response = await client.get(
                urljoin(self._base_url(), f"api/v1/scans/{job_id}/artifacts/{artifact_id}/download")
            )
            response.raise_for_status()
            return response.content, response.headers.get("content-type", "application/octet-stream")
    @staticmethod
    def _single_artifact(payload: object) -> dict | None:
        if isinstance(payload, dict) and payload.get("artifact_id"):
            return payload
        artifacts = ReleaseNoteAgentTool._artifact_items(payload)
        if len(artifacts) == 1:
            return artifacts[0]
        return None

    @staticmethod
    def _should_hydrate_artifact(artifact: dict) -> bool:
        artifact_type = str(artifact.get("artifact_type") or artifact.get("type") or "").lower()
        content_type = str(artifact.get("content_type") or artifact.get("mime_type") or "").lower()
        relative_path = str(artifact.get("relative_path") or artifact.get("path") or "").lower()
        if artifact_type in TEXT_ARTIFACT_TYPES:
            return True
        if content_type.startswith("text/") or content_type.startswith("application/json"):
            return True
        return relative_path.endswith((".md", ".json", ".txt"))

    @staticmethod
    def _extract_job_id(payload: dict) -> str | None:
        for key in ("job_id", "id", "scan_id"):
            value = payload.get(key)
            if value:
                return str(value)
        nested = payload.get("job") or payload.get("scan")
        if isinstance(nested, dict):
            return ReleaseNoteAgentTool._extract_job_id(nested)
        return None

    @staticmethod
    def _status(payload: dict) -> str:
        for key in ("status", "state", "phase"):
            value = payload.get(key)
            if value:
                return str(value).lower()
        return "unknown"

    @staticmethod
    def _artifact_items(payload: object) -> list[dict]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("artifacts", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return []
