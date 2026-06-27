from pathlib import Path
import textwrap
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

    def save_markdown_pdf(
        self,
        *,
        run_id: str,
        user_id: str,
        artifact_type: ArtifactKind,
        title: str,
        markdown: str,
        metadata: dict | None = None,
    ) -> dict:
        return self.save_bytes(
            run_id=run_id,
            user_id=user_id,
            artifact_type=artifact_type,
            title=title,
            content=_render_text_pdf(markdown=markdown, title=title),
            filename_suffix=".pdf",
            mime_type="application/pdf",
            metadata=metadata or {},
        )

    def read_artifact_bytes(self, storage_path: str) -> bytes:
        root = self.storage_root.resolve()
        path = (root / storage_path).resolve()
        if root not in path.parents and path != root:
            raise ValueError("Artifact path escaped storage root")
        return path.read_bytes()


def _render_text_pdf(*, markdown: str, title: str) -> bytes:
    lines = _plain_text_lines(markdown=markdown, title=title)
    pages = [lines[index:index + 48] for index in range(0, len(lines), 48)] or [[title]]
    objects: list[bytes] = []

    def add(obj: str) -> int:
        objects.append(obj.encode("latin-1", errors="replace"))
        return len(objects)

    catalog_id = add("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add("<< /Type /Pages /Kids [] /Count 0 >>")
    font_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for page_lines in pages:
        stream = _pdf_text_stream(page_lines)
        content_id = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        page_id = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1")

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("latin-1"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "latin-1"
        )
    )
    return bytes(output)


def _plain_text_lines(*, markdown: str, title: str) -> list[str]:
    lines = [title, ""]
    for raw in markdown.splitlines():
        clean = raw.strip()
        if clean.startswith("### "):
            clean = clean[4:].upper()
        elif clean.startswith("## "):
            clean = clean[3:].upper()
        elif clean.startswith("# "):
            clean = clean[2:].upper()
        elif clean.startswith("- "):
            clean = "* " + clean[2:]
        clean = clean.replace("`", "")
        if not clean:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(clean, width=88) or [clean])
    return lines


def _pdf_text_stream(lines: list[str]) -> str:
    parts = ["BT", "/F1 10 Tf", "50 752 Td", "13 TL"]
    for line in lines:
        parts.append(f"({_pdf_escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts)


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
