from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    category: str
    risk_level: str
    enabled: bool
    allowed_roles: tuple[str, ...]
    allowed_environments: tuple[str, ...]
    allowed_workflow_types: tuple[str, ...]
    timeout_seconds: int
    description: str = ""

    def allows(self, *, workflow_type: str, environment: str = "local") -> bool:
        if not self.enabled:
            return False
        workflow_allowed = "*" in self.allowed_workflow_types or workflow_type in self.allowed_workflow_types
        environment_allowed = "*" in self.allowed_environments or environment in self.allowed_environments
        return workflow_allowed and environment_allowed


@dataclass
class ToolRegistry:
    definitions: dict[str, ToolDefinition] = field(default_factory=dict)

    def get(self, tool_name: str) -> ToolDefinition | None:
        return self.definitions.get(tool_name)

    def list_enabled(self) -> list[ToolDefinition]:
        return [definition for definition in self.definitions.values() if definition.enabled]

    def is_allowed(self, *, tool_name: str, workflow_type: str, environment: str = "local") -> bool:
        definition = self.get(tool_name)
        return bool(definition and definition.allows(workflow_type=workflow_type, environment=environment))


def default_tool_registry() -> ToolRegistry:
    return ToolRegistry(
        definitions={
            "rest.get": ToolDefinition(
                name="rest.get",
                category="rest",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator"),
                allowed_environments=("*",),
                allowed_workflow_types=("health_check_diagnostic", "k8s_management", "helm_management", "mop_execution"),
                timeout_seconds=20,
                description="Read-only HTTP GET against allowlisted hosts.",
            ),
            "powershell.ps_http_get": ToolDefinition(
                name="powershell.ps_http_get",
                category="powershell",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator"),
                allowed_environments=("*",),
                allowed_workflow_types=("health_check_diagnostic", "k8s_management", "helm_management", "mop_execution"),
                timeout_seconds=30,
                description="Template-only PowerShell HTTP GET contract with no raw command field.",
            ),
            "mcp.k8s_inspector": ToolDefinition(
                name="mcp.k8s_inspector",
                category="mcp",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator"),
                allowed_environments=("*",),
                allowed_workflow_types=("health_check_diagnostic", "k8s_management", "helm_management", "mop_execution"),
                timeout_seconds=30,
                description="Read-only Kubernetes inspector MCP routes.",
            ),
            "mop.k8s_inspector": ToolDefinition(
                name="mop.k8s_inspector",
                category="mcp",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator"),
                allowed_environments=("*",),
                allowed_workflow_types=("mop_generation",),
                timeout_seconds=60,
                description="Read-only Kubernetes evidence collection for MoP generation.",
            ),
            "mop.helm_manager": ToolDefinition(
                name="mop.helm_manager",
                category="helm_read",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator"),
                allowed_environments=("*",),
                allowed_workflow_types=("mop_generation",),
                timeout_seconds=60,
                description="Read-only Helm evidence collection for MoP generation.",
            ),
            "mop.creation_agent": ToolDefinition(
                name="mop.creation_agent",
                category="artifact",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator"),
                allowed_environments=("*",),
                allowed_workflow_types=("mop_generation",),
                timeout_seconds=180,
                description="Read-only MoP creation agent draft/evidence generation.",
            ),
            "release_notes.agent_scan": ToolDefinition(
                name="release_notes.agent_scan",
                category="release_notes",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator"),
                allowed_environments=("*",),
                allowed_workflow_types=("release_note_creation",),
                timeout_seconds=120,
                description="Read-only release-note-agent scan for GitHub source evidence.",
            ),
            "k8s.restart": ToolDefinition(
                name="k8s.restart",
                category="kubernetes_restart",
                risk_level="high",
                enabled=True,
                allowed_roles=("admin", "operator", "approver"),
                allowed_environments=("local", "dev", "stage"),
                allowed_workflow_types=("k8s_management", "mop_execution"),
                timeout_seconds=60,
                description="Approval-gated Kubernetes workload restart placeholder.",
            ),
            "k8s.patch": ToolDefinition(
                name="k8s.patch",
                category="kubernetes_patch",
                risk_level="high",
                enabled=True,
                allowed_roles=("admin", "operator", "approver"),
                allowed_environments=("local", "dev", "stage"),
                allowed_workflow_types=("k8s_management", "mop_execution"),
                timeout_seconds=60,
                description="Approval-gated Kubernetes patch placeholder.",
            ),
            "helm.upgrade": ToolDefinition(
                name="helm.upgrade",
                category="helm_upgrade",
                risk_level="high",
                enabled=True,
                allowed_roles=("admin", "operator", "approver"),
                allowed_environments=("local", "dev", "stage"),
                allowed_workflow_types=("helm_management", "mop_execution"),
                timeout_seconds=120,
                description="Approval-gated Helm upgrade placeholder.",
            ),
            "helm.rollback": ToolDefinition(
                name="helm.rollback",
                category="helm_rollback",
                risk_level="high",
                enabled=True,
                allowed_roles=("admin", "operator", "approver"),
                allowed_environments=("local", "dev", "stage"),
                allowed_workflow_types=("helm_management", "mop_execution"),
                timeout_seconds=120,
                description="Approval-gated Helm rollback placeholder.",
            ),
            "helm.status": ToolDefinition(
                name="helm.status",
                category="helm_read",
                risk_level="low",
                enabled=True,
                allowed_roles=("admin", "operator", "approver"),
                allowed_environments=("local", "dev", "stage"),
                allowed_workflow_types=("helm_management", "mop_execution"),
                timeout_seconds=30,
                description="Read-only Helm status placeholder for L4 eligibility checks.",
            ),
        }
    )