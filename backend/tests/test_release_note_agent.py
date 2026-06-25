import asyncio
import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest
from backend.app.tools.release_note_agent import ReleaseNoteAgentMcpError, ReleaseNoteAgentTool


def test_release_note_agent_allows_configured_github_host() -> None:
    tool = ReleaseNoteAgentTool(Settings(allowed_github_hosts="github.com,github.example.com"))

    assert tool._allowed_github_url("https://github.com/org/repo")
    assert tool._allowed_github_url("https://github.example.com/org/repo")
    assert not tool._allowed_github_url("https://gitlab.com/org/repo")
    assert not tool._allowed_github_url("file:///tmp/repo")


def test_release_note_agent_payload_defaults() -> None:
    tool = ReleaseNoteAgentTool(Settings())
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="release_notes.agent_scan",
        workflow_type="release_note_creation",
        user_id="usr_1",
        arguments={"github_url": "https://github.com/org/repo", "release_name": "v1"},
    )

    payload = tool._payload(request)

    assert payload["repo_url"] == "https://github.com/org/repo"
    assert payload["release_name"] == "v1"
    assert payload["analysis_depth"] == "fast"
    assert payload["output_formats"] == ["markdown", "json"]


def test_release_note_agent_extracts_nested_job_id() -> None:
    assert ReleaseNoteAgentTool._extract_job_id({"job": {"id": "abc"}}) == "abc"
    assert ReleaseNoteAgentTool._extract_job_id({"scan_id": "scan-1"}) == "scan-1"

def test_release_note_agent_uses_one_selected_ref_by_specificity() -> None:
    tool = ReleaseNoteAgentTool(Settings())
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="release_notes.agent_scan",
        workflow_type="release_note_creation",
        user_id="usr_1",
        arguments={
            "github_url": "https://github.com/org/repo",
            "branch": "main",
            "tag": "v1.0.0",
            "commit_sha": "abc123",
        },
    )

    payload = tool._payload(request)
    normalized = tool.normalize_ref_arguments(request.arguments)

    assert payload["commit_sha"] == "abc123"
    assert payload["tag"] is None
    assert payload["branch"] is None
    assert normalized["selected_ref"] == {"type": "commit_sha", "value": "abc123"}
    assert normalized["omitted_refs"] == {"branch": "main", "tag": "v1.0.0"}


def test_release_note_agent_uses_tag_before_branch() -> None:
    tool = ReleaseNoteAgentTool(Settings())
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="release_notes.agent_scan",
        workflow_type="release_note_creation",
        user_id="usr_1",
        arguments={
            "github_url": "https://github.com/org/repo",
            "branch": "main",
            "tag": "v1.0.0",
        },
    )

    payload = tool._payload(request)

    assert payload["tag"] == "v1.0.0"
    assert payload["branch"] is None
    assert payload["commit_sha"] is None


def test_release_note_agent_parses_structured_http_error() -> None:
    request = httpx.Request("POST", "http://release-note-agent.bosgenesis.local/api/v1/scans")
    response = httpx.Response(
        500,
        request=request,
        json={
            "detail": {
                "error_code": "AMBIGUOUS_REF",
                "message": "Specify only one of branch, tag, or commit_sha.",
                "retryable": False,
            }
        },
    )
    exc = httpx.HTTPStatusError("server error", request=request, response=response)

    error = ReleaseNoteAgentTool._http_error_payload(exc)

    assert error == {
        "code": "AMBIGUOUS_REF",
        "message": "Specify only one of branch, tag, or commit_sha.",
        "retryable": False,
        "status_code": 500,
    }



def test_release_note_agent_selects_single_artifact_from_nested_payload() -> None:
    artifact = ReleaseNoteAgentTool._single_artifact(
        {"job_id": "job_1", "artifacts": [{"artifact_id": "artifact_1", "artifact_type": "markdown"}]}
    )

    assert artifact == {"artifact_id": "artifact_1", "artifact_type": "markdown"}


def test_release_note_agent_hydrates_text_artifacts_only() -> None:
    assert ReleaseNoteAgentTool._should_hydrate_artifact(
        {"artifact_type": "markdown", "content_type": "text/markdown"}
    )
    assert ReleaseNoteAgentTool._should_hydrate_artifact(
        {"artifact_type": "analytics", "content_type": "application/json"}
    )
    assert not ReleaseNoteAgentTool._should_hydrate_artifact(
        {"artifact_type": "pdf", "content_type": "application/pdf", "relative_path": "release-note.pdf"}
    )

def test_release_note_agent_prefers_configured_mcp_transport() -> None:
    tool = ReleaseNoteAgentTool(
        Settings(
            release_note_agent_mcp_url="http://release-note-agent.bosgenesis.local",
            release_note_agent_url="http://release-note-agent.bosgenesis.local",
        )
    )

    assert tool._use_mcp_transport() is True
    assert (
        tool._mcp_tool_url("github_release_scan_start")
        == "http://release-note-agent.bosgenesis.local/mcp/tools/github_release_scan_start"
    )


def test_release_note_agent_supports_mcp_url_with_mcp_suffix() -> None:
    tool = ReleaseNoteAgentTool(
        Settings(release_note_agent_mcp_url="http://release-note-agent.bosgenesis.local/mcp")
    )

    assert (
        tool._mcp_tool_url("github_release_generate_note")
        == "http://release-note-agent.bosgenesis.local/mcp/tools/github_release_generate_note"
    )


def test_release_note_agent_can_force_rest_transport() -> None:
    tool = ReleaseNoteAgentTool(
        Settings(
            release_note_agent_transport="REST",
            release_note_agent_mcp_url="http://release-note-agent.bosgenesis.local",
            release_note_agent_url="http://release-note-agent.bosgenesis.local",
        )
    )

    assert tool._use_mcp_transport() is False


def test_release_note_agent_mcp_tool_call_extracts_compatibility_result() -> None:
    async def run_call() -> dict:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/mcp/tools/github_release_scan_start"
            return httpx.Response(
                200,
                json={"ok": True, "result": {"job_id": "job_1", "status": "completed"}},
            )

        tool = ReleaseNoteAgentTool(
            Settings(release_note_agent_mcp_url="http://release-note-agent.bosgenesis.local")
        )
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await tool._mcp_tool_call(
                client,
                "github_release_scan_start",
                {"repo_url": "https://github.com/org/repo"},
            )

    assert asyncio.run(run_call()) == {"job_id": "job_1", "status": "completed"}


def test_release_note_agent_mcp_tool_call_raises_tool_error() -> None:
    async def run_call() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "ok": False,
                    "error": {
                        "error_code": "MCP_TOOL_ERROR",
                        "message": "scan failed",
                        "retryable": False,
                    },
                },
            )

        tool = ReleaseNoteAgentTool(
            Settings(release_note_agent_mcp_url="http://release-note-agent.bosgenesis.local")
        )
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            await tool._mcp_tool_call(client, "github_release_scan_start", {})

    try:
        asyncio.run(run_call())
    except ReleaseNoteAgentMcpError as exc:
        error = ReleaseNoteAgentTool._mcp_error_payload(exc.payload)
    else:  # pragma: no cover - defensive branch.
        raise AssertionError("Expected release-note MCP tool error")

    assert error == {
        "code": "MCP_TOOL_ERROR",
        "message": "scan failed",
        "retryable": False,
    }


def test_release_note_agent_hydrates_more_than_first_five_artifacts() -> None:
    async def run_call() -> list[dict]:
        tool = ReleaseNoteAgentTool(Settings(release_note_agent_url="http://agent.local"))
        artifacts = [
            {"artifact_id": f"artifact_{index}", "artifact_type": "analytics", "content_type": "application/json"}
            for index in range(5)
        ]
        artifacts.append(
            {"artifact_id": "artifact_pdf", "artifact_type": "pdf", "content_type": "application/pdf"}
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
            return await tool._hydrate_artifacts(client, "job_1", artifacts)

    hydrated = asyncio.run(run_call())

    assert len(hydrated) == 6
    assert hydrated[-1]["artifact_id"] == "artifact_pdf"
    assert hydrated[-1]["artifact_type"] == "pdf"
