from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.config import Settings


ENV_AGENT_ROUTE = "/env-agent"
ENV_AGENT_WORKFLOW_TYPE = "env_agent"


@dataclass(frozen=True)
class EnvAgentToolRisk:
    action: str
    risk_level: str
    approval_required: bool
    description: str


ENV_AGENT_ENVIRONMENT_SCOPES: list[dict[str, str]] = [
    {
        "scope_id": "kubernetes_namespace",
        "label": "Kubernetes Namespace",
        "description": "Namespace-scoped Kubernetes diagnostics and approved remediation.",
    },
    {
        "scope_id": "helm_release",
        "label": "Helm Release",
        "description": "Helm release status, history, values summary, rollback candidate lookup, and approved rollback.",
    },
    {
        "scope_id": "workload",
        "label": "Workload",
        "description": "Pod, Deployment, StatefulSet, DaemonSet, Job, CronJob, Service, Ingress, ConfigMap, PVC, and event inspection.",
    },
]


ENV_AGENT_RESOURCE_KINDS: list[str] = [
    "namespace",
    "pod",
    "deployment",
    "statefulset",
    "daemonset",
    "job",
    "cronjob",
    "service",
    "ingress",
    "configmap",
    "pvc",
    "event",
    "helm_release",
]


ENV_AGENT_USER_ROLES: list[dict[str, str]] = [
    {"role": "admin", "description": "Can run diagnostics, submit approvals, and operate approved remediation flows."},
    {"role": "operator", "description": "Can run diagnostics and request approval-gated remediation."},
    {"role": "approver", "description": "Can approve scoped remediation after reviewing evidence."},
]


ENV_AGENT_REMEDIATION_MODES: list[dict[str, str]] = [
    {
        "mode": "diagnostic_only",
        "label": "Diagnostic only",
        "description": "Read-only inspection and evidence-backed explanation. No mutation is proposed or executed.",
    },
    {
        "mode": "propose_only",
        "label": "Propose only",
        "description": "Builds a remediation plan with rollback and verification, but does not execute.",
    },
    {
        "mode": "approval_gated_remediation",
        "label": "Approval-gated remediation",
        "description": "Executes only typed, scoped, approved MCP tool actions and verifies the result.",
    },
]


ENV_AGENT_TOOL_RISKS: list[EnvAgentToolRisk] = [
    EnvAgentToolRisk("list", "low", False, "List namespaced resources and summarize status."),
    EnvAgentToolRisk("describe", "low", False, "Describe namespaced resources without secret material."),
    EnvAgentToolRisk("events", "low", False, "Read namespace or resource events."),
    EnvAgentToolRisk("logs", "medium", False, "Read bounded, redacted logs with volume limits."),
    EnvAgentToolRisk("restart", "high", True, "Trigger a rollout restart for an explicitly selected workload."),
    EnvAgentToolRisk("scale", "high", True, "Scale an explicitly selected workload within approved bounds."),
    EnvAgentToolRisk("patch", "high", True, "Apply a typed patch to an explicitly selected resource."),
    EnvAgentToolRisk("rollback", "high", True, "Rollback a Helm release to an approved revision."),
    EnvAgentToolRisk("install", "high", True, "Install or upgrade a Helm release inside an approved namespace."),
    EnvAgentToolRisk("uninstall", "high", True, "Uninstall an explicitly selected Helm release inside an approved namespace."),
    EnvAgentToolRisk("apply", "high", True, "Apply an explicit manifest to an approved namespace."),
    EnvAgentToolRisk("delete", "high", True, "Delete an explicitly selected namespaced resource. Namespace and cluster-wide deletes are blocked."),
]


ENV_AGENT_POLICY_STOP_CONDITIONS: list[dict[str, str]] = [
    {"condition": "secret_material_requested", "action": "block", "description": "Do not read, display, or persist Secret data, tokens, passwords, credentials, or kubeconfigs."},
    {"condition": "cluster_wide_change", "action": "block", "description": "Block cluster-wide, non-namespaced, or cross-tenant mutation requests in V1."},
    {"condition": "destructive_action", "action": "block", "description": "Block namespace deletion, wipe, raw shell, and other unbounded destructive requests."},
    {"condition": "ambiguous_target", "action": "clarify", "description": "Ask for namespace, resource kind, and resource name before planning tool calls."},
    {"condition": "missing_namespace", "action": "clarify", "description": "Require an allowed namespace for all Kubernetes and Helm tool calls."},
    {"condition": "low_confidence", "action": "pause", "description": "Stop before remediation when diagnosis confidence is below policy threshold."},
]


ENV_AGENT_LOGGING_REQUIREMENTS: list[dict[str, Any]] = [
    {
        "event_type": "run_event",
        "store": "postgresql",
        "required_fields": ["run_id", "workflow_type", "user_id", "namespace", "status", "timestamp"],
    },
    {
        "event_type": "tool_call",
        "store": "postgresql",
        "required_fields": ["run_id", "tool_name", "risk_level", "arguments_redacted", "duration_ms", "status"],
    },
    {
        "event_type": "approval",
        "store": "postgresql",
        "required_fields": ["run_id", "approval_id", "operator", "scope", "expiry", "decision", "timestamp"],
    },
    {
        "event_type": "llm_review",
        "store": "postgresql",
        "required_fields": ["run_id", "model_profile", "prompt_version", "prompt_hash", "safe_reasoning_summary"],
    },
]


ENV_AGENT_ACTIVITY_VISIBILITY: dict[str, Any] = {
    "enabled": True,
    "workflow_type": ENV_AGENT_WORKFLOW_TYPE,
    "node_fields": [
        "run_id",
        "session_name",
        "namespace",
        "mode",
        "tools_used",
        "risk_level",
        "approval_state",
        "status",
        "updated_at",
    ],
    "chat_context": [
        "user_prompt",
        "safe_reasoning_summaries",
        "redacted_tool_observations",
        "approval_events",
        "final_report",
    ],
}


def env_agent_namespace_list(settings: Settings) -> list[str]:
    return [item.strip() for item in settings.env_agent_allowed_namespaces.split(",") if item.strip()]


def env_agent_contract(settings: Settings) -> dict[str, Any]:
    namespaces = env_agent_namespace_list(settings)
    default_namespace = (
        settings.env_agent_default_namespace
        if settings.env_agent_default_namespace in namespaces
        else (namespaces[0] if namespaces else "")
    )
    return {
        "route": ENV_AGENT_ROUTE,
        "workflow_type": ENV_AGENT_WORKFLOW_TYPE,
        "environment_scopes": ENV_AGENT_ENVIRONMENT_SCOPES,
        "namespaces": namespaces,
        "default_namespace": default_namespace,
        "resource_kinds": ENV_AGENT_RESOURCE_KINDS,
        "user_roles": ENV_AGENT_USER_ROLES,
        "remediation_modes": ENV_AGENT_REMEDIATION_MODES,
        "default_mode": settings.env_agent_default_mode,
        "tool_risks": [
            {
                "action": item.action,
                "risk_level": item.risk_level,
                "approval_required": item.approval_required,
                "description": item.description,
            }
            for item in ENV_AGENT_TOOL_RISKS
        ],
        "policy_stop_conditions": ENV_AGENT_POLICY_STOP_CONDITIONS,
        "logging_requirements": ENV_AGENT_LOGGING_REQUIREMENTS,
        "activity_visibility": ENV_AGENT_ACTIVITY_VISIBILITY,
    }

