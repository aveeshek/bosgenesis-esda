from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest
from backend.app.tools.registry import ToolDefinition, ToolRegistry

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only in minimal environments.
    yaml = None


PolicyDecisionValue = Literal["allow", "deny", "approval_required"]


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    description: str
    decision: PolicyDecisionValue


@dataclass(frozen=True)
class PolicyDecision:
    decision: PolicyDecisionValue
    risk_level: str
    reasons: list[str]
    matched_rules: list[str]
    approval_required: bool = False
    expected_impact: str = ""
    rollback_note: str = ""

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "risk_level": self.risk_level,
            "approval_required": self.approval_required,
            "reasons": self.reasons,
            "matched_rules": self.matched_rules,
            "expected_impact": self.expected_impact,
            "rollback_note": self.rollback_note,
        }


@dataclass(frozen=True)
class PolicyRules:
    version: int = 1
    allowed_workflows: set[str] = field(default_factory=set)
    allowed_environments: set[str] = field(default_factory=set)
    allowed_namespaces: set[str] = field(default_factory=set)
    denied_actions: set[str] = field(default_factory=set)
    approval_required_actions: set[str] = field(default_factory=set)
    production_mutation_allowed: bool = False

    @classmethod
    def defaults(cls) -> "PolicyRules":
        return cls(
            version=1,
            allowed_workflows={
                "health_check_diagnostic",
                "release_note_creation",
                "mop_creation",
                "mop_execution",
                "helm_management",
                "k8s_management",
            },
            allowed_environments={"local", "dev", "stage"},
            allowed_namespaces={"bosgenesis"},
            denied_actions={
                "raw_shell",
                "raw_powershell",
                "kubernetes_secret_read",
                "namespace_delete",
                "helm_uninstall",
                "credential_extraction",
                "unbounded_web_download",
            },
            approval_required_actions={
                "kubernetes_restart",
                "kubernetes_patch",
                "helm_upgrade",
                "helm_rollback",
                "production_write",
                "long_running_job",
            },
            production_mutation_allowed=False,
        )


class PolicyGuard:
    def __init__(self, *, settings: Settings, tool_registry: ToolRegistry) -> None:
        self.settings = settings
        self.tool_registry = tool_registry
        self.rules = load_policy_rules(settings.policy_rules_path)

    def evaluate_tool(
        self,
        request: ToolExecutionRequest,
        *,
        user_roles: list[str] | tuple[str, ...] = (),
    ) -> PolicyDecision:
        definition = self.tool_registry.get(request.tool_name)
        action = classify_action(request.tool_name, request.arguments)
        reasons: list[str] = []
        matched_rules: list[str] = []

        denied = self._deny_reasons(
            request=request,
            definition=definition,
            action=action,
            user_roles=set(user_roles),
        )
        if denied:
            return PolicyDecision(
                decision="deny",
                risk_level="critical" if action in self.rules.denied_actions else "high",
                reasons=denied,
                matched_rules=[*matched_rules, "policy.deny"],
                expected_impact=expected_impact(action, request),
                rollback_note=rollback_note(action),
            )

        if action in self.rules.approval_required_actions:
            reasons.append(f"Action '{action}' requires human approval before execution.")
            matched_rules.append(f"approval_required.{action}")
            return PolicyDecision(
                decision="approval_required",
                risk_level="high",
                reasons=reasons,
                matched_rules=matched_rules,
                approval_required=True,
                expected_impact=expected_impact(action, request),
                rollback_note=rollback_note(action),
            )

        risk_level = definition.risk_level if definition else "unknown"
        if risk_level in {"high", "critical"}:
            reasons.append(f"Tool risk level '{risk_level}' requires human approval.")
            matched_rules.append(f"risk_level.{risk_level}")
            return PolicyDecision(
                decision="approval_required",
                risk_level=risk_level,
                reasons=reasons,
                matched_rules=matched_rules,
                approval_required=True,
                expected_impact=expected_impact(action, request),
                rollback_note=rollback_note(action),
            )

        return PolicyDecision(
            decision="allow",
            risk_level=risk_level,
            reasons=["Request is inside the configured policy and tool registry boundaries."],
            matched_rules=["policy.allow"],
            expected_impact=expected_impact(action, request),
            rollback_note=rollback_note(action),
        )

    def _deny_reasons(
        self,
        *,
        request: ToolExecutionRequest,
        definition: ToolDefinition | None,
        action: str,
        user_roles: set[str],
    ) -> list[str]:
        reasons: list[str] = []
        if action in self.rules.denied_actions:
            reasons.append(f"Action '{action}' is explicitly denied.")
        if request.workflow_type not in self.rules.allowed_workflows:
            reasons.append(f"Workflow '{request.workflow_type}' is outside the policy ODD.")
        if request.environment not in self.rules.allowed_environments:
            reasons.append(f"Environment '{request.environment}' is outside the policy ODD.")
        if request.namespace and request.namespace not in self.rules.allowed_namespaces:
            reasons.append(f"Namespace '{request.namespace}' is outside the policy ODD.")
        if request.environment == "prod" and _is_mutation_action(action):
            if not self.rules.production_mutation_allowed:
                reasons.append("Production mutation is disabled by policy.")
        if not definition:
            reasons.append(f"Tool '{request.tool_name}' is not registered.")
            return reasons
        if not definition.enabled:
            reasons.append(f"Tool '{request.tool_name}' is disabled.")
        if not definition.allows(
            workflow_type=request.workflow_type,
            environment=request.environment,
        ):
            reasons.append(
                f"Tool '{request.tool_name}' is not allowed for "
                f"{request.workflow_type}/{request.environment}."
            )
        if user_roles and not user_roles.intersection(definition.allowed_roles):
            reasons.append(f"User role is not allowed to execute '{request.tool_name}'.")
        return reasons


def load_policy_rules(path: str) -> PolicyRules:
    defaults = PolicyRules.defaults()
    policy_path = Path(path)
    if not policy_path.exists() or yaml is None:
        return defaults
    data = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    odd = data.get("operational_design_domain") or {}
    mutation_policy = data.get("mutation_policy") or {}
    production = odd.get("production") or {}
    return PolicyRules(
        version=int(data.get("version") or defaults.version),
        allowed_workflows=_string_set(odd.get("allowed_workflows"), defaults.allowed_workflows),
        allowed_environments=_string_set(
            odd.get("allowed_environments"),
            defaults.allowed_environments,
        ),
        allowed_namespaces=_string_set(
            odd.get("allowed_namespaces"),
            defaults.allowed_namespaces,
        ),
        denied_actions=_string_set(mutation_policy.get("denied"), defaults.denied_actions),
        approval_required_actions=_string_set(
            mutation_policy.get("always_requires_approval"),
            defaults.approval_required_actions,
        ),
        production_mutation_allowed=bool(
            production.get("mutation_allowed", defaults.production_mutation_allowed)
        ),
    )


def classify_action(tool_name: str, arguments: dict[str, Any] | None = None) -> str:
    arguments = arguments or {}
    lowered_tool = tool_name.lower()
    haystack = " ".join(
        [
            lowered_tool,
            str(arguments.get("action", "")).lower(),
            str(arguments.get("operation", "")).lower(),
            str(arguments.get("template_id", "")).lower(),
            str(arguments.get("resource", "")).lower(),
            str(arguments.get("kind", "")).lower(),
        ]
    )
    if lowered_tool in {"shell.raw", "bash.raw", "cmd.raw"} or "raw_shell" in haystack:
        return "raw_shell"
    if lowered_tool == "powershell.raw" or arguments.get("command"):
        return "raw_powershell"
    if "secret" in haystack and any(verb in haystack for verb in ("get", "list", "read")):
        return "kubernetes_secret_read"
    if "namespace" in haystack and "delete" in haystack:
        return "namespace_delete"
    if "helm" in lowered_tool and "uninstall" in haystack:
        return "helm_uninstall"
    if "restart" in haystack or "rollout_restart" in haystack:
        return "kubernetes_restart"
    if "patch" in haystack:
        return "kubernetes_patch"
    if "helm" in lowered_tool and "upgrade" in haystack:
        return "helm_upgrade"
    if "helm" in lowered_tool and "rollback" in haystack:
        return "helm_rollback"
    if "memory.write" in lowered_tool:
        return "memory_write"
    if "artifact" in lowered_tool and "write" in haystack:
        return "artifact_write"
    return "read_only"


def expected_impact(action: str, request: ToolExecutionRequest) -> str:
    target = request.namespace or request.environment
    impacts = {
        "kubernetes_restart": f"May restart workload pods in {target}.",
        "kubernetes_patch": f"May change Kubernetes resource state in {target}.",
        "helm_upgrade": f"May alter Helm-managed release state in {target}.",
        "helm_rollback": f"May roll a Helm release back in {target}.",
        "memory_write": "May persist new operational memory for future runs.",
        "artifact_write": "May create or update a durable artifact.",
        "read_only": "Read-only inspection; no intended state change.",
    }
    return impacts.get(action, f"Policy classified this as {action}.")


def rollback_note(action: str) -> str:
    rollback_notes = {
        "kubernetes_restart": "Verify pod readiness; rollback is normally not applicable.",
        "kubernetes_patch": "Requires pre-change manifest or patch reversal plan.",
        "helm_upgrade": "Requires previous revision and rollback validation plan.",
        "helm_rollback": "Requires target revision and post-rollback validation plan.",
    }
    return rollback_notes.get(action, "No rollback metadata required for this action.")


def _string_set(value: Any, default: set[str]) -> set[str]:
    if not value:
        return set(default)
    return {str(item) for item in value}


def _is_mutation_action(action: str) -> bool:
    return action != "read_only"
