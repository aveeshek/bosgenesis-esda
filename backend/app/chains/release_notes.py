import hashlib
import json
import re
from urllib.parse import urlparse

from pydantic import ValidationError

from backend.app.chains.schemas import (
    AgentPlan,
    IntentClassification,
    RecoveryRecommendation,
    ReportWriterResult,
    VerificationResult,
)
from backend.app.logging.redaction import redact

WORKFLOW_TYPE = "release_note_creation"


def prompt_hash(*, prompt_version: str, system_prompt: str, payload: dict) -> str:
    seed = json.dumps(
        {
            "prompt_version": prompt_version,
            "system_prompt": system_prompt,
            "payload": redact(payload),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class ReleaseNoteIntentClassifierChain:
    prompt_version = "release_note_intent_classifier_v1"
    system_prompt = (
        "Classify the user's BOS Genesis request. Return JSON only. Supported workflow types are "
        "release_note_creation, health_check_diagnostic, mop_creation, mop_execution, helm_management, "
        "k8s_management, and unknown. Include a concise reasoning_summary, confidence, and any "
        "clarifying questions. Do not include hidden chain-of-thought."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        user_text: str,
        github_url: str | None = None,
        release_name: str | None = None,
        model_profile: str | None = None,
    ) -> IntentClassification:
        payload = {
            "user_text": user_text,
            "github_url": github_url,
            "release_name": release_name,
        }
        fallback = self._fallback(payload)
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=payload,
            fallback=fallback.model_dump(),
            model_profile=model_profile,
        )
        return _validate_model(IntentClassification, fallback, raw)

    def _fallback(self, payload: dict) -> IntentClassification:
        user_text = str(payload.get("user_text") or "")
        github_url = payload.get("github_url")
        lowered = user_text.lower()
        has_github_url = bool(github_url) or "github.com/" in lowered
        asks_release_notes = "release note" in lowered or "changelog" in lowered
        workflow_type = WORKFLOW_TYPE if has_github_url or asks_release_notes else "unknown"
        needs_clarification = workflow_type == WORKFLOW_TYPE and not has_github_url
        questions = ["Which GitHub repository URL should I analyze?"] if needs_clarification else []
        confidence = 0.92 if has_github_url else 0.72 if asks_release_notes else 0.2
        return IntentClassification(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            workflow_type=workflow_type,
            confidence=confidence,
            needs_clarification=needs_clarification,
            clarification_questions=questions,
            target_url=github_url or _extract_github_url(user_text),
            input_summary=user_text[:240],
            reasoning_summary=(
                "Classified as release-note creation because the request includes a GitHub source "
                "or asks for release notes."
                if workflow_type == WORKFLOW_TYPE
                else "Could not confidently map the request to a supported release-note workflow."
            ),
        )


class ReleaseNotePlannerChain:
    prompt_version = "release_note_planner_v1"
    system_prompt = (
        "Create a read-only release-note generation plan. Use only allowlisted GitHub source "
        "collection, release-note-agent evidence gathering, evidence-bound drafting, validation, "
        "and Markdown artifact saving. Return JSON only with reasoning_summary and steps."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        github_url: str,
        release_name: str | None,
        branch: str | None,
        tag: str | None,
        commit_sha: str | None,
        model_profile: str | None = None,
    ) -> AgentPlan:
        payload = {
            "github_url": github_url,
            "release_name": release_name,
            "branch": branch,
            "tag": tag,
            "commit_sha": commit_sha,
        }
        fallback = AgentPlan(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            workflow_type=WORKFLOW_TYPE,
            reasoning_summary=(
                "Validate the GitHub request, collect release evidence through release-note-agent, "
                "draft only from evidence, verify required sections, and save a Markdown artifact."
            ),
            steps=[
                {
                    "title": "Classify request as release-note creation",
                    "tool": "workflow.intent_classifier",
                    "risk": "low",
                    "rationale": "Confirm the request maps to the supported release-note workflow.",
                },
                {
                    "title": "Validate GitHub URL and source range",
                    "tool": "policy.github_url",
                    "risk": "low",
                    "rationale": "Keep source collection inside allowlisted GitHub hosts.",
                },
                {
                    "title": "Collect release evidence",
                    "tool": "release_notes.agent_scan",
                    "risk": "low",
                    "rationale": "Gather commits, pull requests, tags, and issue references.",
                },
                {
                    "title": "Draft Markdown release notes",
                    "tool": "release_notes.report_writer",
                    "risk": "low",
                    "rationale": "Generate a human-reviewable artifact from collected evidence.",
                },
                {
                    "title": "Verify draft structure and evidence references",
                    "tool": "release_notes.verifier",
                    "risk": "low",
                    "rationale": "Block empty or unsupported drafts before completion.",
                },
            ],
        )
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=payload,
            fallback=fallback.model_dump(),
            model_profile=model_profile,
        )
        return _validate_model(AgentPlan, fallback, raw, normalizer=_normalize_plan_payload)


class ReleaseNoteVerifierChain:
    prompt_version = "release_note_verifier_v1"
    system_prompt = (
        "Verify a Markdown release-note draft. Return JSON only with valid, confidence, message, "
        "missing_sections, evidence_gaps, policy_notes, and checks. Require a title, "
        "a summary-equivalent section, and source-evidence references. Accept rich release-note-agent "
        "headings such as Executive Summary, Release Overview, Appendix, and evidence tables."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        markdown: str,
        github_url: str,
        agent_result: dict,
        plan: dict,
        model_profile: str | None = None,
    ) -> VerificationResult:
        prompt_payload = {
            "github_url": github_url,
            "markdown": markdown[:6000],
            "agent_result": agent_result,
            "plan": plan,
        }
        deterministic = self._fallback(
            {
                **prompt_payload,
                "markdown": markdown,
            }
        )
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=prompt_payload,
            fallback=deterministic.model_dump(),
            model_profile=model_profile,
        )
        result = _validate_model(VerificationResult, deterministic, raw)
        return result.model_copy(
            update={
                "valid": deterministic.valid,
                "confidence": deterministic.confidence,
                "message": deterministic.message,
                "missing_sections": deterministic.missing_sections,
                "evidence_gaps": deterministic.evidence_gaps,
                "checks": deterministic.checks,
            }
        )

    def _fallback(self, payload: dict) -> VerificationResult:
        markdown = str(payload.get("markdown") or "")
        github_url = str(payload.get("github_url") or "")
        validation = _release_note_structure(markdown, github_url)
        valid = validation["valid"]
        return VerificationResult(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            reasoning_summary=(
                "Checked required Markdown title, summary-equivalent sections, and source evidence references."
            ),
            valid=valid,
            confidence=0.95 if valid else 0.7,
            message=(
                "Release-note draft has required Markdown structure and source evidence."
                if valid
                else "Release-note draft needs review before completion."
            ),
            missing_sections=validation["missing_sections"],
            evidence_gaps=validation["evidence_gaps"],
            policy_notes=["Draft is read-only. Publishing remains approval-gated."],
            checks=validation["checks"],
        )


class ReleaseNoteRecoveryRecommendationChain:
    prompt_version = "release_note_recovery_v1"
    system_prompt = (
        "Recommend a bounded recovery action for a release-note run. Return JSON only. Prefer "
        "retry only for retryable tool failures, ask_clarifying_question for missing inputs, "
        "continue for acceptable validation, and escalate for policy or evidence failures."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        agent_result: dict,
        verification: dict,
        github_url: str,
        model_profile: str | None = None,
    ) -> RecoveryRecommendation:
        payload = {
            "agent_result": agent_result,
            "verification": verification,
            "github_url": github_url,
        }
        deterministic = self._fallback(payload)
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=payload,
            fallback=deterministic.model_dump(),
            model_profile=model_profile,
        )
        result = _validate_model(RecoveryRecommendation, deterministic, raw)
        return result.model_copy(
            update={
                "reasoning_summary": deterministic.reasoning_summary,
                "action": deterministic.action,
                "retryable": deterministic.retryable,
                "recommendations": deterministic.recommendations,
                "escalation_required": deterministic.escalation_required,
            }
        )

    def _fallback(self, payload: dict) -> RecoveryRecommendation:
        agent_result = payload.get("agent_result") or {}
        verification = payload.get("verification") or {}
        error = agent_result.get("error") or {}
        retryable = bool(error.get("retryable"))
        if verification.get("valid"):
            action = "continue"
            recommendations = ["Continue to artifact save and final report."]
            escalation_required = False
        elif retryable:
            action = "retry"
            recommendations = ["Retry release-note-agent source collection once before escalating."]
            escalation_required = False
        else:
            action = "escalate"
            recommendations = ["Ask a human reviewer to inspect missing evidence or policy denial."]
            escalation_required = True
        return RecoveryRecommendation(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            reasoning_summary="Selected a bounded recovery action from tool and verification status.",
            action=action,
            retryable=retryable,
            recommendations=recommendations,
            escalation_required=escalation_required,
        )


class ReleaseNoteReportWriterChain:
    prompt_version = "release_note_report_writer_v1"
    system_prompt = (
        "Draft complete Markdown release notes using only supplied release-note-agent evidence. "
        "When hydrated release-note-agent Markdown content is present, preserve its sections, tables, "
        "headings, graph references, metrics, and operational details; enrich only where evidence supports it. "
        "Return JSON only with markdown, source_evidence_summary, limitations, and reasoning_summary. "
        "Markdown must start with a single # title and include ## Summary and ## Source Evidence. "
        "Do not invent features, fixes, issue IDs, or deployment notes."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        github_url: str,
        release_name: str | None,
        plan: dict,
        agent_result: dict,
        model_profile: str | None = None,
    ) -> ReportWriterResult:
        payload = {
            "github_url": github_url,
            "release_name": release_name,
            "plan": plan,
            "agent_result": agent_result,
        }
        fallback = self._fallback(payload)
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=payload,
            fallback=fallback.model_dump(),
            model_profile=model_profile,
        )
        result = _validate_model(ReportWriterResult, fallback, raw)
        normalized_markdown = _ensure_release_note_markdown(
            result.markdown,
            fallback_markdown=fallback.markdown,
            github_url=github_url,
            release_name=release_name,
            agent_result=agent_result,
            limitations=result.limitations,
            source_evidence_summary=result.source_evidence_summary,
        )
        if normalized_markdown != result.markdown:
            limitations = list(
                dict.fromkeys(
                    [
                        *result.limitations,
                        "Model draft was normalized to required release-note sections.",
                    ]
                )
            )
            result = result.model_copy(
                update={"markdown": normalized_markdown, "limitations": limitations}
            )
        return result

    def _fallback(self, payload: dict) -> ReportWriterResult:
        github_url = str(payload.get("github_url") or "")
        release_name = payload.get("release_name")
        repo_name = _repo_name(github_url)
        agent_result = payload.get("agent_result") or {}
        artifact_records = _agent_artifact_records(agent_result)
        agent_markdown = _extract_agent_markdown(agent_result)
        limitations = []
        if not artifact_records:
            limitations.append("release-note-agent returned no hydrated artifact content yet.")
        if agent_markdown:
            source_summary = (
                f"release-note-agent returned {len(artifact_records)} artifact record(s); "
                "Markdown artifact used as the initial document."
            )
            markdown = _ensure_release_note_markdown(
                agent_markdown,
                fallback_markdown="",
                github_url=github_url,
                release_name=release_name,
                agent_result=agent_result,
                limitations=[],
                source_evidence_summary=source_summary,
            )
            return ReportWriterResult(
                prompt_version=self.prompt_version,
                prompt_hash=prompt_hash(
                    prompt_version=self.prompt_version,
                    system_prompt=self.system_prompt,
                    payload=payload,
                ),
                reasoning_summary=(
                    "Used the hydrated release-note-agent Markdown artifact as the initial document "
                    "for the final evidence-bound draft."
                ),
                markdown=markdown,
                source_evidence_summary=source_summary,
                limitations=[],
            )
        has_evidence = bool(artifact_records)
        feature_line = "- Review collected pull request and commit evidence before publishing."
        fix_line = "- Review bug-fix evidence from release-note-agent before publishing."
        ops_line = "- Confirm deployment, configuration, and migration notes from source evidence."
        known_issue_line = "- None identified by the current draft path."
        if not has_evidence:
            feature_line = "- No feature evidence was confirmed by the current source analysis."
            fix_line = "- No fix evidence was confirmed by the current source analysis."
            ops_line = (
                "- No operational-change evidence was confirmed by the current source analysis."
            )
            known_issue_line = "- Generated as a read-only draft with limited evidence."
        markdown_lines = [
            f"# Release Notes: {release_name or repo_name or 'Draft'}",
            "",
            "## Summary",
            f"Draft generated for `{github_url}`.",
            f"Source analysis status: `{agent_result.get('status', 'unknown')}`.",
            "",
            "## Features",
            feature_line,
            "",
            "## Fixes",
            fix_line,
            "",
            "## Operational Changes",
            ops_line,
            "",
            "## Known Issues",
            known_issue_line,
            "",
            "## Source Evidence",
            f"- GitHub URL: {github_url}",
            f"- release-note-agent status: `{agent_result.get('status', 'unknown')}`",
            f"- release-note-agent artifacts: {len(artifact_records)}",
        ]
        markdown_lines.extend(f"- Limitation: {limitation}" for limitation in limitations)
        markdown = "\n".join(markdown_lines)
        return ReportWriterResult(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            reasoning_summary="Drafted evidence-bound release notes and surfaced evidence limitations.",
            markdown=markdown,
            source_evidence_summary=f"release-note-agent returned {len(artifact_records)} artifact record(s).",
            limitations=limitations,
        )


def _agent_artifact_records(agent_result: dict) -> list[dict]:
    output = agent_result.get("output") or {}
    artifacts = output.get("artifacts") or []
    records: list[dict] = []

    def collect(item: object) -> None:
        if not isinstance(item, dict):
            return
        records.append(item)
        nested = item.get("artifacts")
        if isinstance(nested, list):
            for nested_item in nested:
                collect(nested_item)

    for artifact in artifacts:
        collect(artifact)
    return records


def _extract_agent_markdown(agent_result: dict) -> str | None:
    for artifact in _agent_artifact_records(agent_result):
        content = artifact.get("content") or artifact.get("markdown") or artifact.get("text")
        if not isinstance(content, str):
            continue
        clean = content.strip()
        if not clean:
            continue
        identity = " ".join(
            str(artifact.get(key) or "")
            for key in (
                "artifact_type",
                "content_type",
                "mime_type",
                "name",
                "relative_path",
                "path",
            )
        ).lower()
        if "markdown" in identity or ".md" in identity or clean.startswith("# "):
            return clean
    return None


def _validate_model(model_type, fallback, raw: dict, normalizer=None):
    fallback_payload = fallback.model_dump()
    raw_payload = raw if isinstance(raw, dict) else {}
    payload = (
        normalizer(fallback_payload, raw_payload) if normalizer else fallback_payload | raw_payload
    )
    try:
        return model_type.model_validate(payload)
    except ValidationError:
        return fallback.model_copy(
            update={
                "reasoning_summary": (
                    f"{fallback.reasoning_summary} Model response did not match the structured "
                    "schema; deterministic fallback was used."
                )
            }
        )


def _normalize_plan_payload(fallback_payload: dict, raw_payload: dict) -> dict:
    payload = fallback_payload | raw_payload
    raw_steps = raw_payload.get("steps")
    if not isinstance(raw_steps, list):
        return payload

    normalized_steps = []
    for index, step in enumerate(raw_steps, start=1):
        if not isinstance(step, dict):
            continue
        title = step.get("title") or step.get("step") or step.get("name") or f"Step {index}"
        tool = step.get("tool") or step.get("action") or "release_notes.planner"
        rationale = step.get("rationale") or step.get("reason") or step.get("description") or ""
        normalized_steps.append(
            {
                "title": str(title),
                "tool": str(tool),
                "risk": str(step.get("risk") or "low"),
                "status": str(step.get("status") or "planned"),
                "rationale": str(rationale),
            }
        )
    if normalized_steps:
        payload["steps"] = normalized_steps
    return payload


def _markdown_heading_labels(markdown: str) -> list[str]:
    labels: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", line)
        if match:
            labels.append(match.group(1).strip().lower())
    return labels


def _has_summary_equivalent(markdown: str) -> bool:
    headings = _markdown_heading_labels(markdown)
    return any(
        heading == "summary"
        or "executive summary" in heading
        or "release overview" in heading
        or heading == "overview"
        for heading in headings
    )


def _has_source_evidence_equivalent(markdown: str, github_url: str) -> bool:
    headings = _markdown_heading_labels(markdown)
    lowered = markdown.lower()
    has_evidence_heading = any(
        "evidence" in heading or heading == "appendix" for heading in headings
    )
    has_evidence_refs = "`ev_" in lowered or re.search(r"\bev_[a-f0-9]{8,}\b", lowered) is not None
    mentions_source = bool(github_url) and github_url in markdown
    return has_evidence_heading or (mentions_source and has_evidence_refs)


def _release_note_structure(markdown: str, github_url: str) -> dict:
    has_title = markdown.lstrip().startswith("# ")
    has_subsection = bool(_markdown_heading_labels(markdown))
    has_summary = _has_summary_equivalent(markdown)
    has_source_evidence = _has_source_evidence_equivalent(markdown, github_url)
    mentions_source = bool(github_url) and github_url in markdown
    missing_sections = []
    if not has_summary:
        missing_sections.append("## Summary")
    if not has_source_evidence:
        missing_sections.append("## Source Evidence")
    evidence_gaps = [] if mentions_source else ["Draft does not cite the requested GitHub URL."]
    valid = has_title and has_subsection and not missing_sections and not evidence_gaps
    return {
        "valid": valid,
        "missing_sections": missing_sections,
        "evidence_gaps": evidence_gaps,
        "checks": {
            "has_title": has_title,
            "has_subsection": has_subsection,
            "has_summary": has_summary,
            "has_source_evidence": has_source_evidence,
            "mentions_source": mentions_source,
        },
    }


def _has_required_release_note_structure(markdown: str, github_url: str) -> bool:
    return bool(_release_note_structure(markdown, github_url)["valid"])


def _ensure_release_note_markdown(
    markdown: str,
    *,
    fallback_markdown: str,
    github_url: str,
    release_name: str | None,
    agent_result: dict,
    limitations: list[str],
    source_evidence_summary: str,
) -> str:
    clean = (markdown or "").strip()
    if _has_required_release_note_structure(clean, github_url):
        return clean

    status = str(agent_result.get("status") or "unknown")
    artifacts = _agent_artifact_records(agent_result)
    error = agent_result.get("error") or {}
    error_message = str(error.get("message") or "").strip()
    title = release_name or _repo_name(github_url) or "Draft"

    lines = [
        f"# Release Notes: {title}",
        "",
        "## Summary",
        f"Draft generated for `{github_url}`.",
        f"Source analysis status: `{status}`.",
    ]
    if status != "success":
        lines.append("Detailed source evidence is limited; review before publishing.")
    lines.extend(
        [
            "",
            "## Features",
            "- No feature evidence was confirmed by the current source analysis.",
            "",
            "## Fixes",
            "- No fix evidence was confirmed by the current source analysis.",
            "",
            "## Operational Changes",
            "- No operational-change evidence was confirmed by the current source analysis.",
            "",
            "## Known Issues",
            "- Generated as a read-only draft with limited evidence.",
            "",
            "## Source Evidence",
            f"- GitHub URL: {github_url}",
            f"- release-note-agent status: `{status}`",
            f"- release-note-agent artifacts: {len(artifacts)}",
        ]
    )
    if source_evidence_summary:
        lines.append(f"- Evidence summary: {source_evidence_summary}")
    if error_message:
        lines.append(f"- Tool error summary: {error_message}")
    for limitation in limitations:
        lines.append(f"- Limitation: {limitation}")

    fallback_clean = (fallback_markdown or "").strip()
    if clean and clean != fallback_clean:
        lines.extend(["", "## Model Draft Notes", clean])
    return "\n".join(lines)


async def _structured_response(
    llm,
    *,
    system: str,
    user_payload: dict,
    fallback: dict,
    model_profile: str | None = None,
) -> dict:
    if not hasattr(llm, "structured_response"):
        return fallback
    if model_profile is not None:
        raw = await llm.structured_response(
            system=system,
            user_payload=user_payload,
            fallback=fallback,
            model_profile=model_profile,
        )
    else:
        raw = await llm.structured_response(
            system=system,
            user_payload=user_payload,
            fallback=fallback,
        )
    if not isinstance(raw, dict):
        return fallback
    return raw


def _extract_github_url(text: str) -> str | None:
    for token in text.split():
        stripped = token.strip(".,;:()[]{}<>")
        parsed = urlparse(stripped)
        if parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() == "github.com":
            return stripped
    return None


def _repo_name(github_url: str) -> str:
    path = urlparse(github_url).path.strip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return path or "release"
