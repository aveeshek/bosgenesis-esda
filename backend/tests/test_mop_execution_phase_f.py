from __future__ import annotations

from io import BytesIO
from typing import Any
import zipfile

from backend.app.tools.mop_agents import redact_sensitive
from backend.app.tools.mop_execution_agent import MopExecutionAgentResponse
from backend.tests.test_mop_execution_phase_e import _bundle_bytes, _seed_bundle_run
from backend.tests.test_phase1_app import build_test_client


_FULL_CAPABILITIES = {
    "capabilities": [
        "bundle_validation",
        "dry_run",
        "approval",
        "mutation",
        "validation reports",
        "rollback",
        "cleanup",
    ]
}


def _large_bundle_bytes() -> bytes:
    source = BytesIO(_bundle_bytes())
    target = BytesIO()
    with zipfile.ZipFile(source) as existing, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in existing.infolist():
            if info.is_dir():
                continue
            archive.writestr(info.filename, existing.read(info.filename))
        large = zipfile.ZipInfo("deployment-artifacts/kubernetes-manifests/raw/large-configmap.yaml")
        large.compress_type = zipfile.ZIP_STORED
        archive.writestr(
            large,
            "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: large-context\n  namespace: agent-testing\ndata:\n  payload: "
            + "x" * 800_000,
        )
    return target.getvalue()


class FakeMopExecutionAgent:
    def __init__(self, *, capabilities: dict[str, Any] | None = None, validation: dict[str, Any] | None = None) -> None:
        self.capabilities_payload = capabilities or _FULL_CAPABILITIES
        self.validation_payload = validation or {"ok": True, "data": {"valid": True, "bundle": {"validation_status": "valid"}}, "bundle_id": "bundle_agent_123"}
        self.calls: list[str] = []
        self.registration_request: dict[str, Any] | None = None
        self.validation_request: dict[str, Any] | None = None

    async def health(self) -> MopExecutionAgentResponse:
        self.calls.append("health")
        return self._response("GET", "http://agent/healthz", {"status": "ok"})

    async def readiness(self) -> MopExecutionAgentResponse:
        self.calls.append("readiness")
        return self._response("GET", "http://agent/readyz", {"status": "ready"})

    async def capabilities(self) -> MopExecutionAgentResponse:
        self.calls.append("capabilities")
        return self._response("GET", "http://agent/v1/capabilities", self.capabilities_payload)

    async def register_bundle(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("register_bundle")
        self.registration_request = payload
        return self._response("POST", "http://agent/v1/artifact-bundles", {"ok": True, "bundle_id": "bundle_agent_123", "data": {"bundle_id": "bundle_agent_123"}}, request=payload)

    async def validate_bundle(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("validate_bundle")
        self.validation_request = payload
        return self._response("POST", "http://agent/v1/artifact-bundles/bundle_agent_123/validate", self.validation_payload, request=payload)

    @staticmethod
    def _response(method: str, url: str, payload: dict[str, Any], request: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        return MopExecutionAgentResponse(
            method=method,
            url=url,
            status_code=200,
            payload=payload,
            redacted_request=redact_sensitive({"json": request or {}, "params": {}}),
            redacted_response=redact_sensitive(payload),
        )


def test_mop_execution_phase_f_validates_bundle_and_persists_bundle_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        artifact = _seed_bundle_run(client, user_id, _bundle_bytes())
        fake_agent = FakeMopExecutionAgent()
        client.app.state.mop_execution_agent = fake_agent

        response = client.post(
            "/api/mop-execution/validate",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
                "correlation_id": "agent-ai-agent-testing-execution-test",
                "execution_mode": "dry_run_then_approval",
                "model_profile": "azure_gpt5_pro",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "validated"
        assert result["bundle_id"] == "bundle_agent_123"
        assert fake_agent.calls == ["health", "readiness", "capabilities", "register_bundle", "validate_bundle"]
        assert fake_agent.registration_request is not None
        assert fake_agent.registration_request["source"] == {
            "type": "object_store",
            "value": "https://raw.githubusercontent.com/aveeshek/bosgenesis-artifacts/main/260630_120000_mop_signoz/mop-bundle.zip",
        }
        assert fake_agent.validation_request is not None
        assert fake_agent.validation_request["bundle_id"] == "bundle_agent_123"
        assert fake_agent.validation_request["mutation_allowed"] is False

        event_types = [event["event_type"] for event in result["events"]]
        assert "agent_health_checked" in event_types
        assert "agent_readiness_checked" in event_types
        assert "agent_capabilities_checked" in event_types
        assert "bundle_validated" in event_types
        metadata = client.app.state.mop_execution_store.execution_metadata(result["run_id"])
        assert metadata["bundle_id"] == "bundle_agent_123"


def test_mop_execution_phase_f_uses_artifact_repo_reference_for_large_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        artifact = _seed_bundle_run(client, user_id, _large_bundle_bytes())
        fake_agent = FakeMopExecutionAgent()
        client.app.state.mop_execution_agent = fake_agent

        response = client.post(
            "/api/mop-execution/validate",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert fake_agent.registration_request is not None
        source = fake_agent.registration_request["source"]
        assert source["type"] == "object_store"
        assert source["value"].endswith("/main/260630_120000_mop_signoz/mop-bundle.zip")
        assert "archive_base64" not in source
        bundle_event = next(event for event in result["events"] if event["event_type"] == "bundle_validated")
        request_json = bundle_event["payload"]["bundle_validation"]["agent_response"]["request"]["json"]
        assert request_json["bundle_id"] == "bundle_agent_123"


def test_mop_execution_phase_f_stops_when_required_capability_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        artifact = _seed_bundle_run(client, user_id, _bundle_bytes())
        fake_agent = FakeMopExecutionAgent(capabilities={"capabilities": ["bundle_validation", "dry_run", "approval", "mutation", "reports", "rollback"]})
        client.app.state.mop_execution_agent = fake_agent

        response = client.post(
            "/api/mop-execution/validate",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert "cleanup" in result["missing_capabilities"]
        assert fake_agent.calls == ["health", "readiness", "capabilities"]
        assert any(event["event_type"] == "run_failed" for event in result["events"])


def test_mop_execution_phase_f_stops_when_agent_rejects_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        artifact = _seed_bundle_run(client, user_id, _bundle_bytes())
        fake_agent = FakeMopExecutionAgent(validation={"ok": True, "data": {"valid": False, "bundle": {"validation_status": "invalid", "validation_error": "machine plan schema unsupported"}}, "bundle_id": "bundle_agent_123"})
        client.app.state.mop_execution_agent = fake_agent

        response = client.post(
            "/api/mop-execution/validate",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["failures"] == ["machine plan schema unsupported"]
        event_types = [event["event_type"] for event in result["events"]]
        assert "bundle_validated" in event_types
        assert "run_failed" in event_types