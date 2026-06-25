from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from backend.app.config import Settings
from backend.app.db.database import RunRepository
from backend.app.policy.evaluator import PolicyGuard, yaml
from backend.app.tools.contracts import ToolExecutionRequest
from backend.app.tools.registry import ToolRegistry


class L4ToolStep(BaseModel):
    step_id: str | None = None
    title: str = ""
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    risk_level: str | None = None
    validation: dict = Field(default_factory=dict)
    rollback: dict = Field(default_factory=dict)


class L4EligibilityRequest(BaseModel):
    run_id: str | None = None
    workflow_type: str = "k8s_management"
    environment: str = "local"
    namespace: str | None = "bosgenesis"
    tool_sequence: list[L4ToolStep] = Field(default_factory=list)
    procedure_id: str | None = None
    procedure_version: str | None = None
    rollback_metadata: dict = Field(default_factory=dict)
    validation_checks: list[dict] = Field(default_factory=list)
    retry_count: int = 0
    elapsed_seconds: int = 0
    logging_available: bool = True
    autonomy_mode: str = "conditional_l4"
    create_audit: bool = True


class L4StopCheckRequest(BaseModel):
    risk_level: str = "low"
    policy_decision: str = "allow"
    validation_failure_count: int = 0
    secret_detected: bool = False
    rollback_ready: bool = True
    retry_count: int = 0
    elapsed_seconds: int = 0
    logging_available: bool = True
    tool_output_contradiction: bool = False
    model_uncertainty: float = 0.0


class ProcedureCreateRequest(BaseModel):
    name: str
    workflow_type: str
    version: str = "v1"
    status: str = "approved"
    steps: list[L4ToolStep] = Field(default_factory=list)
    policies: list[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class ConditionalL4Odd:
    enabled: bool = False
    allowed_workflows: set[str] = field(default_factory=lambda: {"k8s_management", "helm_management"})
    allowed_environments: set[str] = field(default_factory=lambda: {"local", "dev"})
    allowed_namespaces: set[str] = field(default_factory=lambda: {"bosgenesis"})
    allowed_roles: set[str] = field(default_factory=lambda: {"admin", "operator"})
    allowed_tools: set[str] = field(default_factory=set)
    allowed_risk_levels: set[str] = field(default_factory=lambda: {"low", "medium_preapproved"})
    approved_procedures: set[str] = field(default_factory=set)
    max_retries: int = 2
    max_duration_seconds: int = 900
    requires_approved_procedure: bool = True
    requires_rollback_metadata: bool = True
    requires_validation_rules: bool = True
    requires_postgresql_logging: bool = True
    rollback_fields: tuple[str, ...] = (
        "rollback_plan",
        "pre_change_state",
        "validation_plan",
        "owner",
    )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "allowed_workflows": sorted(self.allowed_workflows),
            "allowed_environments": sorted(self.allowed_environments),
            "allowed_namespaces": sorted(self.allowed_namespaces),
            "allowed_roles": sorted(self.allowed_roles),
            "allowed_tools": sorted(self.allowed_tools),
            "allowed_risk_levels": sorted(self.allowed_risk_levels),
            "approved_procedures": sorted(self.approved_procedures),
            "max_retries": self.max_retries,
            "max_duration_seconds": self.max_duration_seconds,
            "requires_approved_procedure": self.requires_approved_procedure,
            "requires_rollback_metadata": self.requires_rollback_metadata,
            "requires_validation_rules": self.requires_validation_rules,
            "requires_postgresql_logging": self.requires_postgresql_logging,
            "rollback_fields": list(self.rollback_fields),
        }


class StopConditionEngine:
    def __init__(self, odd: ConditionalL4Odd) -> None:
        self.odd = odd

    def evaluate(self, request: L4StopCheckRequest) -> dict:
        reasons: list[str] = []
        if request.risk_level == "critical":
            reasons.append("critical_risk_detected")
        if request.policy_decision == "deny":
            reasons.append("policy_denial")
        if request.validation_failure_count >= 2:
            reasons.append("validation_failed_twice")
        if request.secret_detected:
            reasons.append("secret_like_output_detected")
        if not request.rollback_ready:
            reasons.append("missing_rollback_state")
        if request.retry_count > self.odd.max_retries:
            reasons.append("retry_budget_exceeded")
        if request.elapsed_seconds > self.odd.max_duration_seconds:
            reasons.append("run_duration_exceeded")
        if request.tool_output_contradiction:
            reasons.append("contradictory_tool_output")
        if not request.logging_available:
            reasons.append("postgresql_logging_unavailable")
        if request.model_uncertainty >= 0.8:
            reasons.append("model_uncertainty_high")
        return {
            "stop": bool(reasons),
            "reasons": reasons,
            "max_retries": self.odd.max_retries,
            "max_duration_seconds": self.odd.max_duration_seconds,
        }


class ConditionalL4Service:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: RunRepository,
        policy_guard: PolicyGuard,
        tool_registry: ToolRegistry,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.policy_guard = policy_guard
        self.tool_registry = tool_registry
        self.odd = load_l4_odd(settings.policy_rules_path)
        self.stop_conditions = StopConditionEngine(self.odd)

    def evaluate_eligibility(
        self,
        request: L4EligibilityRequest,
        *,
        user_id: str,
        user_roles: list[str] | tuple[str, ...],
    ) -> dict:
        reasons: list[str] = []
        checks: dict[str, bool] = {}
        policy_decisions: list[dict] = []

        self._check(bool(self.odd.enabled), "l4_enabled", "Conditional L4 is disabled by ODD.", reasons, checks)
        self._check(
            bool(set(user_roles).intersection(self.odd.allowed_roles)),
            "user_authorized",
            "User role is not approved for conditional L4.",
            reasons,
            checks,
        )
        self._check(
            request.workflow_type in self.odd.allowed_workflows,
            "workflow_authorized",
            f"Workflow '{request.workflow_type}' is not L4-approved.",
            reasons,
            checks,
        )
        self._check(
            request.environment in self.odd.allowed_environments,
            "environment_authorized",
            f"Environment '{request.environment}' is not L4-approved.",
            reasons,
            checks,
        )
        self._check(
            not request.namespace or request.namespace in self.odd.allowed_namespaces,
            "namespace_authorized",
            f"Namespace '{request.namespace}' is not L4-approved.",
            reasons,
            checks,
        )
        self._check(
            request.retry_count <= self.odd.max_retries,
            "retry_budget_available",
            "Retry budget exceeded before L4 execution.",
            reasons,
            checks,
        )
        self._check(
            request.elapsed_seconds <= self.odd.max_duration_seconds,
            "duration_budget_available",
            "Run duration budget exceeded before L4 execution.",
            reasons,
            checks,
        )
        if self.odd.requires_postgresql_logging:
            self._check(
                request.logging_available,
                "postgresql_logging_available",
                "PostgreSQL logging is required for L4 but is unavailable.",
                reasons,
                checks,
            )

        procedure = self._check_procedure(request, reasons, checks)
        self._check_rollback(request, reasons, checks)
        self._check_validation(request, reasons, checks)
        self._check_tool_sequence(request, user_id, user_roles, reasons, checks, policy_decisions)

        rollback_ready = checks.get("rollback_ready", True)
        worst_risk = _worst_risk([item.get("risk_level", "unknown") for item in policy_decisions])
        policy_denied = any(item.get("decision") == "deny" for item in policy_decisions)
        stop = self.stop_conditions.evaluate(
            L4StopCheckRequest(
                risk_level=worst_risk,
                policy_decision="deny" if policy_denied else "allow",
                retry_count=request.retry_count,
                elapsed_seconds=request.elapsed_seconds,
                logging_available=request.logging_available,
                rollback_ready=rollback_ready,
            )
        )
        if stop["stop"]:
            reasons.extend(f"Stop condition matched: {reason}" for reason in stop["reasons"])

        eligible = not reasons
        result = {
            "eligible": eligible,
            "decision": "eligible" if eligible else "ineligible",
            "reasons": reasons,
            "checks": checks,
            "odd_config": self.odd.to_dict(),
            "procedure": procedure,
            "tool_policy_decisions": policy_decisions,
            "stop_conditions": stop,
            "audit_id": None,
        }
        if request.create_audit:
            audit = self.repository.create_l4_audit_record(
                run_id=request.run_id,
                user_id=user_id,
                workflow_type=request.workflow_type,
                environment=request.environment,
                namespace=request.namespace,
                eligible=eligible,
                decision=result["decision"],
                reasons=reasons,
                odd_config=self.odd.to_dict(),
                tool_sequence=[step.model_dump() for step in request.tool_sequence],
                procedure_id=request.procedure_id,
                procedure_version=request.procedure_version,
                stop_conditions=stop,
            )
            result["audit_id"] = audit["audit_id"]
        return result

    def create_procedure(self, request: ProcedureCreateRequest, *, owner_user_id: str) -> dict:
        return self.repository.create_procedure(
            procedure_id=f"proc_{uuid4().hex}",
            name=request.name,
            workflow_type=request.workflow_type,
            owner_user_id=owner_user_id,
            version=request.version,
            status=request.status,
            approved_by_user_id=owner_user_id if request.status == "approved" else None,
            steps=[step.model_dump() for step in request.steps],
            policies=request.policies,
            metadata=request.metadata,
        )

    def export_audit_markdown(self, audit: dict) -> str:
        lines = [
            f"# L4 Audit Report: {audit['audit_id']}",
            "",
            f"- Decision: `{audit['decision']}`",
            f"- Eligible: `{audit['eligible']}`",
            f"- Workflow: `{audit['workflow_type']}`",
            f"- Environment: `{audit['environment']}`",
            f"- Namespace: `{audit.get('namespace') or 'not provided'}`",
            f"- Procedure: `{audit.get('procedure_id') or 'not provided'}`",
            "",
            "## Reasons",
        ]
        reasons = audit.get("reasons") or []
        lines.extend([f"- {reason}" for reason in reasons] or ["- None"])
        lines.extend(
            [
                "",
                "## ODD Configuration",
                "```json",
                _jsonish(audit.get("odd_config")),
                "```",
                "",
                "## Tool Sequence",
                "```json",
                _jsonish(audit.get("tool_sequence")),
                "```",
                "",
                "## Stop Conditions",
                "```json",
                _jsonish(audit.get("stop_conditions")),
                "```",
            ]
        )
        return "\n".join(lines)

    def _check_procedure(
        self,
        request: L4EligibilityRequest,
        reasons: list[str],
        checks: dict[str, bool],
    ) -> dict | None:
        if not request.procedure_id:
            if self.odd.requires_approved_procedure:
                self._check(False, "procedure_approved", "Approved procedure is required for L4.", reasons, checks)
            else:
                checks["procedure_approved"] = True
            return None
        procedure = self.repository.get_approved_procedure(
            procedure_id=request.procedure_id,
            version=request.procedure_version,
        )
        approved = bool(procedure)
        if approved and self.odd.approved_procedures:
            approved = request.procedure_id in self.odd.approved_procedures
        self._check(
            approved,
            "procedure_approved",
            f"Procedure '{request.procedure_id}' is not approved for L4.",
            reasons,
            checks,
        )
        return procedure

    def _check_rollback(
        self,
        request: L4EligibilityRequest,
        reasons: list[str],
        checks: dict[str, bool],
    ) -> None:
        if not self.odd.requires_rollback_metadata:
            checks["rollback_ready"] = True
            return
        missing = [field for field in self.odd.rollback_fields if not request.rollback_metadata.get(field)]
        self._check(
            not missing,
            "rollback_ready",
            f"Rollback metadata is missing required fields: {', '.join(missing)}.",
            reasons,
            checks,
        )

    def _check_validation(
        self,
        request: L4EligibilityRequest,
        reasons: list[str],
        checks: dict[str, bool],
    ) -> None:
        if not self.odd.requires_validation_rules:
            checks["validation_available"] = True
            return
        self._check(
            bool(request.validation_checks),
            "validation_available",
            "At least one validation check is required for L4.",
            reasons,
            checks,
        )

    def _check_tool_sequence(
        self,
        request: L4EligibilityRequest,
        user_id: str,
        user_roles: list[str] | tuple[str, ...],
        reasons: list[str],
        checks: dict[str, bool],
        policy_decisions: list[dict],
    ) -> None:
        if not request.tool_sequence:
            self._check(False, "tool_sequence_authorized", "Tool sequence is required for L4.", reasons, checks)
            return
        sequence_ok = True
        for index, step in enumerate(request.tool_sequence, start=1):
            definition = self.tool_registry.get(step.tool_name)
            if not definition:
                reasons.append(f"Step {index} tool '{step.tool_name}' is not registered.")
                sequence_ok = False
                continue
            if self.odd.allowed_tools and step.tool_name not in self.odd.allowed_tools:
                reasons.append(f"Step {index} tool '{step.tool_name}' is not L4-approved.")
                sequence_ok = False
            tool_request = ToolExecutionRequest(
                run_id=request.run_id or f"l4_{uuid4().hex}",
                step_id=step.step_id or f"l4_step_{index}",
                tool_name=step.tool_name,
                workflow_type=request.workflow_type,
                environment=request.environment,
                namespace=request.namespace,
                user_id=user_id,
                arguments=step.arguments,
                autonomy_mode=request.autonomy_mode,
            )
            policy = self.policy_guard.evaluate_tool(tool_request, user_roles=user_roles)
            risk_level = step.risk_level or definition.risk_level or policy.risk_level
            policy_dict = policy.to_dict()
            policy_dict.update(
                {
                    "step_id": tool_request.step_id,
                    "tool_name": step.tool_name,
                    "risk_level": risk_level,
                }
            )
            policy_decisions.append(policy_dict)
            if policy.decision != "allow":
                reasons.append(f"Step {index} policy decision is '{policy.decision}'.")
                sequence_ok = False
            if _normalize_risk(risk_level) not in self.odd.allowed_risk_levels:
                reasons.append(f"Step {index} risk level '{risk_level}' is outside L4 threshold.")
                sequence_ok = False
        checks["tool_sequence_authorized"] = sequence_ok

    @staticmethod
    def _check(
        condition: bool,
        check_name: str,
        reason: str,
        reasons: list[str],
        checks: dict[str, bool],
    ) -> None:
        checks[check_name] = condition
        if not condition:
            reasons.append(reason)


def load_l4_odd(path: str) -> ConditionalL4Odd:
    policy_path = Path(path)
    if not policy_path.exists() or yaml is None:
        return ConditionalL4Odd()
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    odd = data.get("operational_design_domain") or {}
    l4 = odd.get("conditional_l4") or {}
    rollback = data.get("rollback_requirements") or {}
    return ConditionalL4Odd(
        enabled=bool(l4.get("enabled", False)),
        allowed_workflows=_string_set(l4.get("allowed_workflows"), {"k8s_management", "helm_management"}),
        allowed_environments=_string_set(l4.get("allowed_environments"), {"local", "dev"}),
        allowed_namespaces=_string_set(l4.get("allowed_namespaces"), {"bosgenesis"}),
        allowed_roles=_string_set(l4.get("allowed_roles"), {"admin", "operator"}),
        allowed_tools=_string_set(l4.get("allowed_tools"), set()),
        allowed_risk_levels=_string_set(l4.get("allowed_risk_levels"), {"low", "medium_preapproved"}),
        approved_procedures=_string_set(l4.get("approved_procedures"), set()),
        max_retries=int(l4.get("max_retries", 2)),
        max_duration_seconds=int(l4.get("max_duration_seconds", 900)),
        requires_approved_procedure=bool(l4.get("requires_approved_procedure", True)),
        requires_rollback_metadata=bool(l4.get("requires_rollback_metadata", True)),
        requires_validation_rules=bool(l4.get("requires_validation_rules", True)),
        requires_postgresql_logging=bool(l4.get("requires_postgresql_logging", True)),
        rollback_fields=tuple(rollback.get("fields") or ConditionalL4Odd().rollback_fields),
    )


def _string_set(value: Any, default: set[str]) -> set[str]:
    if not value:
        return set(default)
    return {str(item) for item in value}


def _normalize_risk(risk_level: str) -> str:
    if risk_level == "medium":
        return "medium_preapproved"
    return risk_level


def _worst_risk(risk_levels: list[str]) -> str:
    order = {"unknown": 0, "low": 1, "medium_preapproved": 2, "medium": 2, "high": 3, "critical": 4}
    return max(risk_levels or ["unknown"], key=lambda value: order.get(value, 0))


def _jsonish(value: Any) -> str:
    import json

    return json.dumps(value, indent=2, sort_keys=True)
