from backend.app.config import Settings
from backend.app.policy.evaluator import PolicyGuard
from backend.app.tools.contracts import ToolExecutionRequest
from backend.app.tools.registry import default_tool_registry


def build_guard(tmp_path) -> PolicyGuard:
    return PolicyGuard(
        settings=Settings(policy_rules_path=str(tmp_path / "missing-policy.yaml")),
        tool_registry=default_tool_registry(),
    )


def test_policy_allows_registered_read_only_tool(tmp_path) -> None:
    guard = build_guard(tmp_path)
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="rest.get",
        workflow_type="health_check_diagnostic",
        environment="local",
        namespace="bosgenesis",
        user_id="usr_1",
        arguments={"url": "http://localhost:8080/health"},
    )

    decision = guard.evaluate_tool(request, user_roles=["operator"])

    assert decision.decision == "allow"
    assert decision.risk_level == "low"


def test_policy_requires_approval_for_restart(tmp_path) -> None:
    guard = build_guard(tmp_path)
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="k8s.restart",
        workflow_type="k8s_management",
        environment="local",
        namespace="bosgenesis",
        user_id="usr_1",
        arguments={"action": "restart", "resource": "deployment/api"},
    )

    decision = guard.evaluate_tool(request, user_roles=["operator"])

    assert decision.decision == "approval_required"
    assert decision.approval_required is True
    assert "approval_required.kubernetes_restart" in decision.matched_rules


def test_policy_denies_raw_powershell(tmp_path) -> None:
    guard = build_guard(tmp_path)
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="powershell.raw",
        workflow_type="k8s_management",
        environment="local",
        namespace="bosgenesis",
        user_id="usr_1",
        arguments={"command": "Get-Secret"},
    )

    decision = guard.evaluate_tool(request, user_roles=["operator"])

    assert decision.decision == "deny"
    assert any("raw_powershell" in reason for reason in decision.reasons)


def test_policy_denies_namespace_outside_odd(tmp_path) -> None:
    guard = build_guard(tmp_path)
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="k8s.restart",
        workflow_type="k8s_management",
        environment="local",
        namespace="default",
        user_id="usr_1",
        arguments={"action": "restart", "resource": "deployment/api"},
    )

    decision = guard.evaluate_tool(request, user_roles=["operator"])

    assert decision.decision == "deny"
    assert any("Namespace 'default'" in reason for reason in decision.reasons)


def test_policy_allows_mop_generation_read_only_namespace_from_mop_allowlist(tmp_path) -> None:
    guard = PolicyGuard(
        settings=Settings(
            policy_rules_path=str(tmp_path / "missing-policy.yaml"),
            mop_allowed_namespaces="bosgenesis,signoz,agent-testing",
        ),
        tool_registry=default_tool_registry(),
    )
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="mop.k8s_inspector",
        workflow_type="mop_generation",
        environment="kubernetes_generic",
        namespace="signoz",
        user_id="usr_1",
        arguments={"tool_name": "namespace_summary", "arguments": {"namespace": "signoz"}},
    )

    decision = guard.evaluate_tool(request, user_roles=["admin"])

    assert decision.decision == "allow"
    assert not any("Namespace 'signoz'" in reason for reason in decision.reasons)
