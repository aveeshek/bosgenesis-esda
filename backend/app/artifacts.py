from pathlib import Path
import textwrap
from typing import Literal
from uuid import uuid4

from backend.app.db.database import RunRepository


ArtifactKind = Literal[
    "release_note",
    "release_note_pdf",
    "mop",
    "mop_pdf",
    "mop_installation",
    "mop_plan",
    "mop_metadata",
    "mop_deployment_zip",
    "mop_bundle_zip",
    "execution_report",
]


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
    pages: list[list[str]] = []
    commands: list[str] = []
    page_number = 0
    y = 0.0

    def begin_page() -> None:
        nonlocal commands, page_number, y
        if commands:
            pages.append(commands)
        page_number += 1
        commands = _pdf_page_chrome(title=title, page_number=page_number)
        y = 684.0

    def ensure(space: float) -> None:
        if y - space < 58:
            begin_page()

    def draw_rule(color: tuple[float, float, float] = (0.64, 0.47, 0.76)) -> None:
        nonlocal y
        ensure(14)
        commands.append(_pdf_line_command(54, y, 558, y, color=color, width=0.8))
        y -= 12

    def write_wrapped(
        value: str,
        *,
        font: str = "F1",
        size: float = 9.6,
        color: tuple[float, float, float] = (0.15, 0.10, 0.26),
        x: float = 54,
        width: float = 504,
        leading: float | None = None,
        uppercase: bool = False,
        mono: bool = False,
    ) -> None:
        nonlocal y
        leading = leading or size + 4
        text = _pdf_normalize_text(value)
        if uppercase:
            text = text.upper()
        if not text:
            y -= 7
            return
        ratio = 0.58 if mono else 0.50
        wrap_width = max(18, int(width / max(size * ratio, 1)))
        wrapped = textwrap.wrap(
            text,
            width=wrap_width,
            break_long_words=mono,
            break_on_hyphens=False,
        ) or [text]
        for line in wrapped:
            ensure(leading + 3)
            commands.append(_pdf_text_command(x=x, y=y, text=line, font=font, size=size, color=color))
            y -= leading

    begin_page()
    source_lines = markdown.splitlines() or [title]
    in_code = False
    for raw in source_lines:
        clean = raw.rstrip()
        stripped = clean.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if not stripped:
            y -= 7
            continue
        if in_code:
            write_wrapped(stripped, font="F3", size=7.4, color=(0.26, 0.22, 0.42), mono=True)
            continue
        if stripped.startswith("# "):
            y -= 4
            write_wrapped(stripped[2:], font="F2", size=18, color=(0.34, 0.19, 0.49), leading=22, uppercase=True)
            draw_rule(color=(0.88, 0.41, 0.46))
            continue
        if stripped.startswith("## "):
            ensure(42)
            y -= 6
            commands.append(_pdf_rect_command(48, y - 4, 516, 24, color=(0.93, 0.84, 0.91)))
            write_wrapped(stripped[3:], font="F2", size=12.5, color=(0.29, 0.21, 0.48), x=58, width=492, leading=17, uppercase=True)
            y -= 2
            continue
        if stripped.startswith("### "):
            ensure(28)
            y -= 3
            write_wrapped(stripped[4:], font="F2", size=10.6, color=(0.40, 0.28, 0.58), leading=15, uppercase=True)
            continue
        if stripped.startswith("| ") or stripped.startswith("|"):
            if set(stripped) <= set("|-: "):
                draw_rule(color=(0.78, 0.67, 0.84))
            else:
                write_wrapped(stripped, font="F3", size=7.3, color=(0.18, 0.16, 0.28), x=60, width=490, leading=10, mono=True)
            continue
        if stripped.startswith("- "):
            write_wrapped("* " + stripped[2:], x=68, width=474, leading=13)
            continue
        numbered = _numbered_list_text(stripped)
        if numbered:
            write_wrapped(numbered, x=68, width=474, leading=13)
            continue
        write_wrapped(stripped, leading=13.5)

    if commands:
        pages.append(commands)
    return _build_pdf(pages)


def _pdf_page_chrome(*, title: str, page_number: int) -> list[str]:
    short_title = _pdf_shorten(_pdf_normalize_text(title), 72)
    commands = [
        _pdf_rect_command(0, 0, 612, 792, color=(0.985, 0.972, 0.992)),
        _pdf_rect_command(0, 728, 242, 64, color=(0.88, 0.35, 0.44)),
        _pdf_rect_command(242, 728, 370, 64, color=(0.32, 0.32, 0.72)),
        _pdf_rect_command(44, 704, 524, 1.2, color=(0.91, 0.76, 0.82)),
        _pdf_text_command(
            x=54,
            y=759,
            text="Ericsson Autonomous SRE and DevOps Agent",
            font="F2",
            size=12,
            color=(1, 1, 1),
        ),
        _pdf_text_command(
            x=54,
            y=741,
            text=short_title,
            font="F1",
            size=9.2,
            color=(0.98, 0.92, 0.97),
        ),
        _pdf_text_command(
            x=500,
            y=34,
            text=f"Page {page_number}",
            font="F1",
            size=8,
            color=(0.45, 0.38, 0.58),
        ),
    ]
    return commands


def _build_pdf(pages: list[list[str]]) -> bytes:
    objects: list[bytes] = []

    def add(obj: str) -> int:
        objects.append(obj.encode("latin-1", errors="replace"))
        return len(objects)

    def add_bytes(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    catalog_id = add("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add("<< /Type /Pages /Kids [] /Count 0 >>")
    font_regular_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_bold_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    font_mono_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    page_ids = []
    for page_commands in pages or [[]]:
        stream = "\n".join(page_commands).encode("latin-1", errors="replace")
        content_id = add_bytes(b"<< /Length " + str(len(stream)).encode("latin-1") + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_regular_id} 0 R /F2 {font_bold_id} 0 R /F3 {font_mono_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
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


def _pdf_text_command(
    *,
    x: float,
    y: float,
    text: str,
    font: str,
    size: float,
    color: tuple[float, float, float],
) -> str:
    r, g, b = color
    return f"BT /{font} {size:.2f} Tf {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} Td ({_pdf_escape(text)}) Tj ET"


def _pdf_rect_command(x: float, y: float, width: float, height: float, *, color: tuple[float, float, float]) -> str:
    r, g, b = color
    return f"q {r:.3f} {g:.3f} {b:.3f} rg {x:.2f} {y:.2f} {width:.2f} {height:.2f} re f Q"


def _pdf_line_command(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: tuple[float, float, float],
    width: float = 1,
) -> str:
    r, g, b = color
    return f"q {r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S Q"


def _numbered_list_text(text: str) -> str | None:
    head, sep, tail = text.partition(" ")
    if sep and head.endswith(".") and head[:-1].isdigit():
        return f"{head} {tail}"
    return None


def _pdf_normalize_text(text: str) -> str:
    return " ".join(str(text).replace("`", "").split())


def _pdf_shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 3)].rstrip() + "..."


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
