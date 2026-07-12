import asyncio

import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest
from backend.app.tools.env_agents import (
    EnvAgentDataIngestionTool,
    EnvAgentHelmManagerTool,
    EnvAgentK8sInspectorTool,
    EnvAgentObservabilityTool,
    redact_env_payload,
)
from backend.app.tools.registry import default_tool_registry
from backend.tests.test_phase1_app import build_test_client


def _request(tool_name: str, *, namespace: str = "bosgenesis", arguments: dict | None = None) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        run_id="env_run_1",
        step_id="inspect",
        tool_name="env.k8s_inspector",
        workflow_type="env_agent",
        environment="kubernetes",
        namespace=namespace,
        user_id="usr_1",
        arguments={"tool_name": tool_name, "arguments": arguments or {}},
    )


def test_env_k8s_inspector_pod_health_normalizes_evidence_and_redacts() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = request.url.query.decode()
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "kind": "Pod",
                        "metadata": {"name": "api-0", "namespace": "bosgenesis"},
                        "status": {"phase": "Running", "containerStatuses": [{"restartCount": 3}]},
                    }
                ],
                "token": "raw-token",
            },
        )

    tool = EnvAgentK8sInspectorTool(
        Settings(k8s_inspector_agent_mcp_url="http://k8s-inspector.local"),
        transport=httpx.MockTransport(handler),
    )
    result, duration_ms = asyncio.run(tool.execute(_request("pod_health")))

    assert duration_ms >= 0
    assert captured["path"] == "/pods"
    assert "namespace=bosgenesis" in captured["query"]
    assert "actor=esda" in captured["query"]
    assert result.status == "success"
    evidence = result.output["evidence"]
    assert evidence["workflow_type"] == "env_agent"
    assert evidence["tool_name"] == "env.k8s_inspector"
    assert evidence["source_type"] == "k8s_inspector"
    assert evidence["action"] == "pod_health"
    assert evidence["resource_kind"] == "pod"
    assert evidence["namespace"] == "bosgenesis"
    assert evidence["risk_level"] == "low"
    assert evidence["confidence"] > 0.8
    assert evidence["observation_redacted"]["token"] == "***"


def test_env_k8s_logs_require_target_and_truncate_sensitive_output() -> None:
    missing_tool = EnvAgentK8sInspectorTool(
        Settings(k8s_inspector_agent_mcp_url="http://k8s-inspector.local")
    )
    missing, _ = asyncio.run(missing_tool.execute(_request("logs")))
    assert missing.status == "blocked"
    assert missing.error["code"] == "ENV_AGENT_TOOL_ARGUMENT_MISSING"

    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = request.url.query.decode()
        return httpx.Response(
            200,
            json={"logs": "password=unsafe token=unsafe " + ("x" * 120)},
        )

    tool = EnvAgentK8sInspectorTool(
        Settings(
            k8s_inspector_agent_mcp_url="http://k8s-inspector.local",
            env_agent_max_observation_chars=48,
            env_agent_log_tail_lines=25,
        ),
        transport=httpx.MockTransport(handler),
    )
    result, _ = asyncio.run(tool.execute(_request("logs", arguments={"pod_name": "api-0"})))

    assert result.status == "success"
    assert captured["path"] == "/pods/api-0/logs"
    assert "tail_lines=25" in captured["query"]
    evidence = result.output["evidence"]
    assert evidence["risk_level"] == "medium"
    assert evidence["resource_name"] == "api-0"
    logs = evidence["observation_redacted"]["logs"]
    assert "password=***" in logs
    assert "token=***" in logs
    assert "<truncated" in logs


def test_env_helm_manager_status_and_rollback_candidate_mapping() -> None:
    captured = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, request.url.query.decode()))
        return httpx.Response(200, json={"release": "signoz", "status": "deployed", "history": [{"revision": 1}]})

    tool = EnvAgentHelmManagerTool(
        Settings(helm_manager_agent_mcp_url="http://helm-manager.local"),
        transport=httpx.MockTransport(handler),
    )
    status, _ = asyncio.run(
        tool.execute(
            _request(
                "helm_release_status",
                namespace="agent-testing",
                arguments={"release_name": "signoz"},
            )
        )
    )
    rollback, _ = asyncio.run(
        tool.execute(
            _request(
                "helm_rollback_candidates",
                namespace="agent-testing",
                arguments={"release_name": "signoz"},
            )
        )
    )

    assert captured[0][0] == "/releases/signoz/status"
    assert "namespace=agent-testing" in captured[0][1]
    assert captured[1][0] == "/releases/signoz/history"
    assert status.status == "success"
    assert rollback.status == "success"
    assert status.output["evidence"]["source_type"] == "helm_manager"
    assert rollback.output["evidence"]["action"] == "helm_rollback_candidates"
    assert rollback.output["evidence"]["resource_kind"] == "helm_release"


def test_env_optional_lookup_hooks_normalize_when_configured() -> None:
    captured = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.path)
        return httpx.Response(200, json={"items": [{"name": "prior-snapshot"}]})

    ingestion = EnvAgentDataIngestionTool(
        Settings(env_agent_data_ingestion_url="http://ingestion.local"),
        transport=httpx.MockTransport(handler),
    )
    observability = EnvAgentObservabilityTool(
        Settings(env_agent_observability_url="http://observability.local"),
        transport=httpx.MockTransport(handler),
    )

    ingestion_result, _ = asyncio.run(ingestion.execute(_request("namespace_snapshot")))
    observability_result, _ = asyncio.run(observability.execute(_request("recent_traces")))

    assert captured == ["/snapshots/namespace/bosgenesis", "/traces/recent"]
    assert ingestion_result.status == "success"
    assert ingestion_result.output["evidence"]["source_type"] == "data_ingestion"
    assert observability_result.status == "success"
    assert observability_result.output["evidence"]["source_type"] == "observability"


def test_env_redaction_handles_secret_shapes_and_large_lists() -> None:
    payload = {
        "kind": "Secret",
        "data": {"password": "abc"},
        "items": [{"token": "raw"}, {"safe": "ok"}, {"safe": "truncated"}],
        "log": "Authorization: Bearer abc api_key=xyz",
    }

    redacted = redact_env_payload(payload, max_string_chars=80, max_list_items=2)

    assert redacted["data"] == {"password": "***"}
    assert redacted["items"] == [{"token": "***"}, {"safe": "ok"}, {"truncated_items": 1}]
    assert "Authorization=***" in redacted["log"]
    assert "Bearer abc" not in redacted["log"]
    assert "api_key=***" in redacted["log"]


def test_env_tools_are_registered_and_exposed_on_app_state(tmp_path, monkeypatch) -> None:
    registry = default_tool_registry()
    assert registry.is_allowed(tool_name="env.k8s_inspector", workflow_type="env_agent")
    assert registry.is_allowed(tool_name="env.helm_manager", workflow_type="env_agent")
    assert registry.is_allowed(tool_name="env.data_ingestion", workflow_type="env_agent")
    assert registry.is_allowed(tool_name="env.observability", workflow_type="env_agent")
    assert not registry.is_allowed(tool_name="env.k8s_inspector", workflow_type="release_note_creation")

    with build_test_client(tmp_path, monkeypatch) as client:
        assert client.app.state.env_k8s_inspector.name == "env.k8s_inspector"
        assert client.app.state.env_helm_manager.name == "env.helm_manager"
        assert client.app.state.env_data_ingestion.name == "env.data_ingestion"
        assert client.app.state.env_observability.name == "env.observability"
