from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

from backend.app.logging.redaction import redact


class ArtifactGitPublishError(RuntimeError):
    """Raised when release-note artifacts cannot be published to the git artifact repo."""


@dataclass(frozen=True)
class ArtifactPublishPayload:
    filename: str
    content: bytes
    artifact_id: str | None = None
    mime_type: str | None = None


class ArtifactGitPublisher:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.repo_url = settings.artifact_git_repo_url.strip()
        self.branch = settings.artifact_git_branch.strip() or "main"
        self.workspace_dir = Path(settings.artifact_git_workspace_dir)
        self.timeout_seconds = settings.artifact_git_command_timeout_seconds

    @property
    def is_enabled(self) -> bool:
        return bool(self.settings.artifact_git_publish_enabled and self.repo_url)

    def target_summary(self) -> dict:
        return {
            "enabled": self.is_enabled,
            "repo_url": _redact_url(self.repo_url),
            "branch": self.branch,
        }

    async def publish_release_artifacts(
        self,
        *,
        run_id: str,
        github_url: str,
        job_name: str,
        markdown: ArtifactPublishPayload,
        pdf: ArtifactPublishPayload,
    ) -> dict:
        if not self.is_enabled:
            return {"status": "disabled", "message": "Artifact git publishing is disabled."}
        return await asyncio.to_thread(
            self._publish_release_artifacts,
            run_id,
            github_url,
            job_name,
            markdown,
            pdf,
        )

    def _publish_release_artifacts(
        self,
        run_id: str,
        github_url: str,
        job_name: str,
        markdown: ArtifactPublishPayload,
        pdf: ArtifactPublishPayload,
    ) -> dict:
        git = shutil.which("git")
        if not git:
            raise ArtifactGitPublishError("git executable was not found in PATH.")

        folder_name = self._artifact_folder_name(job_name)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(
            tempfile.mkdtemp(prefix=f"publish_{_safe_token(run_id)}_", dir=self.workspace_dir)
        )
        repo_dir = temp_dir / "repo"
        try:
            self._git(git, ["clone", "--depth", "1", self.repo_url, str(repo_dir)], cwd=temp_dir)
            self._prepare_branch(git, repo_dir)
            self._git(git, ["config", "core.autocrlf", "false"], cwd=repo_dir)
            self._git(
                git, ["config", "user.name", self.settings.artifact_git_user_name], cwd=repo_dir
            )
            self._git(
                git, ["config", "user.email", self.settings.artifact_git_user_email], cwd=repo_dir
            )

            destination = repo_dir / folder_name
            if destination.exists():
                folder_name = self._unique_folder_name(repo_dir, folder_name)
                destination = repo_dir / folder_name
            destination.mkdir(parents=True, exist_ok=False)
            (destination / markdown.filename).write_bytes(markdown.content)
            (destination / pdf.filename).write_bytes(pdf.content)

            self._git(git, ["add", folder_name], cwd=repo_dir)
            status = self._git(
                git, ["status", "--porcelain", "--", folder_name], cwd=repo_dir
            ).stdout.strip()
            if not status:
                raise ArtifactGitPublishError("No artifact changes were staged for commit.")

            commit_message = f"Add release note artifacts for {job_name} ({run_id})"
            self._git(git, ["commit", "-m", commit_message], cwd=repo_dir)
            commit_hash = self._git(git, ["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
            self._git(git, ["push", "origin", f"HEAD:{self.branch}"], cwd=repo_dir)

            return {
                "status": "success",
                "repo_url": _redact_url(self.repo_url),
                "branch": self.branch,
                "folder_name": folder_name,
                "folder_path": folder_name,
                "tree_url": _github_tree_url(self.repo_url, self.branch, folder_name),
                "commit_hash": commit_hash,
                "files": [
                    {
                        "filename": markdown.filename,
                        "artifact_id": markdown.artifact_id,
                        "mime_type": markdown.mime_type,
                    },
                    {
                        "filename": pdf.filename,
                        "artifact_id": pdf.artifact_id,
                        "mime_type": pdf.mime_type,
                    },
                ],
                "github_url": github_url,
            }
        finally:
            _remove_tree(temp_dir)

    def _prepare_branch(self, git: str, repo_dir: Path) -> None:
        origin_branch = self._git_allow_error(
            git,
            ["rev-parse", "--verify", f"origin/{self.branch}"],
            cwd=repo_dir,
        )
        if origin_branch.returncode == 0:
            self._git(git, ["checkout", "-B", self.branch, f"origin/{self.branch}"], cwd=repo_dir)
            return
        self._git(git, ["checkout", "-B", self.branch], cwd=repo_dir)

    def _git(self, git: str, args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        result = self._git_allow_error(git, args, cwd=cwd)
        if result.returncode != 0:
            command = "git " + " ".join(args[:2])
            message = (result.stderr or result.stdout or "git command failed").strip()
            raise ArtifactGitPublishError(f"{command} failed: {redact(message)[:1000]}")
        return result

    def _git_allow_error(
        self, git: str, args: list[str], *, cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        return subprocess.run(
            [git, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            env=env,
            check=False,
        )

    @staticmethod
    def _artifact_folder_name(job_name: str) -> str:
        timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
        return f"{timestamp}_{_safe_job_name(job_name)}"

    @staticmethod
    def _unique_folder_name(repo_dir: Path, folder_name: str) -> str:
        for index in range(2, 100):
            candidate = f"{folder_name}_{index}"
            if not (repo_dir / candidate).exists():
                return candidate
        raise ArtifactGitPublishError(
            f"Could not create a unique artifact folder for {folder_name}."
        )


def _safe_job_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    clean = clean.strip("._-")
    return (clean or "release-note")[:80]


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value)[:48] or "run"


def _redact_url(value: str) -> str:
    return redact(value)


def _github_tree_url(repo_url: str, branch: str, folder_name: str) -> str | None:
    parsed = urlparse(repo_url)
    if (parsed.hostname or "").lower() != "github.com":
        return None
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path:
        return None
    return f"https://github.com/{path}/tree/{branch}/{folder_name}"


def _remove_tree(path: Path) -> None:
    def onerror(func, value, _exc_info):
        with contextlib.suppress(Exception):
            os.chmod(value, 0o700)
            func(value)

    import contextlib

    with contextlib.suppress(FileNotFoundError):
        shutil.rmtree(path, onerror=onerror)
