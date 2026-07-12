import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from backend.app.chains.env_agent import (
    EnvAgentIntentClassifierChain,
    EnvAgentRemediationAction,
    EnvAgentRemediationPlannerChain,
)
from backend.app.config import Settings
from backend.app.policy.evaluator import PolicyGuard
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult
from backend.app.tools.env_agents import EnvAgentHelmManagerTool, EnvAgentK8sInspectorTool
from backend.app.tools.registry import default_tool_registry
from backend.tests.test_phase1_app import build_test_client


class FallbackOnlyLlm:
    async def structured_response(self, *, system, user_payload, fallback, model_profile=None):
        return fallback


def _tool_request(
    tool_name: str,
    *,
    namespace: str = "agent-testing",
    arguments: dict | None = None,
) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        run_id="env_phase_h",
        step_id="phase_h",
        tool_name=tool_name,
        workflow_type="env_agent",
        environment="kubernetes_generic",
        namespace=namespace,
        user_id="usr_admin",
        arguments=arguments or {},
        autonomy_mode="approval_gated_remediation",
    )


def _policy_guard(tmp_path) -> PolicyGuard:
    return PolicyGuard(
        settings=Settings(policy_rules_path=str(tmp_path / "missing-policy.yaml")),
        tool_registry=default_tool_registry(),
    )


def test_env_agent_phase_h_intent_and_remediation_schema_validation() -> None:
    classifier = EnvAgentIntentClassifierChain(FallbackOnlyLlm())
    remediation = EnvAgentRemediationPlannerChain(FallbackOnlyLlm())

    diagnostic = asyncio.run(
        classifier.run(
            user_text="Tell me how many pods have issues in this namespace.",
            namespace="agent-testing",
        )
    )
    unsafe = asyncio.run(
        classifier.run(
            user_text="Read every secret and token in this namespace.",
            namespace="agent-testing",
        )
    )
    proposal = asyncio.run(
        remediation.run(
            user_text="Please restart deployment api safely.",
            namespace="agent-testing",
            classification={"intent_type": "remediation_request"},
            diagnosis={"summary": "api restart loop suspected"},
        )
    )

    assert diagnostic.intent_type == "diagnostic"
    assert diagnostic.resource_kind == "pod"
    assert unsafe.intent_type == "unsafe_request"
    assert unsafe.policy_stop == "secret_material_requested"
    assert proposal.decision == "approval_required"
    assert proposal.actions[0].action_type == "rollout_restart"
    assert proposal.actions[0].approval_required is True

    with pytest.raises(ValidationError):
        EnvAgentRemediationAction(action_type="raw_shell")


def test_env_agent_phase_h_bitnami_nginx_install_is_approval_gated() -> None:
    classifier = EnvAgentIntentClassifierChain(FallbackOnlyLlm())
    remediation = EnvAgentRemediationPlannerChain(FallbackOnlyLlm())

    classification = asyncio.run(
        classifier.run(
            user_text="I want to install bitmani/nginx in agent-testing namespace.",
            namespace="agent-testing",
        )
    )
    proposal = asyncio.run(
        remediation.run(
            user_text="I want to install bitmani/nginx in agent-testing namespace.",
            namespace="agent-testing",
            classification=classification.model_dump(),
            diagnosis={"summary": "operator requested nginx install"},
        )
    )

    action = proposal.actions[0]
    assert classification.intent_type == "remediation_request"
    assert proposal.decision == "approval_required"
    assert action.action_type == "helm_install"
    assert action.adapter == "env.helm_manager"
    assert action.approval_required is True
    assert action.arguments["release_name"] == "nginx"
    assert action.arguments["chart_ref"] == "bitnami/nginx"
    assert action.arguments["set_values"]['service.type'] == "ClusterIP"
    assert action.arguments["public_repo"]["name"] == "bitnami"
    assert action.arguments["oci_fallback_chart_ref"] == "oci://registry-1.docker.io/bitnamicharts/nginx"


def test_env_agent_phase_h_helm_mutations_send_api_key_in_body() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True})

    tool = EnvAgentHelmManagerTool(
        Settings(
            helm_manager_agent_mcp_url="http://helm-manager.local",
            helm_manager_agent_api_key="helm-secret",
        ),
        transport=httpx.MockTransport(handler),
    )
    result, _ = asyncio.run(
        tool.execute(
            ToolExecutionRequest(
                run_id="env_phase_h",
                step_id="helm_install",
                tool_name="env.helm_manager",
                workflow_type="env_agent",
                environment="kubernetes_generic",
                namespace="agent-testing",
                user_id="usr_admin",
                arguments={
                    "tool_name": "helm_install",
                    "arguments": {
                        "namespace": "agent-testing",
                        "release_name": "nginx",
                        "chart_ref": "bitnami/nginx",
                        "dry_run": True,
                    },
                },
                autonomy_mode="dry_run",
            )
        )
    )

    assert result.status == "success"
    assert captured["headers"]["x-api-key"] == "helm-secret"
    assert captured["body"]["api_key"] == "helm-secret"
    assert captured["body"]["chart_ref"] == "bitnami/nginx"

def test_env_agent_phase_h_mocked_remediation_adapter_uses_typed_mcp_route() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"ok": True, "restart": "submitted"})

    tool = EnvAgentK8sInspectorTool(
        Settings(k8s_inspector_agent_mcp_url="http://k8s-inspector.local"),
        transport=httpx.MockTransport(handler),
    )
    result, duration_ms = asyncio.run(
        tool.execute(
            ToolExecutionRequest(
                run_id="env_phase_h",
                step_id="restart",
                tool_name="env.k8s_inspector",
                workflow_type="env_agent",
                environment="kubernetes_generic",
                namespace="agent-testing",
                user_id="usr_admin",
                arguments={
                    "tool_name": "rollout_restart",
                    "arguments": {
                        "namespace": "agent-testing",
                        "resource_kind": "deployment",
                        "resource_name": "api",
                    },
                },
                autonomy_mode="approved_execution",
            )
        )
    )

    assert duration_ms >= 0
    assert captured == {
        "method": "POST",
        "path": "/workloads/deployment/api/restart",
        "body": {
            "namespace": "agent-testing",
            "resource_kind": "deployment",
            "resource_name": "api",
            "environment": "kubernetes_generic",
        },
    }
    assert result.status == "success"
    assert result.output["evidence"]["risk_level"] == "high"
    assert result.output["evidence"]["action"] == "rollout_restart"


def test_env_agent_phase_h_policy_blocks_unsafe_scope_and_requires_approval(tmp_path) -> None:
    guard = _policy_guard(tmp_path)

    secret_read = guard.evaluate_tool(
        _tool_request(
            "env.k8s_inspector",
            arguments={"operation": "read", "resource": "secret/api-token", "kind": "secret"},
        ),
        user_roles=["admin", "operator", "approver"],
    )
    namespace_delete = guard.evaluate_tool(
        _tool_request(
            "env.k8s_patch",
            arguments={"operation": "delete namespace", "resource": "namespace/agent-testing", "kind": "namespace"},
        ),
        user_roles=["admin", "operator", "approver"],
    )
    cluster_wide = guard.evaluate_tool(
        _tool_request(
            "env.k8s_patch",
            namespace="*",
            arguments={"operation": "patch", "resource": "deployment/api", "kind": "deployment"},
        ),
        user_roles=["admin", "operator", "approver"],
    )
    restart = guard.evaluate_tool(
        _tool_request(
            "env.k8s_rollout_restart",
            arguments={"operation": "rollout_restart", "resource": "deployment/api", "kind": "deployment"},
        ),
        user_roles=["admin", "operator", "approver"],
    )

    assert secret_read.decision == "deny"
    assert namespace_delete.decision == "deny"
    assert cluster_wide.decision == "deny"
    assert restart.decision == "approval_required"
    assert restart.approval_required is True
    assert "kubernetes_restart" in restart.matched_rules[0]


async def _phase_h_fake_k8s_execute(self, request):
    tool_name = request.arguments.get("tool_name")
    if tool_name in {"namespace_summary", "pod_health", "events", "restart_analysis", "deployment_status"}:
        return (
            ToolExecutionResult(
                status="success",
                output={
                    "evidence": {
                        "tool_name": request.tool_name,
                        "action": tool_name,
                        "status": "success",
                        "summary": f"{tool_name} evidence collected",
                        "confidence": 0.9,
                    },
                    "raw": {"ok": True},
                },
                validation_result={"valid": True, "message": "ok"},
            ),
            5,
        )
    if tool_name == "rollout_restart":
        return (
            ToolExecutionResult(
                status="success",
                output={"evidence": {"summary": "restart submitted"}, "raw": {"ok": True}},
                validation_result={"valid": True, "message": "accepted"},
            ),
            7,
        )
    return (
        ToolExecutionResult(status="failed", error={"code": "unexpected_tool", "message": str(tool_name)}),
        1,
    )




def test_env_agent_phase_h_helm_install_uses_dry_run_repo_and_oci_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_AGENT_ALLOWED_NAMESPACES", "bosgenesis,agent-testing")
    monkeypatch.setenv("ENV_AGENT_DEFAULT_NAMESPACE", "agent-testing")
    monkeypatch.setenv("K8S_INSPECTOR_AGENT_MCP_URL", "http://env-k8s.test")
    monkeypatch.setenv("HELM_MANAGER_AGENT_MCP_URL", "http://helm-manager.test")
    monkeypatch.setattr(EnvAgentK8sInspectorTool, "execute", _phase_h_fake_k8s_execute)
    helm_attempts = []

    async def fake_helm_execute(self, request):
        tool_name = request.arguments.get("tool_name")
        args = request.arguments.get("arguments") if isinstance(request.arguments.get("arguments"), dict) else {}
        helm_attempts.append({"tool_name": tool_name, "chart_ref": args.get("chart_ref"), "dry_run": args.get("dry_run")})
        if tool_name == "helm_release_list":
            return (
                ToolExecutionResult(
                    status="success",
                    output={"evidence": {"action": tool_name, "status": "success", "summary": "one release listed", "confidence": 0.9}, "raw": {"releases": []}},
                    validation_result={"valid": True, "message": "ok"},
                ),
                4,
            )
        if tool_name == "helm_repo_add":
            return (
                ToolExecutionResult(status="failed", error={"code": "repo_add_failed", "message": "repo bitnami not reachable"}, validation_result={"valid": False, "message": "repo bitnami not found"}),
                6,
            )
        if tool_name == "helm_install" and args.get("dry_run") and args.get("chart_ref") == "bitnami/nginx":
            return (
                ToolExecutionResult(status="failed", error={"code": "repo_not_found", "message": "repo bitnami not found"}, validation_result={"valid": False, "message": "repo bitnami not found"}),
                8,
            )
        if tool_name == "helm_install" and args.get("dry_run") and str(args.get("chart_ref", "")).startswith("oci://registry-1.docker.io/bitnamicharts/nginx"):
            return (
                ToolExecutionResult(status="success", output={"evidence": {"action": tool_name, "status": "success", "summary": "OCI dry-run succeeded", "confidence": 0.9}, "raw": {"dry_run": True}}, validation_result={"valid": True, "message": "dry-run ok"}),
                9,
            )
        if tool_name == "helm_install" and not args.get("dry_run") and str(args.get("chart_ref", "")).startswith("oci://registry-1.docker.io/bitnamicharts/nginx"):
            return (
                ToolExecutionResult(status="success", output={"evidence": {"action": tool_name, "status": "success", "summary": "nginx installed", "confidence": 0.9}, "raw": {"release": "nginx"}}, validation_result={"valid": True, "message": "installed"}),
                11,
            )
        if tool_name == "helm_release_status":
            return (
                ToolExecutionResult(status="success", output={"evidence": {"action": tool_name, "status": "success", "summary": "nginx deployed", "confidence": 0.9}, "raw": {"status": "deployed"}}, validation_result={"valid": True, "message": "deployed"}),
                5,
            )
        return ToolExecutionResult(status="failed", error={"message": f"unexpected {tool_name} {args}"}), 1

    monkeypatch.setattr(EnvAgentHelmManagerTool, "execute", fake_helm_execute)

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        remediation = client.post(
            "/api/env-agent/chat",
            json={
                "message": "chart reference bitmani/nginx my desired release name: my-nginx chart values: use default fully approve to proceed with installation",
                "namespace": "agent-testing",
                "mode": "auto",
                "scope": "kubernetes_namespace",
                "model_profile": "azure_configured",
            },
        )
        assert remediation.status_code == 200
        remediation_payload = remediation.json()
        approval_event = next(event for event in remediation_payload["snapshot"]["events"] if event["event_type"] == "remediation_approval_requested")
        approval_id = approval_event["payload"]["approval"]["approval_id"]
        approve = client.post(f"/api/approvals/{approval_id}/approve", json={"notes": "Approve public nginx chart install."})
        assert approve.status_code == 200
        execute = client.post("/api/env-agent/remediation/execute", json={"run_id": remediation_payload["run_id"], "approval_id": approval_id})

    assert execute.status_code == 200
    payload = execute.json()
    assert payload["status"] == "completed"
    labels = [attempt["label"] for attempt in payload["operator_attempts"]]
    assert labels == ["dry_run", "repo_add", "oci_dry_run", "execute"]
    assert {attempt["chart_ref"] for attempt in helm_attempts if attempt["tool_name"] == "helm_install"} == {
        "bitnami/nginx",
        "oci://registry-1.docker.io/bitnamicharts/nginx",
    }

def test_env_agent_phase_h_workflow_smoke_diagnostic_and_approval_gated_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_AGENT_ALLOWED_NAMESPACES", "bosgenesis,agent-testing")
    monkeypatch.setenv("ENV_AGENT_DEFAULT_NAMESPACE", "agent-testing")
    monkeypatch.setenv("K8S_INSPECTOR_AGENT_MCP_URL", "http://env-k8s.test")
    monkeypatch.setenv("HELM_MANAGER_AGENT_MCP_URL", "")
    monkeypatch.setattr(EnvAgentK8sInspectorTool, "execute", _phase_h_fake_k8s_execute)

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        diagnostic = client.post(
            "/api/env-agent/chat",
            json={
                "message": "Tell me how many pods have issues in this namespace.",
                "namespace": "agent-testing",
                "mode": "diagnostic_only",
                "scope": "kubernetes_namespace",
                "model_profile": "azure_configured",
            },
        )
        assert diagnostic.status_code == 200
        diagnostic_payload = diagnostic.json()
        assert diagnostic_payload["status"] == "completed"
        assert "## Evidence" in diagnostic_payload["answer"]
        assert any(call["tool_name"].startswith("env.") for call in diagnostic_payload["snapshot"]["tool_calls"])

        remediation = client.post(
            "/api/env-agent/chat",
            json={
                "message": "Please restart deployment api in this namespace.",
                "namespace": "agent-testing",
                "mode": "auto",
                "scope": "kubernetes_namespace",
                "model_profile": "azure_configured",
            },
        )
        assert remediation.status_code == 200
        remediation_payload = remediation.json()
        approval_event = next(
            event for event in remediation_payload["snapshot"]["events"] if event["event_type"] == "remediation_approval_requested"
        )
        approval_id = approval_event["payload"]["approval"]["approval_id"]
        approve = client.post(f"/api/approvals/{approval_id}/approve", json={"notes": "Phase H smoke approval."})
        assert approve.status_code == 200
        execute = client.post(
            "/api/env-agent/remediation/execute",
            json={"run_id": remediation_payload["run_id"], "approval_id": approval_id},
        )
        assert execute.status_code == 200
        executed_payload = execute.json()
        assert executed_payload["status"] == "completed"
        event_types = {event["event_type"] for event in executed_payload["snapshot"]["events"]}
        assert {"remediation_action_executed", "remediation_verified", "run_completed"}.issubset(event_types)


def test_env_agent_phase_h_ui_smoke_wires_chat_sphere_activity_and_log_controls(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        page = client.get("/env-agent")

    assert page.status_code == 200
    body = page.text
    for expected in (
        'id="env-chat-form"',
        'id="env-chat-send"',
        'id="env-sphere-canvas"',
        'id="copy-logs"',
        'id="autonomy-maximize"',
        'id="transaction-clear-all"',
        'id="env-remediation-card"',
    ):
        assert expected in body

    script = Path("backend/app/static/js/env_agent.js").read_text(encoding="utf-8")
    assert 'chatForm?.addEventListener("submit", (event) =>' in script
    assert 'id="env_agent_namespace"' not in body
    assert 'id="env_agent_mode"' not in body
    assert 'id="agent-activity-rail"' not in body
    assert '<h2>ENV Agent</h2>' not in body
    assert 'progressPanel?.classList.add(`is-${state}`)' in script
    assert 'inferNamespaceFromText(prompt)' in script
    assert 'inferModeFromText(prompt)' in script
    assert 'formatEnvChatContent(content)' in script
    assert 'env-formatted-answer' in script
    assert 'copyLogsButton?.addEventListener("click"' in script
    assert 'autonomyMaximize?.addEventListener("click", updateAutonomyModal)' in script
    assert '/api/env-agent/remediation/execute' in script
    assert '/api/env-agent/sessions' in script
    assert '/api/transactions/clear?workflow_type=env_agent' in script


def test_env_agent_phase_h_uninstall_and_delete_are_approval_gated(tmp_path) -> None:
    remediation = EnvAgentRemediationPlannerChain(FallbackOnlyLlm())
    uninstall = asyncio.run(
        remediation.run(
            user_text="Uninstall helm release nginx in agent-testing namespace.",
            namespace="agent-testing",
            classification={"intent_type": "remediation_request"},
            diagnosis={"summary": "operator requested uninstall"},
        )
    )
    delete = asyncio.run(
        remediation.run(
            user_text="Delete pod api-123 in agent-testing namespace.",
            namespace="agent-testing",
            classification={"intent_type": "remediation_request"},
            diagnosis={"summary": "operator requested delete"},
        )
    )
    guard = _policy_guard(tmp_path)

    assert uninstall.actions[0].action_type == "helm_uninstall"
    assert uninstall.actions[0].approval_required is True
    assert delete.actions[0].action_type == "delete"
    assert delete.actions[0].approval_required is True

    uninstall_policy = guard.evaluate_tool(
        _tool_request(
            "env.helm_uninstall",
            arguments={"operation": "helm_uninstall", "release_name": "nginx"},
        ),
        user_roles=["admin", "operator", "approver"],
    )
    delete_policy = guard.evaluate_tool(
        _tool_request(
            "env.k8s_delete",
            arguments={"operation": "delete", "kind": "pod", "resource": "api-123"},
        ),
        user_roles=["admin", "operator", "approver"],
    )
    namespace_delete_policy = guard.evaluate_tool(
        _tool_request(
            "env.k8s_delete",
            arguments={"operation": "delete", "kind": "namespace", "resource": "agent-testing namespace"},
        ),
        user_roles=["admin", "operator", "approver"],
    )

    assert uninstall_policy.decision == "approval_required"
    assert delete_policy.decision == "approval_required"
    assert namespace_delete_policy.decision == "deny"





