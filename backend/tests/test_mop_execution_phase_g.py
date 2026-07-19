from __future__ import annotations

from typing import Any

from backend.app.tools.mop_execution_agent import MopExecutionAgentResponse
from backend.tests.test_mop_execution_phase_e import _bundle_bytes, _seed_bundle_run
from backend.tests.test_mop_execution_phase_f import FakeMopExecutionAgent
from backend.tests.test_phase1_app import build_test_client


class FakeMopExecutionDryRunAgent(FakeMopExecutionAgent):
    def __init__(self, *, states: list[str] | None = None) -> None:
        super().__init__()
        self.states = list(states or ["running", "succeeded"])
        self.create_job_request: dict[str, Any] | None = None
        self.start_job_request: dict[str, Any] | None = None
        self.decision_requested = False

    async def create_job(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("create_job")
        self.create_job_request = payload
        return self._response(
            "POST",
            "http://agent/v1/execution-jobs",
            {"job_id": "dry_run_job_123", "state": "created"},
            request=payload,
        )

    async def start_job(self, job_id: str, payload: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        self.calls.append("start_job")
        self.start_job_request = {"job_id": job_id, **(payload or {})}
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/start",
            {"job_id": job_id, "state": "running", "current_phase": "dry_run"},
            request=payload or {},
        )

    async def get_job(self, job_id: str) -> MopExecutionAgentResponse:
        self.calls.append("get_job")
        state = self.states.pop(0) if self.states else "succeeded"
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}",
            {"job_id": job_id, "state": state, "current_phase": "dry_run"},
        )

    async def get_observations(self, job_id: str, params: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        self.calls.append("get_observations")
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/observations",
            {"items": [{"phase": "dry_run", "message": "server-side dry-run completed without mutation"}]},
            request={"params": params or {}},
        )

    async def get_audit_events(self, job_id: str, params: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        self.calls.append("get_audit_events")
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/audit-events",
            {"items": [{"event": "helm_template_dry_run", "mutation_allowed": False}]},
            request={"params": params or {}},
        )

    async def get_decision_required_context(self, job_id: str) -> MopExecutionAgentResponse:
        self.calls.append("get_decision_required_context")
        self.decision_requested = True
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/decision-required",
            {"job_id": job_id, "reason_code": "NEEDS_OPERATOR_SCOPE", "phase": "dry_run"},
        )


    async def get_dry_run_evidence(self, job_id: str) -> MopExecutionAgentResponse:
        self.calls.append("get_dry_run_evidence")
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/dry-run-evidence",
            {
                "data": {
                    "dry_run_evidence": {
                        "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
                        "command_fingerprint_hash": "authoritative-fingerprint-hash",
                    }
                }
            },
        )
    async def list_reports(self, job_id: str) -> MopExecutionAgentResponse:
        self.calls.append("list_reports")
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/reports",
            {"reports": [{"report_id": "dry_report_123", "report_type": "dry_run", "title": "Dry-run Report"}]},
        )

    async def get_report_metadata(self, job_id: str, report_id: str) -> MopExecutionAgentResponse:
        self.calls.append("get_report_metadata")
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/reports/{report_id}",
            {
                "report_id": report_id,
                "report_type": "dry_run",
                "summary": "Dry-run would create one ConfigMap and one Service.",
                "resource_changes": [{"kind": "ConfigMap", "name": "agent-ai-config", "action": "create"}],
                "policy_gates": [{"gate": "namespace_scope", "status": "passed"}],
                "warnings": ["Human approval required before mutation."],
                "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
            },
        )

    async def generate_report(self, job_id: str, report_type: str) -> MopExecutionAgentResponse:
        self.calls.append("generate_report")
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/reports",
            {"report_id": "dry_report_generated", "report_type": report_type},
            request={"report_type": report_type},
        )


def _validated_execution_run(client, user_id: str, fake_agent: FakeMopExecutionDryRunAgent) -> dict[str, Any]:
    artifact = _seed_bundle_run(client, user_id, _bundle_bytes())
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
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert result["valid"] is True
    assert result["bundle_id"] == "bundle_agent_123"
    return result


def test_mop_execution_phase_g_creates_starts_and_polls_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "3")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        fake_agent = FakeMopExecutionDryRunAgent(states=["running", "succeeded"])
        validation = _validated_execution_run(client, user_id, fake_agent)

        response = client.post(
            "/api/mop-execution/dry-run",
            json={"run_id": validation["run_id"], "bundle_id": validation["bundle_id"]},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "waiting_for_approval"
        assert result["dry_run_job_id"] == "dry_run_job_123"
        assert result["dry_run_succeeded"] is True
        assert result["mutation_controls_enabled"] is False
        assert fake_agent.create_job_request is not None
        assert fake_agent.create_job_request["mutation_allowed"] is False
        assert fake_agent.create_job_request["mode"] == "execute_after_approval"
        assert fake_agent.create_job_request["execution_mode"] == "execute_after_approval"
        assert fake_agent.create_job_request["idempotency_key"]
        assert fake_agent.start_job_request is not None
        assert fake_agent.start_job_request["mutation_allowed"] is False

        event_types = [event["event_type"] for event in result["events"]]
        assert "dry_run_job_created" in event_types
        assert "job_started" in event_types
        assert event_types.count("job_state_polled") >= 2
        assert "observations_received" in event_types
        assert "safe_reasoning_summary" in event_types
        metadata = client.app.state.mop_execution_store.execution_metadata(validation["run_id"])
        assert metadata["dry_run_job_id"] == "dry_run_job_123"
        assert metadata["current_state"] == "dry_run_succeeded"
        assert client.app.state.repository.get_run(validation["run_id"]).status == "waiting_for_approval"


def test_mop_execution_phase_g_pauses_on_decision_required(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "2")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        fake_agent = FakeMopExecutionDryRunAgent(states=["decision_required"])
        validation = _validated_execution_run(client, user_id, fake_agent)

        response = client.post(
            "/api/mop-execution/dry-run",
            json={"run_id": validation["run_id"], "bundle_id": validation["bundle_id"]},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "decision_required"
        assert result["dry_run_succeeded"] is False
        assert result["mutation_controls_enabled"] is False
        assert fake_agent.decision_requested is True
        event_types = [event["event_type"] for event in result["events"]]
        assert "decision_required" in event_types
        assert "run_failed" not in event_types


def test_mop_execution_phase_g_fails_safe_when_dry_run_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "2")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        fake_agent = FakeMopExecutionDryRunAgent(states=["failed_safe"])
        validation = _validated_execution_run(client, user_id, fake_agent)

        response = client.post(
            "/api/mop-execution/dry-run",
            json={"run_id": validation["run_id"], "bundle_id": validation["bundle_id"]},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["status"] == "failed"
        assert result["dry_run_succeeded"] is False
        assert result["mutation_controls_enabled"] is False
        event_types = [event["event_type"] for event in result["events"]]
        assert "run_failed" in event_types
        assert client.app.state.repository.get_run(validation["run_id"]).status == "failed"
