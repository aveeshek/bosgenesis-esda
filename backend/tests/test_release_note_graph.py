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



class FakePdfReleaseNoteAgent:
    async def execute(self, request):
        return (
            ToolExecutionResult(
                status="success",
                output={
                    "job_id": "job_pdf_1",
                    "artifacts": [
                        {"artifact_id": "artifact_md", "artifact_type": "markdown", "name": "release-note.md"},
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


def create_release_note_graph(tmp_path, llm, release_note_agent, run_id):
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
        event_bus=RunEventBus(),
        logger=PostgresLogger(database, settings),
        llm=llm,
        release_note_agent=release_note_agent,
        artifact_service=ArtifactService(repository=repository, storage_root=settings.artifact_storage_dir),
        tool_registry=default_tool_registry(),
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

    assert run.status == "completed"
    assert len(artifacts) == 1
    assert artifacts[0]["artifact_type"] == "release_note"
    assert artifacts[0]["metadata"]["classification"]["workflow_type"] == "release_note_creation"
    assert artifacts[0]["metadata"]["validation"]["valid"] is True
    assert any(event["event_type"] == "workflow_classified" for event in events)
    assert any(event["event_type"] == "recovery_recommendation" for event in events)
    assert any(event["event_type"] == "artifact_created" for event in events)


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
    pdf_artifact = next(artifact for artifact in artifacts if artifact["artifact_type"] == "release_note_pdf")

    assert run.status == "completed"
    assert artifact_types == {"release_note", "release_note_pdf"}
    assert pdf_artifact["mime_type"] == "application/pdf"
    assert pdf_artifact["metadata"]["source_agent_artifact_id"] == "artifact_pdf"
    assert pdf_artifact["metadata"]["paired_markdown_artifact_id"].startswith("art_")
