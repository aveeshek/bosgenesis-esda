from pathlib import Path
from typing import Literal
from uuid import uuid4

from backend.app.db.database import RunRepository


ArtifactKind = Literal["release_note", "release_note_pdf", "mop", "execution_report"]


class ArtifactService:
    def __init__(self, *, repository: RunRepository, storage_root: str) -> None:
        self.repository = repository
        self.storage_root = Path(storage_root)

    def save_markdown(
        self,
        *,
        run_id: str,
        user_id: str,
        artifact_type: ArtifactKind,
        title: str,
        markdown: str,
        metadata: dict | None = None,
    ) -> dict:
        artifact_id = f"art_{uuid4().hex}"
        relative_path = Path(artifact_type) / run_id / f"{artifact_id}.md"
        absolute_path = self.storage_root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_text(markdown, encoding="utf-8")
        artifact = self.repository.create_artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            user_id=user_id,
            artifact_type=artifact_type,
            title=title,
            mime_type="text/markdown; charset=utf-8",
            storage_path=relative_path.as_posix(),
            metadata=metadata or {},
        )
        return artifact

    def save_bytes(
        self,
        *,
        run_id: str,
        user_id: str,
        artifact_type: ArtifactKind,
        title: str,
        content: bytes,
        filename_suffix: str,
        mime_type: str,
        metadata: dict | None = None,
    ) -> dict:
        artifact_id = f"art_{uuid4().hex}"
        suffix = filename_suffix if filename_suffix.startswith(".") else f".{filename_suffix}"
        relative_path = Path(artifact_type) / run_id / f"{artifact_id}{suffix}"
        absolute_path = self.storage_root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(content)
        return self.repository.create_artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            user_id=user_id,
            artifact_type=artifact_type,
            title=title,
            mime_type=mime_type,
            storage_path=relative_path.as_posix(),
            metadata=metadata or {},
        )

    def read_artifact_bytes(self, storage_path: str) -> bytes:
        root = self.storage_root.resolve()
        path = (root / storage_path).resolve()
        if root not in path.parents and path != root:
            raise ValueError("Artifact path escaped storage root")
        return path.read_bytes()
