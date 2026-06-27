import asyncio

from backend.app.artifacts import ArtifactService
from backend.app.config import Settings
from backend.app.db.database import Database, RunRepository
from backend.app.db.models import User
from backend.app.graphs.event_bus import RunEventBus
from backend.app.graphs.release_notes import ReleaseNoteGraph, ReleaseNoteInput
from backend.app.logging.postgres_logger import PostgresLogger
from backend.app.tools.contracts import ToolExecutionResult
from backend.app.tools.registry import default_tool_registry

RICH_AGENT_MARKDOWN = """# Bosgenesis Mop Creation Agent Release Notes

## Summary
Rich release-note-agent document with detailed repository evidence.

## Architecture Evidence
Agent-only deployment graph details, Mermaid diagrams, and component matrix are preserved here.

## Operational Metrics
| Area | Detail |
| --- | --- |
| Workflow coverage | Release, MOP, and execution planning |

## Source Evidence
- https://github.com/example/repo
"""


class FakeReleaseNoteAgent:
    async def execute(self, request):
        return (
            ToolExecutionResult(
                status="success",
                output={"job_id": "job_1", "artifacts": [{"type": "json", "name": "evidence"}]},
                evidence_refs=["job_1"],
                validation_result={"valid": True, "message": "evidence collected"},
            ),
            5,
        )


class CapturingRunEventBus(RunEventBus):
    def __init__(self) -> None:
        super().__init__()
        self.published: list[dict] = []

    async def publish(self, run_id, event):
        self.published.append(event)
        await super().publish(run_id, event)


class FakePdfReleaseNoteAgent:
    async def execute(self, request):
        return (
            ToolExecutionResult(
                status="success",
                output={
                    "job_id": "job_pdf_1",
                    "artifacts": [
                        {
                            "artifact_id": "artifact_md",
                            "artifact_type": "markdown",
                            "name": "release-note.md",
                            "content": RICH_AGENT_MARKDOWN,
                        },
                        {
                            "artifact_id": "artifact_pdf",
                            "artifact_type": "pdf",
                            "content_type": "application/pdf",
                            "relative_path": "release-note.pdf",
                        },
                    ],
                },
                evidence_refs=["job_pdf_1"],
                validation_result={"valid": True, "message": "evidence collected"},
            ),
            5,
        )

    async def fetch_artifact_bytes(self, job_id, artifact_id):
        assert job_id == "job_pdf_1"
        assert artifact_id == "artifact_pdf"
        return b"%PDF-1.4\nagent pdf\n", "application/pdf"


class FakeFailedReleaseNoteAgent:
    async def execute(self, request):
        return (
            ToolExecutionResult(
                status="failed",
                output={"job_id": "job_2", "artifacts": []},
                error={"message": "release-note-agent returned 500", "retryable": False},
            ),
            5,
        )


class FakeRepoAnalyzer:
    async def analyze(self, **kwargs):
        github_url = kwargs["github_url"]
        return {
            "status": "completed",
            "repository": {"url": github_url, "ref_label": "default branch"},
            "clone": {
                "status": "success",
                "message": "Repository cloned into temporary workspace.",
            },
            "inventory": {"primary_language": "python", "code_file_count": 3, "manifest_count": 1},
            "vulnerability_findings": [],
            "vulnerability_matrix": [
                {
                    "category": "Common vulnerability scan",
                    "severity": "low",
                    "findings": 0,
                    "evidence": "No high-confidence static findings in scanned files.",
                    "recommendation": "Keep dependency and SAST checks in CI before release.",
                }
            ],
            "quality": {
                "status": "completed",
                "tool": "pylint",
                "issue_count": 0,
                "summary": "No quality issues.",
            },
            "quality_matrix": [
                {
                    "area": "Code quality",
                    "tool": "pylint",
                    "result": "completed",
                    "findings": 0,
                    "notes": "No quality issues.",
                }
            ],
            "cleanup": {"status": "removed", "removed": True},
            "llm_review": {
                "overall_risk": "low",
                "executive_summary": "No high-confidence issues were found.",
                "reasoning_summary": "Reviewed static scan output for common vulnerability themes.",
            },
            "limitations": [],
        }

    def format_markdown(self, scan):
        return "\n".join(
            [
                "## Repository Scan",
                "- Repository: https://github.com/example/repo",
                "- Clone status: `success`",
                "",
                "### Vulnerability Matrix",
                "| Category | Severity | Findings | Evidence | Recommendation |",
                "| --- | --- | ---: | --- | --- |",
                "| Common vulnerability scan | low | 0 | No high-confidence static findings | Keep dependency scanning in CI |",
                "",
                "### Code Quality Matrix",
                "| Area | Tool | Result | Findings | Notes |",
                "| --- | --- | --- | ---: | --- |",
                "| Code quality | pylint | completed | 0 | No quality issues. |",
                "",
                "### LLM Security Review Summary",
                "- Overall risk: `low`",
                "- Safe reasoning summary: Reviewed static scan output for common vulnerability themes.",
            ]
        )


class FakeArtifactPublisher:
    is_enabled = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def target_summary(self) -> dict:
        return {
            "enabled": True,
            "repo_url": "https://github.com/aveeshek/bosgenesis-artifacts.git",
            "branch": "main",
        }

    async def publish_release_artifacts(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": "success",
            "repo_url": "https://github.com/aveeshek/bosgenesis-artifacts.git",
            "branch": "main",
            "folder_name": "260627_173012_Test",
            "folder_path": "260627_173012_Test",
            "tree_url": "https://github.com/aveeshek/bosgenesis-artifacts/tree/main/260627_173012_Test",
            "commit_hash": "abc123",
            "files": [
                {
                    "filename": kwargs["markdown"].filename,
                    "artifact_id": kwargs["markdown"].artifact_id,
                },
                {"filename": kwargs["pdf"].filename, "artifact_id": kwargs["pdf"].artifact_id},
            ],
        }


class FakeLlm:
    settings = Settings()

    async def release_note_plan(self, **kwargs):
        return {
            "prompt_version": "release_note_planner_test_v1",
            "prompt_hash": "planhash",
            "reasoning_summary": "Collect evidence, draft, validate, save.",
            "steps": [{"title": "Collect evidence", "tool": "release_notes.agent_scan"}],
        }

    async def release_note_draft(self, **kwargs):
        return {
            "prompt_version": "release_note_draft_test_v1",
            "prompt_hash": "drafthash",
            "reasoning_summary": "Drafted from mocked evidence.",
            "markdown": "\n".join(
                [
                    "# Release Notes: Test",
                    "",
                    "## Summary",
                    "Mocked release-note content.",
                    "",
                    "## Source Evidence",
                    "- https://github.com/example/repo",
                ]
            ),
        }


class FakeMalformedReportLlm:
    settings = Settings()

    async def structured_response(self, *, system, user_payload, fallback):
        if fallback.get("prompt_version") == "release_note_report_writer_v1":
            return {
                "markdown": "## Release Notes for v0.0.1\n\nUnfortunately, details are unavailable.",
                "reasoning_summary": "Returned malformed Markdown for graph regression testing.",
            }
        return fallback


class ExplodingPlanner:
    async def run(self, **kwargs):
        raise RuntimeError("planner exploded")


def create_release_note_graph(
    tmp_path,
    llm,
    release_note_agent,
    run_id,
    event_bus=None,
    repo_analyzer=None,
    artifact_publisher=None,
):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / f'{run_id}.db'}",
        artifact_storage_dir=str(tmp_path / f"{run_id}_artifacts"),
    )
    database = Database(settings)
    database.init()
    repository = RunRepository(database)
    with database.session() as db:
        user_id = db.query(User).first().user_id
    repository.create_run(
        run_id=run_id,
        user_id=user_id,
        goal="Generate release notes",
        target_url="https://github.com/example/repo",
        namespace=None,
        workflow_type="release_note_creation",
    )
    graph = ReleaseNoteGraph(
        repository=repository,
        event_bus=event_bus or RunEventBus(),
        logger=PostgresLogger(database, settings),
        llm=llm,
        release_note_agent=release_note_agent,
        artifact_service=ArtifactService(
            repository=repository, storage_root=settings.artifact_storage_dir
        ),
        tool_registry=default_tool_registry(),
        repo_analyzer=repo_analyzer or FakeRepoAnalyzer(),
        artifact_publisher=artifact_publisher,
    )
    return graph, repository, user_id


def test_release_note_graph_saves_markdown_artifact(tmp_path) -> None:
    graph, repository, user_id = create_release_note_graph(
        tmp_path,
        FakeLlm(),
        FakeReleaseNoteAgent(),
        "run_release_note_1",
    )

    asyncio.run(
        graph.run(
            ReleaseNoteInput(
                run_id="run_release_note_1",
                user_id=user_id,
                github_url="https://github.com/example/repo",
                release_name="Test",
            )
        )
    )

    run = repository.get_run("run_release_note_1")
    artifacts = repository.list_artifacts("run_release_note_1")
    events = repository.list_events("run_release_note_1")

    artifact_types = {artifact["artifact_type"] for artifact in artifacts}
    markdown_artifact = next(
        artifact for artifact in artifacts if artifact["artifact_type"] == "release_note"
    )
    pdf_artifact = next(
        artifact for artifact in artifacts if artifact["artifact_type"] == "release_note_pdf"
    )
    pdf_bytes = graph.artifact_service.read_artifact_bytes(pdf_artifact["storage_path"])

    assert run.status == "completed"
    assert artifact_types == {"release_note", "release_note_pdf"}
    assert "## Repository Scan" in run.final_report
    assert "### Vulnerability Matrix" in run.final_report
    assert "### Code Quality Matrix" in run.final_report
    assert (
        markdown_artifact["metadata"]["classification"]["workflow_type"] == "release_note_creation"
    )
    assert markdown_artifact["metadata"]["validation"]["valid"] is True
    assert markdown_artifact["metadata"]["repository_scan"]["status"] == "completed"
    assert pdf_artifact["metadata"]["generated_from_markdown"] is True
    assert pdf_artifact["metadata"]["source_agent_pdf_preserved"] is False
    assert b"REPOSITORY SCAN" in pdf_bytes
    assert b"VULNERABILITY MATRIX" in pdf_bytes
    assert b"CODE QUALITY MATRIX" in pdf_bytes
    assert any(event["event_type"] == "workflow_classified" for event in events)
    assert any(event["event_type"] == "repo_clone_completed" for event in events)
    assert any(event["event_type"] == "vulnerability_scan_completed" for event in events)
    assert any(event["event_type"] == "quality_scan_completed" for event in events)
    assert any(event["event_type"] == "repo_cleanup_completed" for event in events)
    assert any(event["event_type"] == "recovery_recommendation" for event in events)
    assert any(event["event_type"] == "artifact_created" for event in events)


def test_release_note_graph_publishes_artifacts_after_success(tmp_path) -> None:
    publisher = FakeArtifactPublisher()
    graph, repository, user_id = create_release_note_graph(
        tmp_path,
        FakeLlm(),
        FakeReleaseNoteAgent(),
        "run_release_note_publish",
        artifact_publisher=publisher,
    )

    asyncio.run(
        graph.run(
            ReleaseNoteInput(
                run_id="run_release_note_publish",
                user_id=user_id,
                github_url="https://github.com/example/repo",
                release_name="Test",
            )
        )
    )

    run = repository.get_run("run_release_note_publish")
    events = repository.list_events("run_release_note_publish")
    publish_event = next(
        event for event in events if event["event_type"] == "artifact_publish_completed"
    )
    final_event = next(event for event in events if event["event_type"] == "run_completed")

    assert run.status == "completed"
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["job_name"] == "Test"
    assert publisher.calls[0]["markdown"].filename == "release-notes.md"
    assert publisher.calls[0]["pdf"].filename == "release-notes.pdf"
    assert publish_event["payload"]["artifact_publish"]["folder_name"] == "260627_173012_Test"
    assert final_event["payload"]["artifact_publish"]["commit_hash"] == "abc123"


def test_release_note_graph_streams_ephemeral_working_notes_without_persisting(tmp_path) -> None:
    event_bus = CapturingRunEventBus()
    graph, repository, user_id = create_release_note_graph(
        tmp_path,
        FakeLlm(),
        FakeReleaseNoteAgent(),
        "run_release_note_ephemeral",
        event_bus=event_bus,
    )

    asyncio.run(
        graph.run(
            ReleaseNoteInput(
                run_id="run_release_note_ephemeral",
                user_id=user_id,
                github_url="https://github.com/example/repo",
                release_name="Test",
            )
        )
    )

    persisted_events = repository.list_events("run_release_note_ephemeral")

    assert any(event["event_type"] == "ephemeral_working_note" for event in event_bus.published)
    assert not any(event["event_type"] == "ephemeral_working_note" for event in persisted_events)
    assert any(event["event_type"] == "reasoning_summary" for event in persisted_events)


def test_release_note_graph_completes_with_normalized_draft_when_agent_fails(tmp_path) -> None:
    graph, repository, user_id = create_release_note_graph(
        tmp_path,
        FakeMalformedReportLlm(),
        FakeFailedReleaseNoteAgent(),
        "run_release_note_failed_agent",
    )

    asyncio.run(
        graph.run(
            ReleaseNoteInput(
                run_id="run_release_note_failed_agent",
                user_id=user_id,
                github_url="https://github.com/example/repo",
                release_name="v0.0.1",
            )
        )
    )

    run = repository.get_run("run_release_note_failed_agent")
    artifacts = repository.list_artifacts("run_release_note_failed_agent")

    assert run.status == "completed"
    assert "# Release Notes: v0.0.1" in run.final_report
    assert "## Summary" in run.final_report
    assert "## Source Evidence" in run.final_report
    assert "## Model Draft Notes" in run.final_report
    assert artifacts[0]["metadata"]["agent_status"] == "failed"
    assert artifacts[0]["metadata"]["validation"]["valid"] is True


def test_release_note_graph_marks_run_failed_on_unhandled_node_error(tmp_path) -> None:
    graph, repository, user_id = create_release_note_graph(
        tmp_path,
        FakeLlm(),
        FakeReleaseNoteAgent(),
        "run_release_note_exploding_planner",
    )
    graph.planner = ExplodingPlanner()

    asyncio.run(
        graph.run(
            ReleaseNoteInput(
                run_id="run_release_note_exploding_planner",
                user_id=user_id,
                github_url="https://github.com/example/repo",
                release_name="v0.0.1",
            )
        )
    )

    run = repository.get_run("run_release_note_exploding_planner")
    events = repository.list_events("run_release_note_exploding_planner")

    assert run.status == "failed"
    assert "planner exploded" in run.final_report
    assert any(event["event_type"] == "run_failed" for event in events)


def test_release_note_graph_saves_agent_pdf_artifact(tmp_path) -> None:
    graph, repository, user_id = create_release_note_graph(
        tmp_path,
        FakeLlm(),
        FakePdfReleaseNoteAgent(),
        "run_release_note_pdf",
    )

    asyncio.run(
        graph.run(
            ReleaseNoteInput(
                run_id="run_release_note_pdf",
                user_id=user_id,
                github_url="https://github.com/example/repo",
                release_name="Test PDF",
            )
        )
    )

    run = repository.get_run("run_release_note_pdf")
    artifacts = repository.list_artifacts("run_release_note_pdf")
    artifact_types = {artifact["artifact_type"] for artifact in artifacts}
    pdf_artifact = next(
        artifact for artifact in artifacts if artifact["artifact_type"] == "release_note_pdf"
    )

    assert run.status == "completed"
    assert artifact_types == {"release_note", "release_note_pdf"}
    assert "## Architecture Evidence" in run.final_report
    assert "Agent-only deployment graph details" in run.final_report
    assert "## Repository Scan" in run.final_report
    assert "### Vulnerability Matrix" in run.final_report
    assert pdf_artifact["mime_type"] == "application/pdf"
    assert pdf_artifact["metadata"]["source_agent_artifact_id"] == "artifact_pdf"
    assert pdf_artifact["metadata"]["source_agent_pdf_preserved"] is True
    assert pdf_artifact["metadata"]["generated_from_markdown"] is False
    assert pdf_artifact["metadata"]["repository_scan"]["status"] == "completed"
    assert pdf_artifact["metadata"]["repository_scan_in_markdown_artifact"] is True
    pdf_bytes = graph.artifact_service.read_artifact_bytes(pdf_artifact["storage_path"])

    assert pdf_bytes == b"%PDF-1.4\nagent pdf\n"
    assert pdf_artifact["metadata"]["paired_markdown_artifact_id"].startswith("art_")
