import asyncio
import re
import shutil
import subprocess

import pytest

from backend.app.artifact_publisher import ArtifactGitPublisher, ArtifactPublishPayload
from backend.app.config import Settings


def test_artifact_git_publisher_commits_markdown_and_pdf_to_timestamped_folder(tmp_path) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("git executable is required for artifact publisher test")

    remote = tmp_path / "remote.git"
    subprocess.run([git, "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    settings = Settings(
        artifact_git_publish_enabled=True,
        artifact_git_repo_url=str(remote),
        artifact_git_branch="main",
        artifact_git_workspace_dir=str(tmp_path / "publisher-work"),
        artifact_git_user_name="ESDA Test",
        artifact_git_user_email="esda-test@example.com",
    )
    publisher = ArtifactGitPublisher(settings)

    result = asyncio.run(
        publisher.publish_release_artifacts(
            run_id="run_publish_1",
            github_url="https://github.com/example/repo",
            job_name="v1.0.0 Release",
            markdown=ArtifactPublishPayload(
                filename="release-notes.md",
                content=b"# Release\n",
                artifact_id="art_md",
                mime_type="text/markdown",
            ),
            pdf=ArtifactPublishPayload(
                filename="release-notes.pdf",
                content=b"%PDF-1.4\n",
                artifact_id="art_pdf",
                mime_type="application/pdf",
            ),
        )
    )

    assert result["status"] == "success"
    assert result["branch"] == "main"
    assert re.match(r"^\d{6}_\d{6}_v1.0.0-Release$", result["folder_name"])
    assert {item["filename"] for item in result["files"]} == {
        "release-notes.md",
        "release-notes.pdf",
    }

    md_blob = subprocess.run(
        [git, "--git-dir", str(remote), "show", f"main:{result['folder_name']}/release-notes.md"],
        check=True,
        capture_output=True,
    ).stdout
    pdf_blob = subprocess.run(
        [git, "--git-dir", str(remote), "show", f"main:{result['folder_name']}/release-notes.pdf"],
        check=True,
        capture_output=True,
    ).stdout
    assert md_blob == b"# Release\n"
    assert pdf_blob == b"%PDF-1.4\n"
