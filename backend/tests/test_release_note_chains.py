import asyncio

from backend.app.chains.release_notes import (
    ReleaseNoteIntentClassifierChain,
    ReleaseNotePlannerChain,
    ReleaseNoteRecoveryRecommendationChain,
    ReleaseNoteReportWriterChain,
    ReleaseNoteVerifierChain,
)


class FakeNoStructuredLlm:
    pass


class FakeMalformedReportLlm:
    async def structured_response(self, *, system, user_payload, fallback):
        if fallback.get("prompt_version") == "release_note_report_writer_v1":
            return {
                "markdown": "## Release Notes for v0.0.1\n\nUnfortunately, no details are available.",
                "reasoning_summary": "Returned a malformed draft for normalization testing.",
            }
        return fallback


class FakeMalformedPlannerLlm:
    async def structured_response(self, *, system, user_payload, fallback):
        if fallback.get("prompt_version") == "release_note_planner_v1":
            return {
                "reasoning_summary": "Plan returned with non-canonical step fields.",
                "steps": [
                    {"step": "Source Collection", "action": "call_release_note_agent"},
                    {"step": "Validation", "action": "validate_markdown_artifact"},
                ],
            }
        return fallback


class FakeInvalidVerifierLlm:
    async def structured_response(self, *, system, user_payload, fallback):
        return {
            "valid": False,
            "message": "Model incorrectly marked the valid draft as invalid.",
            "missing_sections": ["## Summary"],
        }


def run(coro):
    return asyncio.run(coro)


def test_intent_classifier_classifies_release_note_workflow() -> None:
    classification = run(
        ReleaseNoteIntentClassifierChain(FakeNoStructuredLlm()).run(
            user_text="Generate release notes for this repository",
            github_url="https://github.com/example/repo",
            release_name="v1.0.0",
        )
    )

    assert classification.workflow_type == "release_note_creation"
    assert classification.needs_clarification is False
    assert classification.target_url == "https://github.com/example/repo"
    assert classification.prompt_version == "release_note_intent_classifier_v1"
    assert len(classification.prompt_hash) == 64


def test_intent_classifier_requests_github_url_when_missing() -> None:
    classification = run(
        ReleaseNoteIntentClassifierChain(FakeNoStructuredLlm()).run(
            user_text="Please create release notes for my service",
        )
    )

    assert classification.workflow_type == "release_note_creation"
    assert classification.needs_clarification is True
    assert classification.clarification_questions


def test_planner_creates_read_only_release_note_chain_plan() -> None:
    plan = run(
        ReleaseNotePlannerChain(FakeNoStructuredLlm()).run(
            github_url="https://github.com/example/repo",
            release_name="v1.0.0",
            branch="main",
            tag=None,
            commit_sha=None,
        )
    )

    assert plan.workflow_type == "release_note_creation"
    assert plan.prompt_version == "release_note_planner_v1"
    assert len(plan.prompt_hash) == 64
    assert any(step.tool == "release_notes.agent_scan" for step in plan.steps)
    assert any(step.tool == "release_notes.verifier" for step in plan.steps)


def test_planner_normalizes_noncanonical_model_steps() -> None:
    plan = run(
        ReleaseNotePlannerChain(FakeMalformedPlannerLlm()).run(
            github_url="https://github.com/example/repo",
            release_name="v1.0.0",
            branch="main",
            tag=None,
            commit_sha=None,
        )
    )

    assert plan.workflow_type == "release_note_creation"
    assert plan.steps[0].title == "Source Collection"
    assert plan.steps[0].tool == "call_release_note_agent"
    assert plan.steps[1].title == "Validation"
    assert plan.steps[1].tool == "validate_markdown_artifact"


def test_report_writer_generates_evidence_bound_markdown() -> None:
    draft = run(
        ReleaseNoteReportWriterChain(FakeNoStructuredLlm()).run(
            github_url="https://github.com/example/repo",
            release_name="v1.0.0",
            plan={"steps": []},
            agent_result={"status": "success", "output": {"artifacts": [{"name": "evidence"}]}},
        )
    )

    assert draft.prompt_version == "release_note_report_writer_v1"
    assert "# Release Notes: v1.0.0" in draft.markdown
    assert "## Source Evidence" in draft.markdown
    assert "https://github.com/example/repo" in draft.markdown
    assert len(draft.prompt_hash) == 64


def test_verifier_flags_missing_source_evidence() -> None:
    verification = run(
        ReleaseNoteVerifierChain(FakeNoStructuredLlm()).run(
            markdown="# Release Notes\n\n## Summary\nDraft only.",
            github_url="https://github.com/example/repo",
            agent_result={"status": "success"},
            plan={"steps": []},
        )
    )

    assert verification.valid is False
    assert "## Source Evidence" in verification.missing_sections
    assert verification.evidence_gaps
    assert verification.prompt_version == "release_note_verifier_v1"


def test_recovery_recommends_continue_for_valid_draft() -> None:
    recovery = run(
        ReleaseNoteRecoveryRecommendationChain(FakeNoStructuredLlm()).run(
            agent_result={"status": "success"},
            verification={"valid": True},
            github_url="https://github.com/example/repo",
        )
    )

    assert recovery.action == "continue"
    assert recovery.escalation_required is False
    assert recovery.prompt_version == "release_note_recovery_v1"


def test_recovery_recommends_retry_for_retryable_tool_error() -> None:
    recovery = run(
        ReleaseNoteRecoveryRecommendationChain(FakeNoStructuredLlm()).run(
            agent_result={"status": "error", "error": {"retryable": True}},
            verification={"valid": False},
            github_url="https://github.com/example/repo",
        )
    )

    assert recovery.action == "retry"
    assert recovery.retryable is True


def test_report_writer_normalizes_malformed_model_markdown() -> None:
    draft = run(
        ReleaseNoteReportWriterChain(FakeMalformedReportLlm()).run(
            github_url="https://github.com/example/repo",
            release_name="v0.0.1",
            plan={"steps": []},
            agent_result={
                "status": "failed",
                "error": {"message": "release-note-agent returned 500"},
                "output": {"artifacts": []},
            },
        )
    )

    assert draft.markdown.startswith("# Release Notes: v0.0.1")
    assert "## Summary" in draft.markdown
    assert "## Source Evidence" in draft.markdown
    assert "https://github.com/example/repo" in draft.markdown
    assert "## Model Draft Notes" in draft.markdown
    assert any("Model draft was normalized" in item for item in draft.limitations)


def test_verifier_keeps_deterministic_required_section_result() -> None:
    verification = run(
        ReleaseNoteVerifierChain(FakeInvalidVerifierLlm()).run(
            markdown="\n".join(
                [
                    "# Release Notes: v0.0.1",
                    "",
                    "## Summary",
                    "Draft generated for `https://github.com/example/repo`.",
                    "",
                    "## Source Evidence",
                    "- GitHub URL: https://github.com/example/repo",
                ]
            ),
            github_url="https://github.com/example/repo",
            agent_result={"status": "failed"},
            plan={"steps": []},
        )
    )

    assert verification.valid is True
    assert verification.missing_sections == []
    assert verification.checks["mentions_source"] is True


def test_verifier_accepts_rich_agent_markdown_with_equivalent_sections() -> None:
    markdown = "\n".join(
        [
            "# Bosgenesis Mop Creation Agent Release Notes",
            "",
            "## Document Control",
            "- Repository: https://github.com/example/repo",
            "",
            "## Executive Summary",
            "Rich release-note-agent document generated from repository evidence.",
            "",
            "## Release Overview",
            "Analytics bundle generated for repository review.",
            "",
            "## Technology Inventory",
            "| Evidence | File |",
            "| --- | --- |",
            "| `ev_abc123456789` | README.md |",
            "",
            "## Appendix",
            "Generated by BOS Genesis Release Note Agent.",
        ]
    )

    verification = run(
        ReleaseNoteVerifierChain(FakeNoStructuredLlm()).run(
            markdown=markdown,
            github_url="https://github.com/example/repo",
            agent_result={"status": "success"},
            plan={"steps": []},
        )
    )

    assert verification.valid is True
    assert verification.missing_sections == []
    assert verification.evidence_gaps == []
    assert verification.checks["has_summary"] is True
    assert verification.checks["has_source_evidence"] is True


def test_verifier_uses_full_markdown_for_deterministic_validation() -> None:
    filler = "\n".join(f"Detailed evidence line {index}." for index in range(700))
    markdown = "\n".join(
        [
            "# Release Notes: v1.0.0",
            "",
            "## Executive Summary",
            "Generated for https://github.com/example/repo.",
            filler,
            "",
            "## Source Evidence",
            "- GitHub URL: https://github.com/example/repo",
        ]
    )

    verification = run(
        ReleaseNoteVerifierChain(FakeNoStructuredLlm()).run(
            markdown=markdown,
            github_url="https://github.com/example/repo",
            agent_result={"status": "success"},
            plan={"steps": []},
        )
    )

    assert verification.valid is True
    assert verification.missing_sections == []
    assert verification.checks["has_source_evidence"] is True


def test_report_writer_uses_hydrated_agent_markdown_as_initial_document() -> None:
    agent_markdown = "\n".join(
        [
            "# Release Notes: v1.0.0",
            "",
            "## Summary",
            "Agent generated summary for `https://github.com/example/repo`.",
            "",
            "## Source Evidence",
            "- GitHub URL: https://github.com/example/repo",
        ]
    )

    draft = run(
        ReleaseNoteReportWriterChain(FakeNoStructuredLlm()).run(
            github_url="https://github.com/example/repo",
            release_name="v1.0.0",
            plan={"steps": []},
            agent_result={
                "status": "success",
                "output": {
                    "artifacts": [
                        {
                            "artifact_id": "artifact_md",
                            "artifact_type": "markdown",
                            "content_type": "text/markdown",
                            "content": agent_markdown,
                        }
                    ]
                },
            },
        )
    )

    assert "Agent generated summary" in draft.markdown
    assert "Markdown artifact used as the initial document" in draft.source_evidence_summary
    assert draft.limitations == []
