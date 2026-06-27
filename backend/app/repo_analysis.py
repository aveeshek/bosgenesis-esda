import asyncio
from contextlib import suppress
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from textwrap import shorten

from backend.app.logging.redaction import redact

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
}

CODE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rb",
    ".php",
    ".cs",
    ".sh",
    ".ps1",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".xml",
    ".tf",
    ".dockerfile",
}

LANGUAGE_SUFFIXES = {
    "python": {".py"},
    "javascript": {".js", ".jsx"},
    "typescript": {".ts", ".tsx"},
    "java": {".java"},
    "go": {".go"},
    "shell": {".sh"},
    "powershell": {".ps1"},
    "iac": {".tf", ".yaml", ".yml"},
}

VULNERABILITY_PATTERNS = [
    {
        "id": "python_eval_exec",
        "category": "Injection",
        "severity": "high",
        "regex": r"\b(eval|exec)\s*\(",
        "recommendation": "Avoid dynamic code execution; use explicit parsers or allowlisted dispatch.",
    },
    {
        "id": "shell_true",
        "category": "Command Execution",
        "severity": "high",
        "regex": r"subprocess\.[A-Za-z_]+\([^\n]*shell\s*=\s*True",
        "recommendation": "Use argv lists with shell disabled and validate all command inputs.",
    },
    {
        "id": "os_system",
        "category": "Command Execution",
        "severity": "high",
        "regex": r"\bos\.system\s*\(",
        "recommendation": "Use subprocess with shell disabled and bounded arguments.",
    },
    {
        "id": "unsafe_yaml_load",
        "category": "Unsafe Deserialization",
        "severity": "high",
        "regex": r"yaml\.load\s*\([^\n)]*(Loader\s*=\s*yaml\.SafeLoader|SafeLoader)",
        "negative": True,
        "recommendation": "Use yaml.safe_load or SafeLoader explicitly.",
    },
    {
        "id": "pickle_load",
        "category": "Unsafe Deserialization",
        "severity": "medium",
        "regex": r"\bpickle\.loads?\s*\(",
        "recommendation": "Do not unpickle untrusted input; use JSON or signed data formats.",
    },
    {
        "id": "tls_verify_false",
        "category": "Transport Security",
        "severity": "high",
        "regex": r"verify\s*=\s*False",
        "recommendation": "Keep TLS verification enabled and configure trusted CA bundles where needed.",
    },
    {
        "id": "debug_true",
        "category": "Debug Exposure",
        "severity": "medium",
        "regex": r"debug\s*=\s*True",
        "recommendation": "Ensure debug mode is disabled outside isolated local development.",
    },
    {
        "id": "weak_hash",
        "category": "Cryptography",
        "severity": "medium",
        "regex": r"hashlib\.(md5|sha1)\s*\(",
        "recommendation": "Use SHA-256 or stronger algorithms unless this is non-security hashing.",
    },
    {
        "id": "hardcoded_secret",
        "category": "Secrets",
        "severity": "high",
        "regex": r"(?i)(password|passwd|secret|api[_-]?key|token)\s*=\s*['\"][^'\"]{8,}['\"]",
        "recommendation": "Move secrets to a secret manager or environment variables and rotate exposed values.",
    },
    {
        "id": "js_eval",
        "category": "Injection",
        "severity": "high",
        "regex": r"\beval\s*\(",
        "recommendation": "Avoid eval; use structured parsing or explicit function maps.",
    },
    {
        "id": "node_child_process_exec",
        "category": "Command Execution",
        "severity": "high",
        "regex": r"child_process\.(exec|execSync)\s*\(",
        "recommendation": "Prefer spawn/execFile with fixed argv and strict input validation.",
    },
    {
        "id": "k8s_privileged",
        "category": "Container Security",
        "severity": "high",
        "regex": r"privileged\s*:\s*true",
        "recommendation": "Avoid privileged containers unless approved by an explicit exception.",
    },
]


class RepoAnalysisService:
    def __init__(self, *, llm, clone_timeout_seconds: int = 180, command_timeout_seconds: int = 120) -> None:
        self.llm = llm
        self.clone_timeout_seconds = clone_timeout_seconds
        self.command_timeout_seconds = command_timeout_seconds

    async def analyze(
        self,
        *,
        github_url: str,
        branch: str | None = None,
        tag: str | None = None,
        commit_sha: str | None = None,
        model_profile: str | None = None,
    ) -> dict:
        static_result = await asyncio.to_thread(
            self._collect_static_analysis,
            github_url,
            branch,
            tag,
            commit_sha,
        )
        llm_review = await self._llm_review(static_result, model_profile=model_profile)
        static_result["llm_review"] = llm_review
        static_result["vulnerability_matrix"] = self._vulnerability_matrix(static_result, llm_review)
        return static_result

    def format_markdown(self, scan: dict) -> str:
        repo = scan.get("repository") or {}
        clone = scan.get("clone") or {}
        inventory = scan.get("inventory") or {}
        cleanup = scan.get("cleanup") or {}
        llm_review = scan.get("llm_review") or {}
        quality = scan.get("quality") or {}
        lines = [
            "## Repository Scan",
            f"- Repository: {repo.get('url', 'unknown')}",
            f"- Source ref: {repo.get('ref_label', 'default branch')}",
            f"- Clone status: `{clone.get('status', 'unknown')}`",
            f"- Primary language: `{inventory.get('primary_language', 'unknown')}`",
            f"- Files inspected: {inventory.get('code_file_count', 0)} code file(s), {inventory.get('manifest_count', 0)} manifest(s)",
            f"- Local checkout cleanup: `{cleanup.get('status', 'unknown')}`",
            "",
            "### Vulnerability Matrix",
            "| Category | Severity | Findings | Evidence | Recommendation |",
            "| --- | --- | ---: | --- | --- |",
        ]
        for row in scan.get("vulnerability_matrix") or []:
            lines.append(
                "| {category} | {severity} | {findings} | {evidence} | {recommendation} |".format(
                    category=_cell(row.get("category")),
                    severity=_cell(row.get("severity")),
                    findings=row.get("findings", 0),
                    evidence=_cell(row.get("evidence")),
                    recommendation=_cell(row.get("recommendation")),
                )
            )
        if not scan.get("vulnerability_matrix"):
            lines.append("| Static and LLM review | info | 0 | No findings reported | Continue normal review |")
        lines.extend(
            [
                "",
                "### Code Quality Matrix",
                "| Area | Tool | Result | Findings | Notes |",
                "| --- | --- | --- | ---: | --- |",
            ]
        )
        for row in scan.get("quality_matrix") or []:
            lines.append(
                "| {area} | {tool} | {result} | {findings} | {notes} |".format(
                    area=_cell(row.get("area")),
                    tool=_cell(row.get("tool")),
                    result=_cell(row.get("result")),
                    findings=row.get("findings", 0),
                    notes=_cell(row.get("notes")),
                )
            )
        if not scan.get("quality_matrix"):
            lines.append(
                "| Static quality | {tool} | {status} | {count} | {summary} |".format(
                    tool=_cell(quality.get("tool", "internal")),
                    status=_cell(quality.get("status", "unknown")),
                    count=quality.get("issue_count", 0),
                    summary=_cell(quality.get("summary", "No quality summary available.")),
                )
            )
        lines.extend(
            [
                "",
                "### LLM Security Review Summary",
                f"- Overall risk: `{llm_review.get('overall_risk', 'unknown')}`",
                f"- Summary: {llm_review.get('executive_summary', 'LLM review was not available; static heuristics were used.')}",
                f"- Safe reasoning summary: {llm_review.get('reasoning_summary', 'No persisted reasoning summary available.')}",
            ]
        )
        limitations = scan.get("limitations") or []
        if limitations:
            lines.extend(["", "### Scan Limitations"])
            lines.extend(f"- {item}" for item in limitations)
        return "\n".join(lines).strip()

    def _collect_static_analysis(
        self,
        github_url: str,
        branch: str | None,
        tag: str | None,
        commit_sha: str | None,
    ) -> dict:
        started = time.perf_counter()
        temp_dir = Path(tempfile.mkdtemp(prefix="esda_repo_scan_"))
        repo_dir = temp_dir / "repo"
        result: dict = {
            "status": "started",
            "repository": {
                "url": _redact_url(github_url),
                "ref_label": _ref_label(branch=branch, tag=tag, commit_sha=commit_sha),
                "branch": branch,
                "tag": tag,
                "commit_sha": commit_sha,
            },
            "clone": {"status": "not_started"},
            "inventory": {},
            "vulnerability_findings": [],
            "vulnerability_matrix": [],
            "quality": {"status": "not_started"},
            "quality_matrix": [],
            "cleanup": {"status": "pending", "temp_dir_hash": _hash_text(str(temp_dir))},
            "limitations": [],
        }
        try:
            clone = self._clone_repo(github_url, repo_dir, branch=branch, tag=tag, commit_sha=commit_sha)
            result["clone"] = clone
            if clone["status"] != "success":
                result["status"] = "partial"
                result["limitations"].append("Repository clone failed; vulnerability and quality scan could not inspect source files.")
                result["quality"] = {"status": "skipped", "tool": "none", "issue_count": 0, "summary": "Skipped because clone failed."}
                result["quality_matrix"] = [
                    {
                        "area": "Repository quality",
                        "tool": "none",
                        "result": "skipped",
                        "findings": 0,
                        "notes": "Clone failed before quality checks could run.",
                    }
                ]
                return result
            inventory = self._inventory(repo_dir)
            findings = self._scan_vulnerabilities(repo_dir)
            quality = self._quality_scan(repo_dir, inventory)
            result.update(
                {
                    "status": "completed",
                    "inventory": inventory,
                    "vulnerability_findings": findings,
                    "quality": quality,
                    "quality_matrix": self._quality_matrix(quality, inventory),
                }
            )
            return result
        except Exception as exc:
            result["status"] = "partial"
            result["limitations"].append(f"Repository scan failed before completion: {exc}")
            return result
        finally:
            removed = _remove_tree(temp_dir)
            result["cleanup"] = {
                "status": "removed" if removed else "cleanup_failed",
                "removed": removed,
                "temp_dir_hash": _hash_text(str(temp_dir)),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            }

    def _clone_repo(
        self,
        github_url: str,
        repo_dir: Path,
        *,
        branch: str | None,
        tag: str | None,
        commit_sha: str | None,
    ) -> dict:
        git = shutil.which("git")
        if not git:
            return {"status": "failed", "message": "git executable was not found in PATH."}
        cmd = [git, "clone", "--depth", "1"]
        ref = tag or branch
        if ref:
            cmd.extend(["--branch", ref])
        cmd.extend([github_url, str(repo_dir)])
        clone_result = _run_command(cmd, timeout=self.clone_timeout_seconds)
        if clone_result["returncode"] != 0:
            return {"status": "failed", "message": clone_result["summary"], "command": "git clone"}
        checkout = None
        if commit_sha:
            checkout_result = _run_command(
                [git, "-C", str(repo_dir), "checkout", "--detach", commit_sha],
                timeout=self.command_timeout_seconds,
            )
            checkout = {"status": "success" if checkout_result["returncode"] == 0 else "failed", "message": checkout_result["summary"]}
        return {
            "status": "success",
            "message": "Repository cloned into temporary workspace.",
            "command": "git clone",
            "checkout": checkout,
        }

    def _inventory(self, repo_dir: Path) -> dict:
        files = list(_iter_files(repo_dir))
        suffix_counts: dict[str, int] = {}
        language_counts: dict[str, int] = {key: 0 for key in LANGUAGE_SUFFIXES}
        manifests = []
        total_bytes = 0
        code_file_count = 0
        for path in files:
            rel = _rel(repo_dir, path)
            suffix = path.suffix.lower() or (".dockerfile" if path.name.lower() == "dockerfile" else "")
            suffix_counts[suffix or "<none>"] = suffix_counts.get(suffix or "<none>", 0) + 1
            total_bytes += path.stat().st_size
            if suffix in CODE_SUFFIXES or path.name.lower() == "dockerfile":
                code_file_count += 1
            if path.name.lower() in {"requirements.txt", "pyproject.toml", "package.json", "pom.xml", "build.gradle", "go.mod", "dockerfile"}:
                manifests.append(rel)
            for language, suffixes in LANGUAGE_SUFFIXES.items():
                if suffix in suffixes:
                    language_counts[language] += 1
        primary_language = max(language_counts.items(), key=lambda item: item[1])[0] if any(language_counts.values()) else "unknown"
        dependencies = self._dependency_summary(repo_dir)
        return {
            "file_count": len(files),
            "code_file_count": code_file_count,
            "manifest_count": len(manifests),
            "total_bytes": total_bytes,
            "suffix_counts": suffix_counts,
            "language_counts": language_counts,
            "primary_language": primary_language,
            "manifests": manifests[:40],
            "dependencies": dependencies,
        }

    def _dependency_summary(self, repo_dir: Path) -> dict:
        summary = {"python": [], "javascript": []}
        for req in repo_dir.glob("**/requirements*.txt"):
            if _skip_path(req):
                continue
            for line in _safe_read(req).splitlines():
                clean = line.split("#", 1)[0].strip()
                if clean and not clean.startswith(("-", "--")):
                    summary["python"].append(clean[:120])
        for package in repo_dir.glob("**/package.json"):
            if _skip_path(package):
                continue
            try:
                data = json.loads(_safe_read(package))
            except json.JSONDecodeError:
                continue
            for section in ("dependencies", "devDependencies"):
                deps = data.get(section) or {}
                for name, version in deps.items():
                    summary["javascript"].append(f"{name}@{version}"[:120])
        return {key: values[:80] for key, values in summary.items() if values}

    def _scan_vulnerabilities(self, repo_dir: Path) -> list[dict]:
        findings: list[dict] = []
        for path in _iter_files(repo_dir):
            suffix = path.suffix.lower()
            if suffix not in CODE_SUFFIXES and path.name.lower() not in {"dockerfile", "requirements.txt"}:
                continue
            content = _safe_read(path, limit=750_000)
            if not content:
                continue
            for pattern in VULNERABILITY_PATTERNS:
                regex = pattern["regex"]
                if pattern.get("negative"):
                    # Detect yaml.load without a SafeLoader mention on the same line.
                    for line_no, line in enumerate(content.splitlines(), start=1):
                        if "yaml.load" in line and "SafeLoader" not in line and "safe_load" not in line:
                            findings.append(_finding(repo_dir, path, line_no, line, pattern))
                    continue
                for match in re.finditer(regex, content):
                    line_no = content.count("\n", 0, match.start()) + 1
                    line = content.splitlines()[line_no - 1] if line_no <= len(content.splitlines()) else ""
                    findings.append(_finding(repo_dir, path, line_no, line, pattern))
                    if len(findings) >= 80:
                        return findings
            if path.name.lower().startswith("requirements"):
                for line_no, line in enumerate(content.splitlines(), start=1):
                    clean = line.split("#", 1)[0].strip()
                    if clean and not any(op in clean for op in ("==", "===", "@")) and not clean.startswith(("-", "--")):
                        findings.append(
                            {
                                "id": "unpinned_dependency",
                                "category": "Supply Chain",
                                "severity": "medium",
                                "file": _rel(repo_dir, path),
                                "line": line_no,
                                "evidence": "Dependency is not exactly pinned.",
                                "recommendation": "Pin dependency versions or use a locked dependency file.",
                            }
                        )
        return findings

    def _quality_scan(self, repo_dir: Path, inventory: dict) -> dict:
        if inventory.get("primary_language") == "python":
            return self._python_quality(repo_dir)
        return self._static_quality(repo_dir, tool="internal-static-quality")

    def _python_quality(self, repo_dir: Path) -> dict:
        python_files = [path for path in _iter_files(repo_dir) if path.suffix.lower() == ".py"][:80]
        if not python_files:
            return {"status": "skipped", "tool": "pylint", "issue_count": 0, "summary": "No Python files found."}
        if shutil.which("pylint") or importlib.util.find_spec("pylint"):
            cmd = [
                sys.executable,
                "-m",
                "pylint",
                "--output-format=json",
                "--reports=n",
                "--score=n",
                "--persistent=n",
                *[str(path) for path in python_files],
            ]
            result = _run_command(cmd, timeout=self.command_timeout_seconds)
            issues = _parse_json_list(result.get("stdout", ""))
            return {
                "status": "completed" if result["returncode"] in {0, 2, 4, 8, 16, 32} else "partial",
                "tool": "pylint",
                "issue_count": len(issues),
                "counts": _count_pylint(issues),
                "summary": result["summary"],
                "sample": issues[:20],
            }
        if importlib.util.find_spec("ruff"):
            cmd = [sys.executable, "-m", "ruff", "check", "--output-format=json", str(repo_dir)]
            result = _run_command(cmd, timeout=self.command_timeout_seconds)
            issues = _parse_json_list(result.get("stdout", ""))
            return {
                "status": "completed" if result["returncode"] in {0, 1} else "partial",
                "tool": "ruff",
                "issue_count": len(issues),
                "counts": {"lint": len(issues)},
                "summary": result["summary"],
                "sample": issues[:20],
                "note": "pylint was unavailable; ruff was used as fallback.",
            }
        quality = self._static_quality(repo_dir, tool="internal-python-static-quality")
        quality["note"] = "pylint was unavailable; internal static quality checks were used."
        return quality

    def _static_quality(self, repo_dir: Path, *, tool: str) -> dict:
        issues = []
        for path in _iter_files(repo_dir):
            suffix = path.suffix.lower()
            if suffix not in CODE_SUFFIXES:
                continue
            content = _safe_read(path, limit=500_000)
            lines = content.splitlines()
            for index, line in enumerate(lines, start=1):
                if len(line) > 120:
                    issues.append({"type": "long_line", "file": _rel(repo_dir, path), "line": index})
                if re.search(r"\bTODO\b|\bFIXME\b", line, re.I):
                    issues.append({"type": "todo", "file": _rel(repo_dir, path), "line": index})
                if suffix == ".py" and re.search(r"except\s*:\s*$", line):
                    issues.append({"type": "bare_except", "file": _rel(repo_dir, path), "line": index})
        counts: dict[str, int] = {}
        for issue in issues:
            counts[issue["type"]] = counts.get(issue["type"], 0) + 1
        return {
            "status": "completed",
            "tool": tool,
            "issue_count": len(issues),
            "counts": counts,
            "summary": f"Static quality scan found {len(issues)} issue(s).",
            "sample": issues[:20],
        }

    async def _llm_review(self, static_result: dict, *, model_profile: str | None) -> dict:
        findings = static_result.get("vulnerability_findings") or []
        fallback = {
            "prompt_version": "repo_vulnerability_scan_v1",
            "prompt_hash": _hash_text(json.dumps(redact(static_result), sort_keys=True, default=str)),
            "overall_risk": _overall_risk(findings),
            "executive_summary": _fallback_security_summary(static_result),
            "reasoning_summary": "Reviewed static vulnerability findings, dependency manifests, and quality scan status for common risk themes.",
            "recommendations": _top_recommendations(findings),
        }
        if not hasattr(self.llm, "structured_response"):
            return fallback
        payload = {
            "repository": static_result.get("repository"),
            "inventory": static_result.get("inventory"),
            "vulnerability_findings": findings[:40],
            "quality": static_result.get("quality"),
            "limitations": static_result.get("limitations"),
        }
        try:
            raw = await self.llm.structured_response(
                system=(
                    "You are the BOS Genesis ESDA security reviewer. Review static repository scan output for common "
                    "vulnerability themes. Return JSON only with overall_risk, executive_summary, reasoning_summary, "
                    "and recommendations. Do not include hidden chain-of-thought or source code snippets."
                ),
                user_payload=payload,
                fallback=fallback,
                model_profile=model_profile,
            )
        except Exception as exc:
            fallback["executive_summary"] = (
                f"{fallback['executive_summary']} LLM review was unavailable: {_redact_url(str(exc))}"
            )
            return fallback
        if not isinstance(raw, dict):
            return fallback
        return fallback | {key: raw.get(key, fallback.get(key)) for key in fallback}

    def _vulnerability_matrix(self, static_result: dict, llm_review: dict) -> list[dict]:
        findings = static_result.get("vulnerability_findings") or []
        grouped: dict[tuple[str, str], list[dict]] = {}
        for finding in findings:
            key = (finding.get("category", "Unknown"), finding.get("severity", "info"))
            grouped.setdefault(key, []).append(finding)
        rows = []
        for (category, severity), items in sorted(grouped.items(), key=lambda item: (_severity_rank(item[0][1]), item[0][0])):
            sample = items[0]
            rows.append(
                {
                    "category": category,
                    "severity": severity,
                    "findings": len(items),
                    "evidence": f"{sample.get('file')}:{sample.get('line')} ({sample.get('id')})",
                    "recommendation": sample.get("recommendation", "Review finding and remediate if confirmed."),
                }
            )
        if not rows:
            rows.append(
                {
                    "category": "Common vulnerability scan",
                    "severity": llm_review.get("overall_risk", "low"),
                    "findings": 0,
                    "evidence": "No high-confidence static findings in scanned files.",
                    "recommendation": "Keep dependency and SAST checks in CI before release.",
                }
            )
        return rows

    def _quality_matrix(self, quality: dict, inventory: dict) -> list[dict]:
        counts = quality.get("counts") or {}
        notes = quality.get("note") or quality.get("summary") or "Quality scan completed."
        return [
            {
                "area": "Language mix",
                "tool": "repository inventory",
                "result": inventory.get("primary_language", "unknown"),
                "findings": inventory.get("code_file_count", 0),
                "notes": f"Detected {inventory.get('manifest_count', 0)} dependency/build manifest(s).",
            },
            {
                "area": "Code quality",
                "tool": quality.get("tool", "unknown"),
                "result": quality.get("status", "unknown"),
                "findings": quality.get("issue_count", 0),
                "notes": notes,
            },
            {
                "area": "Quality categories",
                "tool": quality.get("tool", "unknown"),
                "result": "summarized",
                "findings": sum(counts.values()) if counts else quality.get("issue_count", 0),
                "notes": ", ".join(f"{key}: {value}" for key, value in counts.items()) or "No category counts reported.",
            },
        ]


def _remove_tree(path: Path) -> bool:
    def make_writable_and_retry(func, failed_path, _exc_info):
        with suppress(OSError):
            os.chmod(failed_path, stat.S_IREAD | stat.S_IWRITE)
        with suppress(OSError):
            func(failed_path)

    for _attempt in range(3):
        with suppress(FileNotFoundError):
            shutil.rmtree(path, onerror=make_writable_and_retry)
        if not path.exists():
            return True
        time.sleep(0.2)
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()

def _iter_files(root: Path):
    for path in root.rglob("*"):
        if _skip_path(path):
            continue
        if path.is_file():
            yield path


def _skip_path(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def _safe_read(path: Path, *, limit: int = 1_000_000) -> str:
    try:
        data = path.read_bytes()[:limit]
    except OSError:
        return ""
    if b"\x00" in data:
        return ""
    return data.decode("utf-8", errors="replace")


def _run_command(cmd: list[str], *, timeout: int) -> dict:
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        return {
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "summary": _summarize_output(stdout, stderr, completed.returncode),
        }
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "", "summary": f"Command timed out after {timeout}s."}
    except OSError as exc:
        return {"returncode": 127, "stdout": "", "stderr": "", "summary": str(exc)}


def _summarize_output(stdout: str, stderr: str, returncode: int) -> str:
    text = "\n".join(part for part in [stdout.strip(), stderr.strip()] if part)
    if not text:
        return f"Command completed with return code {returncode}."
    return shorten(_redact_url(text.replace("\r", " ").replace("\n", " ")), width=500, placeholder="...")


def _parse_json_list(text: str) -> list[dict]:
    try:
        parsed = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _count_pylint(issues: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for issue in issues:
        key = str(issue.get("type") or issue.get("category") or "issue")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _finding(repo_dir: Path, path: Path, line_no: int, line: str, pattern: dict) -> dict:
    evidence = "possible hardcoded secret assignment" if pattern["id"] == "hardcoded_secret" else line.strip()
    return {
        "id": pattern["id"],
        "category": pattern["category"],
        "severity": pattern["severity"],
        "file": _rel(repo_dir, path),
        "line": line_no,
        "evidence": shorten(evidence, width=140, placeholder="..."),
        "recommendation": pattern["recommendation"],
    }


def _overall_risk(findings: list[dict]) -> str:
    severities = {str(item.get("severity", "")).lower() for item in findings}
    if "high" in severities:
        return "high"
    if "medium" in severities:
        return "medium"
    if "low" in severities:
        return "low"
    return "low"


def _fallback_security_summary(scan: dict) -> str:
    findings = scan.get("vulnerability_findings") or []
    if not findings:
        return "Static and LLM-ready scan context did not identify high-confidence common vulnerability findings."
    return f"Static scan identified {len(findings)} common vulnerability signal(s) for human review."


def _top_recommendations(findings: list[dict]) -> list[str]:
    recs = []
    for finding in findings:
        rec = finding.get("recommendation")
        if rec and rec not in recs:
            recs.append(rec)
        if len(recs) >= 5:
            break
    return recs or ["Keep dependency, SAST, and secret scanning in CI for release gates."]


def _ref_label(*, branch: str | None, tag: str | None, commit_sha: str | None) -> str:
    if commit_sha:
        return f"commit {commit_sha[:12]}"
    if tag:
        return f"tag {tag}"
    if branch:
        return f"branch {branch}"
    return "default branch"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _redact_url(text: str) -> str:
    return re.sub(r"https://([^/@\s]+)@", "https://***@", text)


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _severity_rank(severity: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(str(severity).lower(), 5)


def _cell(value) -> str:
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\n", " ").strip()
