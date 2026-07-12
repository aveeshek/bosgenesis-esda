from backend.app.tools.contracts import ToolExecutionResult
from backend.app.tools.env_agents import EnvAgentK8sInspectorTool
from backend.tests.test_phase1_app import build_test_client


async def fake_env_k8s_execute(self, request):
    tool_name = request.arguments.get("tool_name")
    if tool_name == "rollout_restart":
        return (
            ToolExecutionResult(
                status="success",
                output={"evidence": {"summary": "rollout restart submitted"}, "raw": {"ok": True}},
                validation_result={"valid": True, "message": "restart accepted"},
            ),
            12,
        )
    if tool_name == "deployment_status":
        return (
            ToolExecutionResult(
                status="success",
                output={"evidence": {"summary": "deployment ready"}, "raw": {"items": [{"name": "api", "ready": True}]}},
                validation_result={"valid": True, "message": "deployment ready"},
            ),
            9,
        )
    return (
        ToolExecutionResult(
            status="failed",
            error={"code": "unexpected_tool", "message": str(tool_name), "retryable": False},
        ),
        1,
    )


def test_env_agent_phase_f_creates_remediation_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_AGENT_ALLOWED_NAMESPACES", "bosgenesis,agent-testing")
    monkeypatch.setenv("ENV_AGENT_DEFAULT_NAMESPACE", "agent-testing")
    monkeypatch.setenv("K8S_INSPECTOR_AGENT_MCP_URL", "")
    monkeypatch.setenv("MCP_K8S_INSPECTOR_URL", "")
    monkeypatch.setenv("HELM_MANAGER_AGENT_MCP_URL", "")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.post(
            "/api/env-agent/chat",
            json={
                "message": "Please restart deployment api in this namespace.",
                "namespace": "agent-testing",
                "mode": "approval_gated_remediation",
                "scope": "kubernetes_namespace",
                "model_profile": "azure_configured",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "proposal_ready"
        events = payload["snapshot"]["events"]
        approval_event = next(event for event in events if event["event_type"] == "remediation_approval_requested")
        approval = approval_event["payload"]["approval"]
        assert approval["status"] == "pending"
        assert approval["run_id"] == payload["run_id"]
        assert approval["tool_name"] == "env.k8s_rollout_restart"
        assert approval["request"]["arguments"]["arguments"]["resource_name"] == "api"

        approvals = client.get("/api/approvals?status=pending")
        assert approvals.status_code == 200
        assert approval["approval_id"] in {item["approval_id"] for item in approvals.json()["approvals"]}


def test_env_agent_phase_f_executes_approved_typed_remediation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_AGENT_ALLOWED_NAMESPACES", "bosgenesis,agent-testing")
    monkeypatch.setenv("ENV_AGENT_DEFAULT_NAMESPACE", "agent-testing")
    monkeypatch.setenv("K8S_INSPECTOR_AGENT_MCP_URL", "http://env-k8s.test")
    monkeypatch.setattr(EnvAgentK8sInspectorTool, "execute", fake_env_k8s_execute)

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.post(
            "/api/env-agent/chat",
            json={
                "message": "Please restart deployment api in this namespace.",
                "namespace": "agent-testing",
                "mode": "approval_gated_remediation",
                "scope": "kubernetes_namespace",
                "model_profile": "azure_configured",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        approval_event = next(event for event in payload["snapshot"]["events"] if event["event_type"] == "remediation_approval_requested")
        approval_id = approval_event["payload"]["approval"]["approval_id"]

        approve = client.post(f"/api/approvals/{approval_id}/approve", json={"notes": "Approved for test."})
        assert approve.status_code == 200
        assert approve.json()["approval"]["status"] == "approved"

        execute = client.post(
            "/api/env-agent/remediation/execute",
            json={"run_id": payload["run_id"], "approval_id": approval_id, "model_profile": "azure_configured"},
        )
        assert execute.status_code == 200
        result = execute.json()
        assert result["status"] == "completed"
        assert result["execution"]["status"] == "success"
        assert result["verification"]["status"] == "success"
        event_types = [event["event_type"] for event in result["snapshot"]["events"]]
        assert "remediation_approval_confirmed" in event_types
        assert "remediation_action_executed" in event_types
        assert "remediation_verified" in event_types
        assert "run_completed" in event_types
        tool_names = [call["tool_name"] for call in result["snapshot"].get("tool_calls") or []]
        assert "env.k8s_rollout_restart" in tool_names
        assert "env.k8s_rollout_restart.verify" in tool_names

