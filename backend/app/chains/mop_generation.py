from __future__ import annotations

import re

from pydantic import ValidationError

from backend.app.chains.release_notes import _normalize_plan_payload, _structured_response, prompt_hash
from backend.app.chains.schemas import (
    AgentPlan,
    IntentClassification,
    RecoveryRecommendation,
    ReportWriterResult,
    VerificationResult,
)

WORKFLOW_TYPE = "mop_generation"


class MopGenerationIntentClassifierChain:
    prompt_version = "mop_generation_intent_classifier_v1"
    system_prompt = (
        "Classify the user's BOS Genesis request. Return JSON only. Supported workflow types are "
        "mop_generation, release_note_creation, health_check_diagnostic, mop_creation, "
        "mop_execution, helm_management, k8s_management, and unknown. Include a concise "
        "reasoning_summary, confidence, and clarification questions. Do not include hidden "
        "chain-of-thought."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        namespace: str,
        change_intent: str,
        target_environment: str,
        model_profile: str | None = None,
    ) -> IntentClassification:
        payload = {
            "namespace": namespace,
            "change_intent": change_intent,
            "target_environment": target_environment,
        }
        fallback = IntentClassification(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            workflow_type=WORKFLOW_TYPE,
            confidence=0.94 if namespace and change_intent else 0.5,
            needs_clarification=not bool(namespace and change_intent),
            clarification_questions=[]
            if namespace and change_intent
            else ["Which namespace and change intent should the MoP cover?"],
            target_url=f"k8s://{target_environment}/{namespace}" if namespace else None,
            input_summary=f"Generate MoP for namespace {namespace}: {change_intent}"[:240],
            reasoning_summary=(
                "The request asks for a Method of Procedure document for a selected namespace, "
                "which maps to the read-only MoP Generation workflow."
            ),
        )
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=payload,
            fallback=fallback.model_dump(),
            model_profile=model_profile,
        )
        return _validate_model(IntentClassification, fallback, raw)


class MopGenerationPlannerChain:
    prompt_version = "mop_generation_planner_v1"
    system_prompt = (
        "Create a read-only MoP Generation plan. Use only namespace allowlist validation, "
        "k8s-inspector evidence, helm-manager evidence, mop-creation-agent drafting, MoP "
        "validation, and local artifact preparation. Return JSON only with reasoning_summary "
        "and steps."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        namespace: str,
        change_intent: str,
        target_environment: str,
        helm_release: str | None,
        analysis_depth: str,
        model_profile: str | None = None,
    ) -> AgentPlan:
        payload = {
            "namespace": namespace,
            "change_intent": change_intent,
            "target_environment": target_environment,
            "helm_release": helm_release,
            "analysis_depth": analysis_depth,
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
                "Validate namespace scope, collect read-only Kubernetes and Helm evidence, call "
                "the MoP creation agent for an initial draft, then verify rollback, validation, "
                "and risk sections."
            ),
            steps=[
                {
                    "title": "Classify request as MoP Generation",
                    "tool": "workflow.intent_classifier",
                    "risk": "low",
                    "rationale": "Confirm document-generation workflow before tool execution.",
                },
                {
                    "title": "Validate namespace allowlist and policy",
                    "tool": "policy.namespace",
                    "risk": "low",
                    "rationale": "Keep evidence collection inside the configured ODD.",
                },
                {
                    "title": "Collect Kubernetes namespace evidence",
                    "tool": "mop.k8s_inspector",
                    "risk": "low",
                    "rationale": "Read workload, service, ingress, event, and config metadata.",
                },
                {
                    "title": "Collect Helm release evidence",
                    "tool": "mop.helm_manager",
                    "risk": "low",
                    "rationale": "Read release status, chart metadata, and rollback candidates.",
                },
                {
                    "title": "Generate initial MoP draft",
                    "tool": "mop.creation_agent",
                    "risk": "low",
                    "rationale": "Use the MoP creation agent as the initial document source.",
                },
                {
                    "title": "Verify MoP readiness",
                    "tool": "mop.verifier",
                    "risk": "low",
                    "rationale": "Check required sections, assumptions, validation, and rollback.",
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


class MopGenerationReportWriterChain:
    prompt_version = "mop_generation_report_writer_v1"
    system_prompt = (
        "Draft a Method of Procedure in Markdown using only supplied namespace, Helm, "
        "Kubernetes, and mop-creation-agent evidence. Return JSON only with markdown, "
        "source_evidence_summary, limitations, and reasoning_summary. Do not invent "
        "operational facts."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        namespace: str,
        change_intent: str,
        target_environment: str,
        helm_release: str | None,
        plan: dict,
        k8s_result: dict,
        helm_result: dict,
        mop_agent_result: dict,
        model_profile: str | None = None,
    ) -> ReportWriterResult:
        payload = {
            "namespace": namespace,
            "change_intent": change_intent,
            "target_environment": target_environment,
            "helm_release": helm_release,
            "plan": plan,
            "k8s_result": k8s_result,
            "helm_result": helm_result,
            "mop_agent_result": mop_agent_result,
        }
        fallback_markdown = _fallback_markdown(payload)
        fallback = ReportWriterResult(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            reasoning_summary=(
                "The MoP draft was built from selected namespace, change intent, available MCP "
                "evidence, and explicit gaps where evidence is missing."
            ),
            markdown=fallback_markdown,
            source_evidence_summary=_evidence_summary(
                k8s_result, helm_result, mop_agent_result
            ),
            limitations=_limitations(k8s_result, helm_result, mop_agent_result),
        )
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=payload,
            fallback=fallback.model_dump(),
            model_profile=model_profile,
        )
        result = _validate_model(ReportWriterResult, fallback, raw)
        markdown = result.markdown if _has_required_mop_structure(result.markdown) else fallback_markdown
        return result.model_copy(update={"markdown": markdown})


class MopGenerationVerifierChain:
    prompt_version = "mop_generation_verifier_v1"
    system_prompt = (
        "Verify a Method of Procedure Markdown draft. Return JSON only with valid, "
        "confidence, message, missing_sections, evidence_gaps, policy_notes, and checks. "
        "Require document control, change summary, namespace placeholder/environment, source "
        "evidence, preconditions, implementation, validation, rollback, risk, and human "
        "review notes."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        markdown: str,
        namespace: str,
        tool_results: dict,
        model_profile: str | None = None,
    ) -> VerificationResult:
        payload = {"markdown": markdown[:7000], "namespace": namespace, "tool_results": tool_results}
        deterministic = self._fallback({**payload, "markdown": markdown})
        raw = await _structured_response(
            self.llm,
            system=self.system_prompt,
            user_payload=payload,
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
        namespace = str(payload.get("namespace") or "")
        validation = _mop_structure(markdown, namespace)
        return VerificationResult(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            reasoning_summary=(
                "Checked required MoP sections, namespace citation, source evidence, and "
                "rollback coverage."
            ),
            valid=validation["valid"],
            confidence=0.94 if validation["valid"] else 0.72,
            message=(
                "MoP draft has required structure."
                if validation["valid"]
                else "MoP draft needs review before artifact rendering."
            ),
            missing_sections=validation["missing_sections"],
            evidence_gaps=validation["evidence_gaps"],
            policy_notes=[
                "MoP Generation remains read-only; execution requires a separate approved workflow."
            ],
            checks=validation["checks"],
        )


class MopGenerationRecoveryRecommendationChain:
    prompt_version = "mop_generation_recovery_v1"
    system_prompt = (
        "Recommend a bounded recovery action for a MoP Generation run. Return JSON only. "
        "Continue when draft structure is valid, escalate on policy/evidence gaps, and retry "
        "only retryable MCP failures."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(
        self,
        *,
        tool_results: dict,
        verification: dict,
        model_profile: str | None = None,
    ) -> RecoveryRecommendation:
        payload = {"tool_results": tool_results, "verification": verification}
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
        verification = payload.get("verification") or {}
        tool_results = payload.get("tool_results") or {}
        retryable = any(
            bool((result.get("error") or {}).get("retryable"))
            for result in tool_results.values()
            if isinstance(result, dict)
        )
        if verification.get("valid"):
            action = "continue"
            recommendations = ["Continue to Phase F artifact rendering and publish implementation."]
            escalation_required = False
        elif retryable:
            action = "retry"
            recommendations = ["Retry transient MCP evidence collection before artifact rendering."]
            escalation_required = False
        else:
            action = "escalate"
            recommendations = [
                "Review missing MoP evidence, rollback details, or namespace policy before execution use."
            ]
            escalation_required = True
        return RecoveryRecommendation(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(
                prompt_version=self.prompt_version,
                system_prompt=self.system_prompt,
                payload=payload,
            ),
            reasoning_summary="Selected a bounded recovery path from MoP validation and MCP status.",
            action=action,
            retryable=retryable,
            recommendations=recommendations,
            escalation_required=escalation_required,
        )


def _validate_model(model_type, fallback, raw: dict, normalizer=None):
    fallback_payload = fallback.model_dump()
    raw_payload = raw if isinstance(raw, dict) else {}
    payload = normalizer(fallback_payload, raw_payload) if normalizer else fallback_payload | raw_payload
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


def _fallback_markdown(payload: dict) -> str:
    namespace = str(payload.get("namespace") or "unknown")
    environment = str(payload.get("target_environment") or "unknown")
    change_intent = str(payload.get("change_intent") or "Not provided")
    helm_release = str(payload.get("helm_release") or "Not selected")
    evidence_summary = _evidence_summary(
        payload.get("k8s_result") or {},
        payload.get("helm_result") or {},
        payload.get("mop_agent_result") or {},
    )
    limitations = _limitations(
        payload.get("k8s_result") or {},
        payload.get("helm_result") or {},
        payload.get("mop_agent_result") or {},
    )
    limitation_lines = "\n".join(f"- {item}" for item in limitations) or "- No known limitations."
    return f"""# Method of Procedure: {namespace}

## 1. Document Control
- Workflow: MoP Generation
- Namespace: `{namespace}`
- Environment: `{environment}`
- Helm Release: `{helm_release}`
- Status: Draft for human review

## 2. Change Summary
{change_intent}

## 3. Namespace Placeholder and Environment
- Source Namespace: `{namespace}`
- Target Namespace Placeholder: `generic-namespace`
- Environment: `{environment}`

## 4. Scope and Assumptions
- This workflow produces a human-reviewed clone MoP package in Markdown and PDF format.
- Evidence collection and document generation are read-only; Kubernetes or Helm mutations are not performed here.
- Human approval is required before any execution workflow uses this document.

## 5. Source Evidence
{evidence_summary}

## 6. Current State Summary
Evidence was collected from configured MCP adapters when available. Missing adapters are listed
under limitations.

## 7. Preconditions and Readiness Checks
- Confirm namespace `{namespace}` is the intended target.
- Confirm Helm release `{helm_release}` or select the correct release before execution.
- Confirm operator has access to required dashboards and rollback evidence.

## 8. Risk Assessment Matrix
| Risk | Impact | Mitigation |
|---|---|---|
| Missing live evidence | Medium | Review MCP limitations before execution. |
| Namespace mismatch | High | Validate namespace allowlist and owner approval. |
| Rollback ambiguity | High | Confirm Helm revision and rollback command. |

## 9. Implementation Steps
1. Review current namespace and Helm evidence.
2. Confirm implementation window and stakeholders.
3. Execute only through a future approved MoP Execution workflow.

## 10. Validation Steps
1. Validate workloads and services after execution.
2. Confirm ingress and service endpoints respond as expected.
3. Capture post-change evidence for audit.

## 11. Rollback Plan
1. Stop execution if validation fails.
2. Use approved Helm/Kubernetes rollback procedure after human approval.
3. Re-run validation checks and capture evidence.

## 12. Communication Plan
- Notify platform owner before execution.
- Notify stakeholders after validation or rollback.

## 13. Approval and Human Review Notes
- This draft is not an execution approval.
- Human reviewer must confirm risk, rollback, and implementation steps.

## 14. Execution Readiness Decision
Needs review before execution.

## 15. Agent Activity and Safe Reasoning Summaries
- MoP was generated from selected namespace, user intent, and available MCP evidence.
- Limitations:
{limitation_lines}
"""


def _status(result: dict) -> str:
    return str(result.get("status") or "unknown")


def _result_message(result: dict) -> str:
    error = result.get("error") or {}
    return str(error.get("message") or result.get("message") or "No successful evidence returned.")


def _evidence_state(result: dict) -> str:
    status = _status(result)
    if status == "success":
        return "available"
    if status in {"blocked", "approval_required"}:
        return "needs review"
    if status in {"failed", "timeout", "denied"}:
        return "limited"
    return "not confirmed"


def _evidence_line(label: str, result: dict) -> str:
    state = _evidence_state(result)
    if state == "available":
        return f"- {label}: `{state}`"
    message = _result_message(result)
    return f"- {label}: `{state}` - {message}"


def _evidence_summary(k8s_result: dict, helm_result: dict, mop_agent_result: dict) -> str:
    return "\n".join(
        [
            _evidence_line("k8s-inspector", k8s_result),
            _evidence_line("helm-manager", helm_result),
            _evidence_line("mop-creation-agent", mop_agent_result),
        ]
    )


def _limitations(k8s_result: dict, helm_result: dict, mop_agent_result: dict) -> list[str]:
    limitations = []
    for label, result in (
        ("k8s-inspector", k8s_result),
        ("helm-manager", helm_result),
        ("mop-creation-agent", mop_agent_result),
    ):
        if result.get("status") != "success":
            limitations.append(f"{label} evidence {_evidence_state(result)}: {_result_message(result)}")
    return limitations


def _heading_labels(markdown: str) -> list[str]:
    labels = []
    for line in markdown.splitlines():
        match = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", line)
        if match:
            labels.append(match.group(1).strip().lower())
    return labels


def _mop_structure(markdown: str, namespace: str) -> dict:
    headings = _heading_labels(markdown)
    required_keywords = {
        "document control": "Document Control",
        "change summary": "Change Summary",
        "namespace placeholder": "Namespace Placeholder and Environment",
        "source evidence": "Source Evidence",
        "preconditions": "Preconditions and Readiness Checks",
        "risk": "Risk Assessment Matrix",
        "implementation": "Implementation Steps",
        "validation": "Validation Steps",
        "rollback": "Rollback Plan",
        "approval": "Approval and Human Review Notes",
    }
    missing = [
        label
        for keyword, label in required_keywords.items()
        if not any(keyword in heading for heading in headings)
    ]
    has_title = markdown.lstrip().startswith("# ")
    mentions_namespace = bool(namespace) and namespace in markdown
    evidence_gaps = [] if mentions_namespace else ["Draft does not cite the selected namespace."]
    valid = has_title and not missing and not evidence_gaps
    return {
        "valid": valid,
        "missing_sections": missing,
        "evidence_gaps": evidence_gaps,
        "checks": {
            "has_title": has_title,
            "mentions_namespace": mentions_namespace,
            "required_section_count": len(required_keywords),
            "missing_section_count": len(missing),
        },
    }


def _has_required_mop_structure(markdown: str) -> bool:
    structure = _mop_structure(markdown, namespace="")
    return bool(markdown.lstrip().startswith("# ") and not structure["missing_sections"])
