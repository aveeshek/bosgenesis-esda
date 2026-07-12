from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from backend.app.logging.redaction import redact

WORKFLOW_TYPE = "env_agent"
EnvIntentType = Literal["diagnostic", "remediation_request", "follow_up", "unsafe_request"]
Risk = Literal["low", "medium", "high", "critical"]


class PromptedModel(BaseModel):
    prompt_version: str
    prompt_hash: str
    reasoning_summary: str = ""


class EnvAgentIntentClassification(PromptedModel):
    workflow_type: Literal["env_agent"] = WORKFLOW_TYPE
    intent_type: EnvIntentType
    confidence: float = Field(ge=0, le=1)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    target_namespace: str | None = None
    resource_kind: str | None = None
    resource_name: str | None = None
    mode: Literal["diagnostic_only", "propose_only", "approval_gated_remediation"] = "diagnostic_only"
    input_summary: str = ""
    policy_stop: str | None = None


class EnvAgentPlanStep(BaseModel):
    title: str
    adapter: Literal["env.k8s_inspector", "env.helm_manager", "env.data_ingestion", "env.observability", "workflow"]
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    risk: Risk = "low"
    approval_required: bool = False
    status: str = "planned"
    rationale: str = ""


class EnvAgentPlan(PromptedModel):
    workflow_type: Literal["env_agent"] = WORKFLOW_TYPE
    intent_type: EnvIntentType
    steps: list[EnvAgentPlanStep] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)


class EnvAgentDiagnosis(PromptedModel):
    symptoms: list[str] = Field(default_factory=list)
    likely_causes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    severity: Literal["informational", "low", "medium", "high", "critical"] = "informational"
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    summary: str


class EnvAgentRemediationAction(BaseModel):
    action_type: Literal[
        "none",
        "rollout_restart",
        "scale",
        "patch",
        "apply",
        "delete",
        "helm_install",
        "helm_upgrade",
        "helm_uninstall",
        "helm_rollback",
        "ask_clarification",
        "block",
    ]
    adapter: Literal["env.k8s_inspector", "env.helm_manager", "workflow"] = "workflow"
    target_kind: str | None = None
    target_name: str | None = None
    namespace: str | None = None
    arguments: dict = Field(default_factory=dict)
    risk: Risk = "low"
    approval_required: bool = False
    rollback_plan: list[str] = Field(default_factory=list)
    verification_plan: list[str] = Field(default_factory=list)


class EnvAgentRemediationPlan(PromptedModel):
    workflow_type: Literal["env_agent"] = WORKFLOW_TYPE
    allowed_to_execute: bool = False
    decision: Literal["no_action", "propose", "approval_required", "blocked", "clarify"]
    actions: list[EnvAgentRemediationAction] = Field(default_factory=list)
    risk: Risk = "low"
    approval_required: bool = False
    policy_notes: list[str] = Field(default_factory=list)
    operator_message: str = ""


class EnvAgentVerification(PromptedModel):
    valid: bool
    health_status: Literal["healthy", "degraded", "failed", "unknown"]
    confidence: float = Field(ge=0, le=1)
    message: str
    evidence_gaps: list[str] = Field(default_factory=list)
    checks: dict = Field(default_factory=dict)


class EnvAgentRecovery(PromptedModel):
    action: Literal["continue", "retry", "ask_clarifying_question", "decision_required", "escalate", "fail"]
    retryable: bool = False
    recommendations: list[str] = Field(default_factory=list)
    escalation_required: bool = False


class EnvAgentIntentClassifierChain:
    prompt_version = "env_agent_intent_classifier_v1"
    system_prompt = (
        "Classify a BOS Genesis Environment Chat request as diagnostic, remediation_request, follow_up, "
        "or unsafe_request. Return JSON only with safe reasoning_summary, never hidden chain-of-thought. "
        "Secret, token, kubeconfig, delete, wipe, and cluster-wide mutation requests are unsafe."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(self, *, user_text: str, namespace: str | None, mode: str = "diagnostic_only", model_profile: str | None = None) -> EnvAgentIntentClassification:
        payload = {"user_text": user_text, "namespace": namespace, "mode": mode}
        fallback = self._fallback(payload)
        raw = await _structured_response(self.llm, system=self.system_prompt, user_payload=payload, fallback=fallback.model_dump(), model_profile=model_profile)
        return _validate_model(EnvAgentIntentClassification, fallback, raw)

    def _fallback(self, payload: dict) -> EnvAgentIntentClassification:
        text = str(payload.get("user_text") or "").strip()
        lowered = text.lower()
        namespace = str(payload.get("namespace") or "").strip() or None
        mode = _normalize_mode(payload.get("mode"))
        policy_stop = _unsafe_reason(lowered)
        if policy_stop:
            intent_type: EnvIntentType = "unsafe_request"
            confidence = 0.96
        elif _looks_like_remediation(lowered):
            intent_type = "remediation_request"
            confidence = 0.90
            if mode == "diagnostic_only":
                mode = "propose_only"
        elif _looks_like_follow_up(lowered):
            intent_type = "follow_up"
            confidence = 0.72
        else:
            intent_type = "diagnostic"
            confidence = 0.84
        questions = []
        if intent_type == "unsafe_request":
            questions.append("Rephrase as a bounded diagnostic or approved remediation request.")
        resource_kind = _resource_kind(lowered)
        return EnvAgentIntentClassification(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(prompt_version=self.prompt_version, system_prompt=self.system_prompt, payload=payload),
            intent_type=intent_type,
            confidence=confidence,
            needs_clarification=bool(questions),
            clarification_questions=questions,
            target_namespace=namespace,
            resource_kind=resource_kind,
            resource_name=_resource_name(text, resource_kind),
            mode=mode,
            input_summary=text[:240],
            policy_stop=policy_stop,
            reasoning_summary=_intent_summary(intent_type, resource_kind, policy_stop),
        )


class EnvAgentPlannerChain:
    prompt_version = "env_agent_planner_v1"
    system_prompt = (
        "Create a bounded Environment Chat MCP plan. Use read-only k8s-inspector, helm-manager, optional "
        "data-ingestion, and optional observability tools unless remediation is explicitly requested. "
        "High-risk remediation must be proposed only and approval-gated. Return JSON only."
    )

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(self, *, user_text: str, namespace: str | None, classification: dict, model_profile: str | None = None) -> EnvAgentPlan:
        payload = {"user_text": user_text, "namespace": namespace, "classification": classification}
        fallback = self._fallback(payload)
        raw = await _structured_response(self.llm, system=self.system_prompt, user_payload=payload, fallback=fallback.model_dump(), model_profile=model_profile)
        plan = _validate_model(EnvAgentPlan, fallback, raw, normalizer=_normalize_plan_payload)
        return _enforce_direct_log_request(plan, user_text, namespace)

    def _fallback(self, payload: dict) -> EnvAgentPlan:
        lowered = str(payload.get("user_text") or "").lower()
        namespace = str(payload.get("namespace") or "").strip()
        classification = payload.get("classification") or {}
        intent_type = classification.get("intent_type") or "diagnostic"
        stop_conditions = []
        if classification.get("policy_stop"):
            stop_conditions.append(str(classification["policy_stop"]))
        if intent_type == "unsafe_request":
            stop_conditions.append("unsafe_request")
        if stop_conditions:
            steps = [EnvAgentPlanStep(title="Stop for policy or missing scope", adapter="workflow", tool_name="policy.stop", risk="critical", status="blocked", rationale="Environment Chat requires safe, bounded requests.")]
            return self._plan(payload, intent_type, steps, stop_conditions, "Planner stopped before MCP calls because scope or policy guardrails were not satisfied.")
        steps: list[EnvAgentPlanStep] = []

        def add(title: str, adapter: str, tool_name: str, *, risk: Risk = "low", approval_required: bool = False, arguments: dict | None = None, rationale: str = "") -> None:
            steps.append(EnvAgentPlanStep(title=title, adapter=adapter, tool_name=tool_name, risk=risk, approval_required=approval_required, arguments=({"namespace": namespace} if namespace else {}) | (arguments or {}), rationale=rationale))

        add("Collect namespace summary", "env.k8s_inspector", "namespace_summary", rationale="Establish the namespace baseline.")
        target_pod_name = _pod_name_from_text(str(payload.get("user_text") or "")) or classification.get("resource_name")
        if _is_log_request(lowered) and target_pod_name:
            add(
                f"Collect bounded logs for {target_pod_name}",
                "env.k8s_inspector",
                "logs",
                risk="medium",
                arguments={"pod_name": target_pod_name},
                rationale="The operator directly requested pod logs; collect bounded redacted logs for that pod.",
            )
        if _mentions_any(lowered, ("pod", "pods", "crash", "restart", "issue", "problem", "failing", "root cause")):
            add("Inspect pod health", "env.k8s_inspector", "pod_health")
            add("Analyze pod restarts", "env.k8s_inspector", "restart_analysis")
            add("Collect recent namespace events", "env.k8s_inspector", "events")
            if _is_root_cause_question(lowered) and target_pod_name and not _is_log_request(lowered):
                add(
                    f"Collect bounded logs for {target_pod_name}",
                    "env.k8s_inspector",
                    "logs",
                    risk="medium",
                    arguments={"pod_name": target_pod_name},
                    rationale="A pod-specific root-cause question needs bounded logs in addition to namespace-level events.",
                )
        if _mentions_any(lowered, ("deploy", "deployment", "rollout", "replica")):
            add("Inspect deployment readiness", "env.k8s_inspector", "deployment_status")
        if _mentions_any(lowered, ("service", "svc", "ingress", "route", "endpoint", "traffic")):
            add("Inspect service status", "env.k8s_inspector", "service_status")
            add("Inspect ingress status", "env.k8s_inspector", "ingress_status")
        if _mentions_any(lowered, ("pvc", "volume", "storage", "mount")):
            add("Inspect PVC status", "env.k8s_inspector", "pvc_checks")
        if _mentions_any(lowered, ("configmap", "configuration", "config map")):
            add("Inspect ConfigMap metadata", "env.k8s_inspector", "configmap_summary")
        if _mentions_any(lowered, ("helm", "release", "chart", "rollback")) or intent_type == "remediation_request":
            add("List Helm releases", "env.helm_manager", "helm_release_list")
        if len(steps) == 1:
            add("Inspect pod health", "env.k8s_inspector", "pod_health")
            add("Collect recent namespace events", "env.k8s_inspector", "events")
            add("List Helm releases", "env.helm_manager", "helm_release_list")
        if intent_type == "remediation_request":
            add("Draft approval-gated remediation proposal", "workflow", "remediation.proposal", risk="high", approval_required=True, rationale="Execution waits for explicit approval before any typed MCP action runs.")
        return self._plan(payload, intent_type, steps, [], f"Planner selected {len(steps)} bounded step(s) for prompt-first Environment Chat analysis.")

    def _plan(self, payload: dict, intent_type: str, steps: list[EnvAgentPlanStep], stop_conditions: list[str], summary: str) -> EnvAgentPlan:
        return EnvAgentPlan(prompt_version=self.prompt_version, prompt_hash=prompt_hash(prompt_version=self.prompt_version, system_prompt=self.system_prompt, payload=payload), intent_type=intent_type, steps=steps, stop_conditions=stop_conditions, reasoning_summary=summary)


class EnvAgentDiagnosisChain:
    prompt_version = "env_agent_diagnosis_v1"
    system_prompt = "Summarize Environment Chat evidence into symptoms, likely causes, confidence, severity, missing evidence, and summary. Return JSON only."

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(self, *, user_text: str, plan: dict, evidence: list[dict], model_profile: str | None = None) -> EnvAgentDiagnosis:
        payload = {"user_text": user_text, "plan": plan, "evidence": evidence}
        fallback = self._fallback(payload)
        raw = await _structured_response(self.llm, system=self.system_prompt, user_payload=payload, fallback=fallback.model_dump(), model_profile=model_profile)
        diagnosis = _validate_model(EnvAgentDiagnosis, fallback, raw)
        diagnosis = _apply_pod_inventory_to_diagnosis(diagnosis, evidence)
        return _apply_root_cause_context(diagnosis, user_text, evidence)

    def _fallback(self, payload: dict) -> EnvAgentDiagnosis:
        evidence = [item for item in payload.get("evidence") or [] if isinstance(item, dict)]
        failed = [item for item in evidence if item.get("status") in {"failed", "blocked"}]
        symptoms = _symptoms_from_evidence(evidence) or ["Awaiting read-only namespace evidence."]
        missing = [] if evidence else ["No MCP evidence has been collected yet."]
        if failed:
            missing.append("One or more MCP evidence calls failed or were blocked.")
        return EnvAgentDiagnosis(
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(prompt_version=self.prompt_version, system_prompt=self.system_prompt, payload=payload),
            symptoms=symptoms,
            likely_causes=_likely_causes(symptoms, failed),
            confidence=0.78 if evidence and not failed else 0.42 if failed else 0.35,
            severity="medium" if any("restart" in s.lower() or "failed" in s.lower() for s in symptoms) else "informational",
            missing_evidence=missing,
            evidence_refs=[str(item.get("evidence_id") or item.get("source_endpoint") or "") for item in evidence if item.get("evidence_id") or item.get("source_endpoint")],
            summary="Environment Chat diagnosis is evidence-backed and bounded to safe summaries.",
            reasoning_summary="Diagnosis summarized visible tool observations using only redacted observable evidence.",
        )


class EnvAgentRemediationPlannerChain:
    prompt_version = "env_agent_remediation_planner_v1"
    system_prompt = "Create a typed Environment Chat remediation proposal. Block unsafe requests. High-risk actions require approval. Return JSON only."

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(self, *, user_text: str, namespace: str | None, classification: dict, diagnosis: dict, model_profile: str | None = None) -> EnvAgentRemediationPlan:
        payload = {"user_text": user_text, "namespace": namespace, "classification": classification, "diagnosis": diagnosis}
        fallback = self._fallback(payload)
        raw = await _structured_response(self.llm, system=self.system_prompt, user_payload=payload, fallback=fallback.model_dump(), model_profile=model_profile)
        return _validate_model(EnvAgentRemediationPlan, fallback, raw)

    def _fallback(self, payload: dict) -> EnvAgentRemediationPlan:
        text = str(payload.get("user_text") or "")
        lowered = text.lower()
        namespace = str(payload.get("namespace") or "").strip() or None
        classification = payload.get("classification") or {}
        policy_stop = classification.get("policy_stop")
        if policy_stop:
            decision = "blocked"
            actions = [EnvAgentRemediationAction(action_type="block", namespace=namespace, risk="critical", verification_plan=["Rephrase as a bounded Environment Chat request."])]
            message = f"Request blocked by Environment Chat policy: {policy_stop}."
        elif classification.get("intent_type") != "remediation_request":
            decision = "no_action"
            actions = [EnvAgentRemediationAction(action_type="none", namespace=namespace)]
            message = "No remediation requested; Environment Chat will report diagnostics only."
        elif not namespace:
            decision = "clarify"
            actions = [EnvAgentRemediationAction(action_type="ask_clarification")]
            message = "Namespace is required before remediation proposal."
        elif _mentions_any(lowered, ("restart", "restarted", "rollout")):
            kind = _resource_kind(lowered) or "deployment"
            name = _resource_name(text, kind)
            decision = "approval_required"
            actions = [EnvAgentRemediationAction(action_type="rollout_restart", adapter="env.k8s_inspector", target_kind=kind, target_name=name, namespace=namespace, arguments={"namespace": namespace, "resource_kind": kind, "resource_name": name}, risk="high", approval_required=True, rollback_plan=["Pause and verify rollout; rollback needs a separate approved rollback action."], verification_plan=["Re-check pod health.", "Review recent events.", "Confirm restart counts stabilize."])]
            message = "Prepared an approval-gated rollout restart proposal."
        elif "uninstall" in lowered and (_mentions_any(lowered, ("helm", "release", "chart", "nginx")) or _chart_ref_from_text(text)):
            details = _helm_release_details(text, namespace)
            decision = "approval_required"
            actions = [
                EnvAgentRemediationAction(
                    action_type="helm_uninstall",
                    adapter="env.helm_manager",
                    target_kind="helm_release",
                    target_name=details["release_name"],
                    namespace=namespace,
                    arguments=details,
                    risk="high",
                    approval_required=True,
                    rollback_plan=[f"Reinstall Helm release `{details['release_name']}` from the approved chart/version if rollback is required."],
                    verification_plan=["Confirm the Helm release is absent.", "Verify related pods/services are removed.", "Review namespace events."],
                )
            ]
            message = f"Prepared an approval-gated Helm uninstall proposal for `{details['release_name']}`."
        elif "upgrade" in lowered and (_mentions_any(lowered, ("helm", "chart", "bitnami", "nginx")) or _chart_ref_from_text(text)):
            details = _helm_install_details(text, namespace)
            decision = "approval_required"
            actions = [
                EnvAgentRemediationAction(
                    action_type="helm_upgrade",
                    adapter="env.helm_manager",
                    target_kind="helm_release",
                    target_name=details["release_name"],
                    namespace=namespace,
                    arguments=details,
                    risk="high",
                    approval_required=True,
                    rollback_plan=[f"Rollback Helm release `{details['release_name']}` to the previous known-good revision if validation fails."],
                    verification_plan=["Check Helm release status/history.", "Verify pods and services created by the release.", "Review recent namespace events."],
                )
            ]
            message = f"Prepared an approval-gated Helm upgrade proposal for `{details['chart_ref']}`."
        elif _mentions_any(lowered, ("install", "installation", "deploy", "proceed", "approve", "approved", "approval")) and (_mentions_any(lowered, ("helm", "chart", "bitnami", "nginx")) or _chart_ref_from_text(text)):
            details = _helm_install_details(text, namespace)
            decision = "approval_required"
            actions = [
                EnvAgentRemediationAction(
                    action_type="helm_install",
                    adapter="env.helm_manager",
                    target_kind="helm_release",
                    target_name=details["release_name"],
                    namespace=namespace,
                    arguments=details,
                    risk="high",
                    approval_required=True,
                    rollback_plan=[f"If installation must be reverted, uninstall Helm release `{details['release_name']}` from `{namespace}` after approval."],
                    verification_plan=["Check Helm release status.", "Verify pods and services created by the release.", "Review recent namespace events."],
                )
            ]
            message = f"Prepared an approval-gated Helm install proposal for `{details['chart_ref']}`."
        elif _mentions_any(lowered, ("delete", "remove")):
            kind = _resource_kind(lowered) or "pod"
            name = _resource_name(text, kind)
            decision = "approval_required"
            actions = [
                EnvAgentRemediationAction(
                    action_type="delete",
                    adapter="env.k8s_inspector",
                    target_kind=kind,
                    target_name=name,
                    namespace=namespace,
                    arguments={"namespace": namespace, "resource_kind": kind, "resource_name": name},
                    risk="high",
                    approval_required=True,
                    rollback_plan=["Recreate the resource from source manifest, Helm release, or approved backup if required."],
                    verification_plan=["Confirm the resource is absent.", "Verify remaining namespace health.", "Review recent events."],
                )
            ]
            message = "Prepared an approval-gated namespace-scoped Kubernetes delete proposal."
        elif _mentions_any(lowered, ("apply", "create", "replace")):
            kind = _resource_kind(lowered) or "resource"
            name = _resource_name(text, kind)
            manifest = _extract_manifest_hint(text)
            decision = "approval_required"
            actions = [
                EnvAgentRemediationAction(
                    action_type="apply",
                    adapter="env.k8s_inspector",
                    target_kind=kind,
                    target_name=name,
                    namespace=namespace,
                    arguments={"namespace": namespace, "resource_kind": kind, "resource_name": name, "manifest": manifest},
                    risk="high",
                    approval_required=True,
                    rollback_plan=["Delete or revert the applied namespaced resource if validation fails."],
                    verification_plan=["Confirm the resource exists.", "Verify namespace health.", "Review recent events."],
                )
            ]
            message = "Prepared an approval-gated namespace-scoped Kubernetes apply proposal."
        elif _mentions_any(lowered, ("rollback", "previous revision")):
            decision = "approval_required"
            actions = [EnvAgentRemediationAction(action_type="helm_rollback", adapter="env.helm_manager", namespace=namespace, risk="high", approval_required=True, rollback_plan=["Identify known-good Helm revision before approval."], verification_plan=["Check Helm status and history.", "Verify pods and services after rollback."])]
            message = "Prepared an approval-gated Helm rollback proposal."
        else:
            decision = "propose"
            actions = [EnvAgentRemediationAction(action_type="none", namespace=namespace, risk="medium", verification_plan=["Collect more evidence before mutation."])]
            message = "More evidence is required before selecting safe remediation."
        approval_required = any(action.approval_required for action in actions)
        risk: Risk = "critical" if decision == "blocked" else "high" if approval_required else "medium" if decision == "propose" else "low"
        return EnvAgentRemediationPlan(prompt_version=self.prompt_version, prompt_hash=prompt_hash(prompt_version=self.prompt_version, system_prompt=self.system_prompt, payload=payload), allowed_to_execute=False, decision=decision, actions=actions, risk=risk, approval_required=approval_required, policy_notes=["Environment Chat Phase F executes only typed MCP actions after explicit human approval."], operator_message=message, reasoning_summary="Remediation plan is typed, bounded, and execution-disabled until the approval gate is accepted.")


class EnvAgentVerifierChain:
    prompt_version = "env_agent_verifier_v1"
    system_prompt = "Evaluate Environment Chat health evidence and policy status. Return JSON only."

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(self, *, diagnosis: dict, remediation: dict, evidence: list[dict], model_profile: str | None = None) -> EnvAgentVerification:
        payload = {"diagnosis": diagnosis, "remediation": remediation, "evidence": evidence}
        fallback = self._fallback(payload)
        raw = await _structured_response(self.llm, system=self.system_prompt, user_payload=payload, fallback=fallback.model_dump(), model_profile=model_profile)
        verification = _validate_model(EnvAgentVerification, fallback, raw)
        return _apply_pod_inventory_to_verification(verification, evidence)

    def _fallback(self, payload: dict) -> EnvAgentVerification:
        evidence = [item for item in payload.get("evidence") or [] if isinstance(item, dict)]
        failed = [item for item in evidence if item.get("status") in {"failed", "blocked"}]
        blocked = (payload.get("remediation") or {}).get("decision") == "blocked"
        if blocked:
            valid, status, confidence, message = False, "failed", 0.88, "Request is blocked by policy."
        elif failed:
            valid, status, confidence, message = False, "degraded", 0.58, "Evidence is partial; review failed or blocked tool calls."
        elif evidence:
            valid, status, confidence, message = True, "healthy", 0.78, "Read-only evidence was collected and is ready for operator review."
        else:
            valid, status, confidence, message = False, "unknown", 0.35, "No evidence available yet."
        return EnvAgentVerification(prompt_version=self.prompt_version, prompt_hash=prompt_hash(prompt_version=self.prompt_version, system_prompt=self.system_prompt, payload=payload), valid=valid, health_status=status, confidence=confidence, message=message, evidence_gaps=[] if evidence else ["No tool evidence has been collected."], checks={"evidence_count": len(evidence), "failed_evidence_count": len(failed), "remediation_decision": (payload.get("remediation") or {}).get("decision")}, reasoning_summary="Verifier evaluated visible evidence and policy status only.")


class EnvAgentRecoveryChain:
    prompt_version = "env_agent_recovery_v1"
    system_prompt = "Recommend bounded Environment Chat recovery for partial evidence or decision-required states. Return JSON only."

    def __init__(self, llm) -> None:
        self.llm = llm

    async def run(self, *, classification: dict, plan: dict, diagnosis: dict, verification: dict, model_profile: str | None = None) -> EnvAgentRecovery:
        payload = {"classification": classification, "plan": plan, "diagnosis": diagnosis, "verification": verification}
        fallback = self._fallback(payload)
        raw = await _structured_response(self.llm, system=self.system_prompt, user_payload=payload, fallback=fallback.model_dump(), model_profile=model_profile)
        return _validate_model(EnvAgentRecovery, fallback, raw)

    def _fallback(self, payload: dict) -> EnvAgentRecovery:
        classification = payload.get("classification") or {}
        verification = payload.get("verification") or {}
        plan = payload.get("plan") or {}
        if classification.get("needs_clarification"):
            action, retryable, escalation_required = "ask_clarifying_question", False, False
            recommendations = classification.get("clarification_questions") or ["Ask the operator for clearer scope."]
        elif plan.get("stop_conditions"):
            action, retryable, escalation_required = "fail", False, True
            recommendations = ["Resolve policy stop conditions before continuing."]
        elif verification.get("valid"):
            action, retryable, escalation_required = "continue", False, False
            recommendations = ["Continue to report generation."]
        elif verification.get("health_status") == "degraded":
            action, retryable, escalation_required = "retry", True, False
            recommendations = ["Retry failed read-only evidence calls once, then ask for operator review."]
        else:
            action, retryable, escalation_required = "decision_required", False, True
            recommendations = ["Ask the operator for more scope or approval before remediation."]
        return EnvAgentRecovery(prompt_version=self.prompt_version, prompt_hash=prompt_hash(prompt_version=self.prompt_version, system_prompt=self.system_prompt, payload=payload), action=action, retryable=retryable, escalation_required=escalation_required, recommendations=[str(item) for item in recommendations], reasoning_summary="Recovery selected a bounded action from classification, plan, and verification status.")


def prompt_hash(*, prompt_version: str, system_prompt: str, payload: dict) -> str:
    return hashlib.sha256(json.dumps({"prompt_version": prompt_version, "system_prompt": system_prompt, "payload": redact(payload)}, sort_keys=True, default=str).encode("utf-8")).hexdigest()


async def _structured_response(llm, *, system: str, user_payload: dict, fallback: dict, model_profile: str | None = None) -> dict:
    if not hasattr(llm, "structured_response"):
        return fallback
    if model_profile is not None:
        raw = await llm.structured_response(system=system, user_payload=user_payload, fallback=fallback, model_profile=model_profile)
    else:
        raw = await llm.structured_response(system=system, user_payload=user_payload, fallback=fallback)
    return raw if isinstance(raw, dict) else fallback


def _validate_model(model_type, fallback, raw: dict, normalizer=None):
    fallback_payload = fallback.model_dump()
    raw_payload = raw if isinstance(raw, dict) else {}
    payload = normalizer(fallback_payload, raw_payload) if normalizer else fallback_payload | raw_payload
    try:
        return model_type.model_validate(payload)
    except ValidationError:
        return fallback.model_copy(update={"reasoning_summary": f"{fallback.reasoning_summary} Model response did not match the structured schema; deterministic fallback was used."})


def _normalize_plan_payload(fallback_payload: dict, raw_payload: dict) -> dict:
    payload = fallback_payload | raw_payload
    raw_steps = raw_payload.get("steps")
    if not isinstance(raw_steps, list):
        return payload
    normalized = []
    for index, step in enumerate(raw_steps, start=1):
        if not isinstance(step, dict):
            continue
        normalized.append({"title": str(step.get("title") or step.get("name") or f"Step {index}"), "adapter": str(step.get("adapter") or step.get("tool_adapter") or "workflow"), "tool_name": str(step.get("tool_name") or step.get("tool") or step.get("action") or "workflow.note"), "arguments": step.get("arguments") if isinstance(step.get("arguments"), dict) else {}, "risk": str(step.get("risk") or "low"), "approval_required": bool(step.get("approval_required", False)), "status": str(step.get("status") or "planned"), "rationale": str(step.get("rationale") or step.get("reason") or "")})
    if normalized:
        payload["steps"] = normalized
    return payload


def _normalize_mode(value: Any):
    clean = str(value or "diagnostic_only").strip()
    return clean if clean in {"diagnostic_only", "propose_only", "approval_gated_remediation"} else "diagnostic_only"


def _unsafe_reason(lowered: str) -> str | None:
    if _mentions_any(lowered, ("secret", "token", "password", "credential", "kubeconfig", "private key")):
        return "secret_material_requested"
    if _mentions_any(lowered, ("delete namespace", "remove namespace", "drop namespace", "wipe namespace", "rm -rf")):
        return "destructive_action"
    if _mentions_any(lowered, ("clusterrole", "clusterrolebinding", "customresourcedefinition", "all namespaces", "--all-namespaces", "cluster-wide")):
        return "cluster_wide_change"
    return None


def _looks_like_remediation(lowered: str) -> bool:
    return _mentions_any(lowered, ("fix", "repair", "restart", "scale", "patch", "apply", "create", "replace", "delete", "remove", "uninstall", "rollback", "install", "installation", "upgrade", "deploy", "proceed", "approve", "approved", "approval", "remediate", "heal"))


def _is_root_cause_question(lowered: str) -> bool:
    return _mentions_any(
        lowered,
        (
            "root cause",
            "why",
            "reason",
            "failing",
            "failed",
            "crash",
            "backoff",
            "not ready",
            "pending",
            "init:",
        ),
    )


def _is_log_request(lowered: str) -> bool:
    return _mentions_any(lowered, ("log", "logs", "tail", "stdout", "stderr"))


def _enforce_direct_log_request(plan: EnvAgentPlan, user_text: str, namespace: str | None) -> EnvAgentPlan:
    lowered = user_text.lower()
    target_pod_name = _pod_name_from_text(user_text)
    if not (_is_log_request(lowered) and target_pod_name):
        return plan
    existing_log_step = next(
        (step for step in plan.steps if step.tool_name == "logs" and str(step.arguments.get("pod_name") or "").lower() == target_pod_name),
        None,
    )
    log_step = existing_log_step or EnvAgentPlanStep(
        title=f"Collect bounded logs for {target_pod_name}",
        adapter="env.k8s_inspector",
        tool_name="logs",
        arguments=({"namespace": namespace} if namespace else {}) | {"pod_name": target_pod_name},
        risk="medium",
        rationale="Deterministic guardrail: direct log follow-up must call the bounded pod logs MCP route.",
    )
    steps = [log_step] + [step for step in plan.steps if step is not existing_log_step and step.tool_name != "logs"]
    return plan.model_copy(
        update={
            "steps": steps,
            "reasoning_summary": (
                f"{plan.reasoning_summary} Direct pod-log follow-up detected; added bounded log collection for `{target_pod_name}`."
            ).strip(),
        }
    )


def _pod_name_from_text(text: str) -> str | None:
    lowered = text.lower()
    before_pod = re.search(r"\b([a-z0-9][a-z0-9.-]{2,})\s+pod\b", lowered)
    if before_pod:
        return before_pod.group(1).strip(".,;:")
    after_pod = re.search(r"\bpod\s+([a-z0-9][a-z0-9.-]{2,})", lowered)
    if after_pod:
        return after_pod.group(1).strip(".,;:")
    excluded = {"namespace", "agent-testing", "bosgenesis", "signoz", "problematic", "failing", "pending", "running"}
    for token in re.findall(r"\b[a-z0-9][a-z0-9.-]{4,}\b", lowered):
        if "-" in token and token not in excluded and not token.endswith("namespace"):
            return token.strip(".,;:")
    return None


def _looks_like_follow_up(lowered: str) -> bool:
    return lowered.startswith(("why", "what about", "continue", "also", "and ", "then ")) or "previous" in lowered


def _resource_kind(lowered: str) -> str | None:
    mapping = {"pod": ("pod", "pods"), "deployment": ("deployment", "deploy"), "statefulset": ("statefulset", "sts"), "service": ("service", "svc"), "ingress": ("ingress", "route"), "pvc": ("pvc", "volume"), "configmap": ("configmap", "config map"), "helm_release": ("helm", "release", "chart")}
    for kind, needles in mapping.items():
        if _mentions_any(lowered, needles):
            return kind
    return None


def _resource_name(text: str, resource_kind: str | None) -> str | None:
    if not resource_kind:
        return None
    lowered = text.lower()
    for pattern in (rf"\b{re.escape(resource_kind)}\s+([a-z0-9][a-z0-9_.-]+)", r"\bpod\s+([a-z0-9][a-z0-9_.-]+)", r"\bdeployment\s+([a-z0-9][a-z0-9_.-]+)", r"\brelease\s+([a-z0-9][a-z0-9_.-]+)"):
        match = re.search(pattern, lowered)
        if match:
            return match.group(1).strip(".,;")
    return None


def _mentions_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _intent_summary(intent_type: str, resource_kind: str | None, policy_stop: str | None) -> str:
    if policy_stop:
        return f"Classified as unsafe because it matched policy stop condition {policy_stop}."
    if intent_type == "remediation_request":
        return "Classified as remediation request; Environment Chat will propose typed, approval-gated actions."
    if intent_type == "follow_up":
        return "Classified as follow-up; Environment Chat should use current or selected run context."
    return f"Classified as diagnostic request for {resource_kind or 'namespace'} evidence."


def _symptoms_from_evidence(evidence: list[dict]) -> list[str]:
    symptoms = []
    for item in evidence:
        summary = str(item.get("summary") or "")
        if summary:
            symptoms.append(summary)
        elif item.get("action"):
            symptoms.append(f"{item.get('action')} evidence returned status {item.get('status') or 'unknown'}.")
    return symptoms[:8]


def _chart_ref_from_text(text: str) -> str | None:
    lowered = text.lower()
    oci_match = re.search(r"\boci://[^\s`'\"]+", text, flags=re.IGNORECASE)
    if oci_match:
        return oci_match.group(0).strip(".,;")
    chart_match = re.search(r"\b([a-z0-9][a-z0-9-]+/[a-z0-9][a-z0-9-]+)\b", lowered)
    if chart_match:
        return _normalize_chart_ref(chart_match.group(1).strip(".,;"))
    if "bitnami nginx" in lowered or "bitmani/nginx" in lowered or "bitnami/nginx" in lowered or "nginx" in lowered:
        return "bitnami/nginx"
    return None


def _normalize_chart_ref(chart_ref: str) -> str:
    lowered = chart_ref.lower().strip()
    common_repo_typos = {
        "bitmani": "bitnami",
        "bitami": "bitnami",
        "bitnmi": "bitnami",
    }
    if "/" not in lowered:
        return lowered
    repo, chart = lowered.split("/", 1)
    return f"{common_repo_typos.get(repo, repo)}/{chart}"


def _public_helm_repo_metadata(chart_ref: str) -> dict:
    prefix = chart_ref.split("/", 1)[0].lower() if "/" in chart_ref else ""
    repos = {
        "bitnami": {"name": "bitnami", "url": "https://charts.bitnami.com/bitnami", "oci_prefix": "oci://registry-1.docker.io/bitnamicharts"},
        "prometheus-community": {"name": "prometheus-community", "url": "https://prometheus-community.github.io/helm-charts"},
        "ingress-nginx": {"name": "ingress-nginx", "url": "https://kubernetes.github.io/ingress-nginx"},
        "jetstack": {"name": "jetstack", "url": "https://charts.jetstack.io"},
        "grafana": {"name": "grafana", "url": "https://grafana.github.io/helm-charts"},
        "metrics-server": {"name": "metrics-server", "url": "https://kubernetes-sigs.github.io/metrics-server"},
        "argo": {"name": "argo", "url": "https://argoproj.github.io/argo-helm"},
        "hashicorp": {"name": "hashicorp", "url": "https://helm.releases.hashicorp.com"},
    }
    return repos.get(prefix, {})


def _helm_release_details(text: str, namespace: str | None) -> dict:
    lowered = text.lower()
    chart_ref = _chart_ref_from_text(text)
    release_name = "nginx" if "nginx" in lowered else ""
    release_match = re.search(r"\b(?:desired\s+release\s+name|release\s+name|release)\s*[:=]?\s+([a-z0-9][a-z0-9-]*)", lowered)
    if release_match:
        release_name = release_match.group(1)
    if not release_name and chart_ref and "/" in chart_ref:
        release_name = chart_ref.rsplit("/", 1)[-1]
    if not release_name:
        release_name = _resource_name(text, "helm_release") or ""
    return {"namespace": namespace, "release_name": release_name, "chart_ref": chart_ref}


def _helm_install_details(text: str, namespace: str | None) -> dict:
    lowered = text.lower()
    chart_ref = _chart_ref_from_text(text) or "bitnami/nginx"
    release_name = chart_ref.rsplit("/", 1)[-1] if "/" in chart_ref else "nginx"
    release_match = re.search(r"\b(?:desired\s+release\s+name|release\s+name|release)\s*[:=]?\s+([a-z0-9][a-z0-9-]*)", lowered)
    if release_match:
        release_name = release_match.group(1)
    repo_metadata = _public_helm_repo_metadata(chart_ref)
    set_values = {"service.type": "ClusterIP"}
    if "loadbalancer" in lowered or "load balancer" in lowered:
        set_values["service.type"] = "LoadBalancer"
    details = {
        "namespace": namespace,
        "release_name": release_name,
        "chart_ref": chart_ref,
        "create_namespace": False,
        "atomic": True,
        "wait": True,
        "timeout": "5m",
        "set_values": set_values,
        "dry_run_first": True,
    }
    if repo_metadata:
        details["public_repo"] = repo_metadata
    if repo_metadata.get("oci_prefix") and "/" in chart_ref:
        details["oci_fallback_chart_ref"] = f"{repo_metadata['oci_prefix']}/{chart_ref.rsplit('/', 1)[-1]}"
    return details


def _extract_manifest_hint(text: str) -> dict | str:
    match = re.search(r"```(?:yaml|yml|json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


def _likely_causes(symptoms: list[str], failed: list[dict]) -> list[str]:
    if failed:
        return ["Tool evidence is partial because one or more MCP calls failed or were blocked."]
    joined = " ".join(symptoms).lower()
    if "restart" in joined or "crash" in joined:
        return ["Possible container crash, probe failure, configuration issue, image problem, or resource pressure."]
    if "event" in joined:
        return ["Recent Kubernetes events should be reviewed for scheduling, pull, mount, or readiness causes."]
    return ["No specific root cause can be confirmed until MCP evidence is collected."]


def pod_inventory_from_evidence(evidence: list[dict]) -> dict:
    pods: dict[str, dict] = {}
    for item in evidence:
        if not isinstance(item, dict):
            continue
        for source in _evidence_observation_sources(item):
            for candidate in _pod_candidates(source):
                pod = _normalize_pod_candidate(candidate)
                if not pod:
                    continue
                key = str(pod.get("name") or "").lower()
                if not key:
                    continue
                pods[key] = _merge_pod_records(pods.get(key), pod)
    ordered = sorted(pods.values(), key=lambda pod: str(pod.get("name") or ""))
    problem_pods = [pod for pod in ordered if pod.get("needs_attention")]
    ready_pods = [pod for pod in ordered if pod.get("ready") is True]
    restart_total = sum(int(pod.get("restarts") or 0) for pod in ordered)
    return {
        "pod_count": len(ordered),
        "ready_count": len(ready_pods),
        "not_ready_count": len(ordered) - len(ready_pods),
        "attention_count": len(problem_pods),
        "restart_total": restart_total,
        "health_status": "degraded" if problem_pods else "healthy" if ordered else "unknown",
        "pods": ordered,
        "problem_pods": problem_pods,
        "summary": _pod_inventory_summary(len(ordered), len(ready_pods), len(problem_pods), restart_total),
    }


def _apply_pod_inventory_to_diagnosis(diagnosis: EnvAgentDiagnosis, evidence: list[dict]) -> EnvAgentDiagnosis:
    inventory = pod_inventory_from_evidence(evidence)
    if not inventory["pod_count"]:
        return diagnosis
    symptoms = list(diagnosis.symptoms)
    symptoms.insert(0, inventory["summary"])
    for pod in inventory["problem_pods"][:6]:
        symptoms.append(_pod_problem_sentence(pod))
    symptoms = _dedupe_preserve_order(symptoms)[:12]
    likely_causes = list(diagnosis.likely_causes)
    if inventory["attention_count"]:
        likely_causes.insert(
            0,
            "One or more pods are not ready; inspect init containers, readiness probes, resource pressure, image pulls, mounts, and recent events.",
        )
    if inventory["restart_total"]:
        likely_causes.insert(0, "Container restarts are present; review logs and events for crash, probe, dependency, or resource-pressure signals.")
    update: dict[str, Any] = {
        "symptoms": symptoms,
        "likely_causes": _dedupe_preserve_order(likely_causes)[:8],
        "confidence": max(diagnosis.confidence, 0.86),
        "summary": (
            f"Environment Chat found {inventory['pod_count']} pod(s); "
            f"{inventory['attention_count']} need attention based on readiness, status, or restarts."
        ),
        "reasoning_summary": (
            f"{diagnosis.reasoning_summary} Deterministic pod inventory classified "
            f"{inventory['attention_count']} of {inventory['pod_count']} pod(s) as needing attention."
        ).strip(),
    }
    if inventory["attention_count"]:
        update["severity"] = "medium" if diagnosis.severity in {"informational", "low"} else diagnosis.severity
    return diagnosis.model_copy(update=update)


def _apply_root_cause_context(diagnosis: EnvAgentDiagnosis, user_text: str, evidence: list[dict]) -> EnvAgentDiagnosis:
    lowered = user_text.lower()
    if not _is_root_cause_question(lowered):
        return diagnosis
    inventory = pod_inventory_from_evidence(evidence)
    target_pod = _pod_name_from_text(user_text)
    if not target_pod and inventory.get("problem_pods"):
        target_pod = str(inventory["problem_pods"][0].get("name") or "")
    if not target_pod:
        return diagnosis

    target_record = _find_pod_record(inventory.get("pods") or [], target_pod)
    signals = _root_cause_signals(evidence, target_pod)
    symptoms = list(diagnosis.symptoms)
    if target_record:
        symptoms.insert(0, _pod_problem_sentence(target_record))
    for signal in signals[:5]:
        symptoms.append(signal)

    likely_causes = list(diagnosis.likely_causes)
    signal_text = " ".join(signals).lower()
    status_text = str((target_record or {}).get("status") or "").lower()
    if "backoff" in signal_text or "back-off" in signal_text or "crashloopbackoff" in status_text:
        likely_causes.insert(
            0,
            f"`{target_pod}` is most likely failing after container startup; Kubernetes is backing off restarts. Inspect logs/config to identify the application-level failure.",
        )
    if "readiness probe" in signal_text or "http probe failed" in signal_text or "http 500" in signal_text:
        likely_causes.insert(0, f"`{target_pod}` is serving a failing readiness probe, so Kubernetes keeps it out of Ready state.")
    if status_text.startswith("init") or "init:" in status_text:
        likely_causes.insert(0, f"`{target_pod}` is blocked during init container startup; inspect init container logs and mounted configuration.")
    if "pending" in status_text and "backoff" not in signal_text:
        likely_causes.insert(0, f"`{target_pod}` is Pending; check scheduling, image pull, PVC mount, and init-container events.")

    missing = list(diagnosis.missing_evidence)
    if not _successful_evidence_action(evidence, "logs"):
        missing.append(f"Bounded pod logs for `{target_pod}` including previous container logs if restarts occurred.")
    missing.append(f"Pod describe/container status details for `{target_pod}` to confirm waiting/termination reason, probes, mounts, and recent events.")
    if "otel" in target_pod or "collector" in target_pod:
        missing.append("OpenTelemetry Collector ConfigMap/chart values to confirm receiver/exporter pipeline syntax and dependency endpoints.")

    summary = (
        f"Root-cause triage for `{target_pod}` found pod-level health issues"
        f"{(' and ' + str(len(signals)) + ' event/log signal(s)') if signals else ''}. "
        "Current evidence supports a bounded diagnosis, but exact application failure needs pod logs/describe evidence before mutation."
    )
    return diagnosis.model_copy(
        update={
            "symptoms": _dedupe_preserve_order(symptoms)[:14],
            "likely_causes": _dedupe_preserve_order(likely_causes)[:8],
            "missing_evidence": _dedupe_preserve_order(missing)[:8],
            "confidence": max(diagnosis.confidence, 0.84 if signals or target_record else diagnosis.confidence),
            "severity": "medium" if diagnosis.severity in {"informational", "low"} else diagnosis.severity,
            "summary": summary,
            "reasoning_summary": (
                f"{diagnosis.reasoning_summary} Root-cause prompt targeted `{target_pod}`; "
                "diagnosis was constrained to observed pod inventory, events, and bounded log availability."
            ).strip(),
        }
    )


def _find_pod_record(pods: list[dict], target_pod: str) -> dict | None:
    target = target_pod.lower()
    for pod in pods:
        name = str(pod.get("name") or "").lower()
        if name == target or name.startswith(target) or target.startswith(name):
            return pod
    return None


def _successful_evidence_action(evidence: list[dict], action: str) -> bool:
    return any(isinstance(item, dict) and item.get("action") == action and item.get("status") == "success" for item in evidence)


def _root_cause_signals(evidence: list[dict], target_pod: str) -> list[str]:
    target = target_pod.lower()
    signals: list[str] = []
    keywords = (
        "backoff",
        "back-off",
        "crashloopbackoff",
        "failed",
        "error",
        "readiness probe",
        "liveness probe",
        "http probe",
        "http 500",
        "pending",
        "init:",
        "failedmount",
        "failedscheduling",
        "imagepull",
        "oom",
    )
    for item in evidence:
        if not isinstance(item, dict):
            continue
        for source in _evidence_observation_sources(item):
            for text in _strings_from_value(source):
                lowered = text.lower()
                if target not in lowered and not any(keyword in lowered for keyword in ("backoff", "probe", "failed", "init:")):
                    continue
                for line in text.splitlines() or [text]:
                    clean = " ".join(line.split())
                    lower_line = clean.lower()
                    if clean and (target in lower_line or any(keyword in lower_line for keyword in keywords)):
                        signals.append(f"Observed for `{target_pod}`: {clean[:260]}")
    return _dedupe_preserve_order(signals)[:8]


def _strings_from_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        strings: list[str] = []
        for item in value:
            strings.extend(_strings_from_value(item))
        return strings
    if isinstance(value, dict):
        strings = []
        for key, item in value.items():
            if isinstance(item, (str, int, float, bool)):
                strings.append(f"{key}: {item}")
            else:
                strings.extend(_strings_from_value(item))
        return strings
    return []


def _apply_pod_inventory_to_verification(verification: EnvAgentVerification, evidence: list[dict]) -> EnvAgentVerification:
    inventory = pod_inventory_from_evidence(evidence)
    if not inventory["pod_count"]:
        return verification
    checks = dict(verification.checks)
    checks["pod_inventory"] = {
        "pod_count": inventory["pod_count"],
        "ready_count": inventory["ready_count"],
        "attention_count": inventory["attention_count"],
        "restart_total": inventory["restart_total"],
        "problem_pods": [
            {
                "name": pod.get("name"),
                "ready": pod.get("ready_detail"),
                "status": pod.get("status"),
                "restarts": pod.get("restarts"),
                "issues": pod.get("issues"),
            }
            for pod in inventory["problem_pods"][:10]
        ],
    }
    if inventory["attention_count"]:
        return verification.model_copy(
            update={
                "valid": False,
                "health_status": "degraded",
                "confidence": max(verification.confidence, 0.88),
                "message": (
                    f"{inventory['attention_count']} of {inventory['pod_count']} pod(s) need attention. "
                    "Review readiness, init state, restarts, logs, and events before declaring the namespace healthy."
                ),
                "checks": checks,
                "reasoning_summary": (
                    f"{verification.reasoning_summary} Pod inventory found non-ready or restarting pod evidence."
                ).strip(),
            }
        )
    return verification.model_copy(
        update={
            "valid": True,
            "health_status": "healthy",
            "confidence": max(verification.confidence, 0.86),
            "message": f"All {inventory['pod_count']} observed pod(s) are ready with no restart concern in the collected evidence.",
            "checks": checks,
        }
    )


def _evidence_observation_sources(item: dict) -> list[Any]:
    sources = []
    for key in ("raw", "observation_redacted", "observation", "data", "result"):
        if key in item:
            sources.append(item.get(key))
    if not sources:
        sources.append(item)
    return sources


def _pod_candidates(value: Any) -> list[Any]:
    candidates: list[Any] = []
    if isinstance(value, str):
        candidates.extend(_pod_candidates_from_text(value))
        return candidates
    if isinstance(value, list):
        for item in value:
            candidates.extend(_pod_candidates(item))
        return candidates
    if not isinstance(value, dict):
        return candidates
    if _looks_like_pod(value):
        candidates.append(value)
    for key in ("items", "pods", "data", "results", "resources"):
        child = value.get(key)
        if isinstance(child, (list, dict, str)):
            candidates.extend(_pod_candidates(child))
    return candidates


def _pod_candidates_from_text(text: str) -> list[dict]:
    rows: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.upper().startswith("NAME "):
            continue
        parts = line.split()
        if len(parts) < 3 or "/" not in parts[1]:
            continue
        restarts = 0
        if len(parts) >= 4:
            match = re.search(r"\d+", parts[3])
            if match:
                restarts = int(match.group(0))
        rows.append({"name": parts[0], "ready": parts[1], "status": parts[2], "restarts": restarts})
    return rows


def _looks_like_pod(value: dict) -> bool:
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    status = value.get("status") if isinstance(value.get("status"), dict) else None
    if str(value.get("kind") or "").lower() == "pod":
        return True
    if metadata.get("name") and isinstance(status, dict) and ("phase" in status or "containerStatuses" in status):
        return True
    keys = {str(key).lower() for key in value}
    return bool({"pod", "pod_name", "name"} & keys and {"ready", "status", "phase", "restarts", "restart_count", "restartcount"} & keys)


def _normalize_pod_candidate(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    status = value.get("status") if isinstance(value.get("status"), dict) else {}
    name = str(metadata.get("name") or value.get("name") or value.get("pod") or value.get("pod_name") or "").strip()
    if not name:
        return None
    namespace = str(metadata.get("namespace") or value.get("namespace") or "").strip() or None
    ready, ready_detail = _pod_ready(value, status)
    status_text = _pod_status_text(value, status)
    restarts = _pod_restart_count(value, status)
    issues = _pod_issue_reasons(ready, status_text, restarts)
    return {
        "name": name,
        "namespace": namespace,
        "ready": ready,
        "ready_detail": ready_detail,
        "status": status_text,
        "restarts": restarts,
        "needs_attention": bool(issues),
        "issues": issues,
    }


def _pod_ready(value: dict, status: dict) -> tuple[bool | None, str]:
    ready_value = value.get("ready")
    if isinstance(ready_value, str) and "/" in ready_value:
        left, _, right = ready_value.partition("/")
        try:
            ready = int(left) == int(right) and int(right) > 0
            return ready, ready_value
        except ValueError:
            return None, ready_value
    if isinstance(ready_value, bool):
        return ready_value, "true" if ready_value else "false"
    for key in ("is_ready", "ready_bool", "containers_ready"):
        candidate = value.get(key)
        if isinstance(candidate, bool):
            return candidate, "true" if candidate else "false"
        if isinstance(candidate, str) and "/" in candidate:
            left, _, right = candidate.partition("/")
            try:
                ready = int(left) == int(right) and int(right) > 0
                return ready, candidate
            except ValueError:
                pass
    containers = _container_statuses(status)
    if containers:
        ready_count = len([container for container in containers if container.get("ready") is True])
        total = len(containers)
        return ready_count == total, f"{ready_count}/{total}"
    conditions = status.get("conditions") if isinstance(status.get("conditions"), list) else []
    for condition in conditions:
        if isinstance(condition, dict) and condition.get("type") == "Ready":
            condition_status = str(condition.get("status") or "").lower()
            return condition_status == "true", condition_status or "unknown"
    return None, "unknown"


def _pod_status_text(value: dict, status: dict) -> str:
    for key in ("status", "phase", "state", "reason"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    waiting_reason = _first_container_state_reason(status)
    if waiting_reason:
        return str(waiting_reason)
    return str(status.get("phase") or "unknown")


def _pod_restart_count(value: dict, status: dict) -> int:
    for key in ("restarts", "restart_count", "restartCount", "restart_count_total"):
        candidate = value.get(key)
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, str):
            match = re.search(r"\d+", candidate)
            if match:
                return int(match.group(0))
    total = 0
    for container in _container_statuses(status, include_init=True):
        restart_count = container.get("restartCount")
        if isinstance(restart_count, int):
            total += restart_count
    return total


def _container_statuses(status: dict, *, include_init: bool = False) -> list[dict]:
    containers = status.get("containerStatuses") if isinstance(status.get("containerStatuses"), list) else []
    if include_init:
        init_containers = status.get("initContainerStatuses") if isinstance(status.get("initContainerStatuses"), list) else []
        containers = list(containers) + list(init_containers)
    return [container for container in containers if isinstance(container, dict)]


def _first_container_state_reason(status: dict) -> str | None:
    for container in _container_statuses(status, include_init=True):
        state = container.get("state") if isinstance(container.get("state"), dict) else {}
        for state_name in ("waiting", "terminated"):
            detail = state.get(state_name)
            if isinstance(detail, dict) and detail.get("reason"):
                return str(detail["reason"])
    return None


def _pod_issue_reasons(ready: bool | None, status_text: str, restarts: int) -> list[str]:
    issues: list[str] = []
    status_lower = status_text.lower()
    if ready is False:
        issues.append("not ready")
    if status_lower.startswith("init:") or status_lower in {"pending", "failed", "unknown", "crashloopbackoff", "imagepullbackoff", "errimagepull"}:
        issues.append(f"status {status_text}")
    if "crash" in status_lower or "backoff" in status_lower or "error" in status_lower:
        issues.append(f"status {status_text}")
    if restarts > 0:
        issues.append(f"{restarts} restart(s)")
    return _dedupe_preserve_order(issues)


def _merge_pod_records(existing: dict | None, incoming: dict) -> dict:
    if existing is None:
        return incoming
    merged = dict(existing)
    merged["ready"] = False if existing.get("ready") is False or incoming.get("ready") is False else incoming.get("ready") if existing.get("ready") is None else existing.get("ready")
    merged["ready_detail"] = incoming.get("ready_detail") if incoming.get("ready_detail") != "unknown" else existing.get("ready_detail")
    merged["status"] = _prefer_problem_status(str(existing.get("status") or "unknown"), str(incoming.get("status") or "unknown"))
    merged["restarts"] = max(int(existing.get("restarts") or 0), int(incoming.get("restarts") or 0))
    merged["issues"] = _dedupe_preserve_order(list(existing.get("issues") or []) + list(incoming.get("issues") or []))
    merged["needs_attention"] = bool(merged["issues"])
    return merged


def _prefer_problem_status(left: str, right: str) -> str:
    neutral = {"running", "succeeded", "completed", "unknown"}
    if right.lower() not in neutral:
        return right
    return left


def _pod_inventory_summary(total: int, ready: int, attention: int, restarts: int) -> str:
    if total == 0:
        return "No pod records were found in the collected evidence."
    summary = f"Pod inventory: {total} total, {ready} ready, {attention} needing attention."
    if restarts:
        summary += f" Observed restart count across pods: {restarts}."
    return summary


def _pod_problem_sentence(pod: dict) -> str:
    details = ", ".join(
        [
            f"ready {pod.get('ready_detail')}",
            f"status {pod.get('status')}",
            f"restarts {pod.get('restarts')}",
            f"issues: {', '.join(pod.get('issues') or [])}",
        ]
    )
    return f"Pod `{pod.get('name')}` needs attention ({details})."


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = str(item).strip()
        key = clean.lower()
        if clean and key not in seen:
            output.append(clean)
            seen.add(key)
    return output








