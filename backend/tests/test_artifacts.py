from pathlib import Path

from backend.app.artifacts import ArtifactService
from backend.app.config import Settings
from backend.app.db.database import Database, RunRepository


def test_artifact_service_saves_markdown_and_metadata(tmp_path) -> None:
    database = Database(Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'artifact.db'}"))
    database.init()
    repository = RunRepository(database)
    with database.session() as db:
        user = db.query(__import__("backend.app.db.models", fromlist=["User"]).User).first()
        user_id = user.user_id
    repository.create_run(
        run_id="run_artifact_1",
        user_id=user_id,
        goal="Generate release notes",
        target_url="https://github.com/example/repo",
        namespace=None,
        workflow_type="release_note_creation",
    )
    service = ArtifactService(repository=repository, storage_root=str(tmp_path / "artifacts"))

    artifact = service.save_markdown(
        run_id="run_artifact_1",
        user_id=user_id,
        artifact_type="release_note",
        title="Example Release",
        markdown="# Release\n\n## Summary\nDone",
        metadata={"source": "test"},
    )

    assert artifact["artifact_type"] == "release_note"
    assert artifact["metadata"] == {"source": "test"}
    saved = Path(tmp_path / "artifacts" / artifact["storage_path"])
    assert saved.read_text(encoding="utf-8").startswith("# Release")
    assert service.read_artifact_bytes(artifact["storage_path"]).startswith(b"# Release")

def test_artifact_service_saves_pdf_bytes_and_metadata(tmp_path) -> None:
    database = Database(Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'artifact_pdf.db'}"))
    database.init()
    repository = RunRepository(database)
    with database.session() as db:
        user = db.query(__import__("backend.app.db.models", fromlist=["User"]).User).first()
        user_id = user.user_id
    repository.create_run(
        run_id="run_artifact_pdf_1",
        user_id=user_id,
        goal="Generate release notes",
        target_url="https://github.com/example/repo",
        namespace=None,
        workflow_type="release_note_creation",
    )
    service = ArtifactService(repository=repository, storage_root=str(tmp_path / "artifacts"))

    artifact = service.save_bytes(
        run_id="run_artifact_pdf_1",
        user_id=user_id,
        artifact_type="release_note_pdf",
        title="Example Release PDF",
        content=b"%PDF-1.4\n",
        filename_suffix=".pdf",
        mime_type="application/pdf",
        metadata={"source": "agent-pdf"},
    )

    assert artifact["artifact_type"] == "release_note_pdf"
    assert artifact["mime_type"] == "application/pdf"
    assert artifact["storage_path"].endswith(".pdf")
    assert artifact["metadata"] == {"source": "agent-pdf"}
    assert service.read_artifact_bytes(artifact["storage_path"]).startswith(b"%PDF")
