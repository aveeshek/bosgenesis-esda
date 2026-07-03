from __future__ import annotations

from typing import Any

from backend.app.tools.mop_execution_agent import MopExecutionAgentError, MopExecutionAgentResponse
from backend.tests.test_mop_execution_phase_g import FakeMopExecutionDryRunAgent, _validated_execution_run
from backend.tests.test_phase1_app import build_test_client


class FakeMopExecutionApprovalAgent(FakeMopExecutionDryRunAgent):
    def __init__(self, *, accepted: bool = True) -> None:
        super().__init__(states=["succeeded"])
        self.accepted = accepted
        self.approval_request: dict[str, Any] | None = None

    async def submit_approval(self, job_id: str, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("submit_approval")
        self.approval_request = {"job_id": job_id, **payload}
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/approvals",
            {"job_id": job_id, "accepted": self.accepted, "status": "approved" if self.accepted else "rejected"},
            request=payload,
        )


class ActiveStatusMopExecutionApprovalAgent(FakeMopExecutionApprovalAgent):
    async def submit_approval(self, job_id: str, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("submit_approval")
        self.approval_request = {"job_id": job_id, **payload}
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/approvals",
            {
                "ok": True,
                "message": "Approval submitted.",
                "job_id": job_id,
                "state": "completed",
                "data": {
                    "approval": {"approval_id": payload["approval_id"], "approval_scope": "mutation"},
                    "job": {"job_id": job_id, "state": "completed", "approval_status": "active"},
                },
            },
            request=payload,
        )
class FailingMopExecutionApprovalAgent(FakeMopExecutionApprovalAgent):
    async def submit_approval(self, job_id: str, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("submit_approval")
        self.approval_request = {"job_id": job_id, **payload}
        raise MopExecutionAgentError(
            method="POST",
            url=f"http://agent/v1/execution-jobs/{job_id}/approvals",
            status_code=500,
            payload={"text": "Internal Server Error"},
        )


def _waiting_for_approval_run(client, user_id: str, fake_agent: FakeMopExecutionApprovalAgent) -> dict[str, Any]:
    validation = _validated_execution_run(client, user_id, fake_agent)
    response = client.post(
        "/api/mop-execution/dry-run",
        json={"run_id": validation["run_id"], "bundle_id": validation["bundle_id"]},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "waiting_for_approval"
    assert result["dry_run_succeeded"] is True
    return result


def test_mop_execution_phase_i_collects_dry_run_reports_after_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionApprovalAgent()
        result = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        assert "list_reports" in fake_agent.calls
        assert "get_report_metadata" in fake_agent.calls
        assert result["reports"]["summary"] == "Dry-run would create one ConfigMap and one Service."
        assert result["reports"]["command_fingerprints"] == ["fp-helm-template-001", "fp-kubectl-dry-run-002"]
        assert result["reports"]["reports"][0]["downloads"]
        assert result["approval_gate"]["status"] == "waiting_for_human_approval"
        event_types = [event["event_type"] for event in result["events"]]
        assert "reports_updated" in event_types
        assert client.app.state.repository.get_run(result["run_id"]).status == "waiting_for_approval"


def test_mop_execution_phase_i_refreshes_report_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionApprovalAgent()
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.get(f"/api/mop-execution/dry-run-report?run_id={run['run_id']}")

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "reports_available"
        assert result["reports"]["command_fingerprints"] == ["fp-helm-template-001", "fp-kubectl-dry-run-002"]
        assert result["mutation_controls_enabled"] is False
        event_types = [event["event_type"] for event in result["events"]]
        assert event_types.count("reports_updated") >= 2


def test_mop_execution_phase_i_submits_approval_with_scope_and_fingerprints(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionApprovalAgent(accepted=True)
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/approval",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "rationale": "Dry-run evidence, policy gates, and command fingerprints have been reviewed.",
                "scope": {
                    "phase": "dry_run",
                    "target_namespace": "agent-testing",
                    "dry_run_job_id": run["dry_run_job_id"],
                },
                "expires_minutes": 60,
                "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["accepted"] is True
        assert result["mutation_controls_enabled"] is True
        assert fake_agent.approval_request is not None
        assert fake_agent.approval_request["approver_id"] == login.json()["user"]["user_id"]
        assert fake_agent.approval_request["approval_scope"] == "mutation"
        assert fake_agent.approval_request["ticket_reference"].startswith("ESDA-")
        assert fake_agent.approval_request["statement"] == "Dry-run evidence, policy gates, and command fingerprints have been reviewed."
        assert fake_agent.approval_request["approver_role"] == "operator"
        assert "expires_at" in fake_agent.approval_request
        assert "command_fingerprint" not in fake_agent.approval_request
        assert "operator" not in fake_agent.approval_request
        assert "approved_by" not in fake_agent.approval_request
        assert "scope" not in fake_agent.approval_request
        assert "command_fingerprints" not in fake_agent.approval_request
        assert "mutation_allowed" not in fake_agent.approval_request
        event_types = [event["event_type"] for event in result["events"]]
        assert "approval_submitted" in event_types
        assert client.app.state.repository.get_run(run["run_id"]).status == "approved_for_mutation"


def test_mop_execution_phase_i_accepts_agent_active_approval_status(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = ActiveStatusMopExecutionApprovalAgent()
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/approval",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "rationale": "Dry-run succeeded and active execution-agent approval status should enable mutation.",
                "scope": {"phase": "dry_run", "target_namespace": "agent-testing"},
                "expires_minutes": 60,
                "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["accepted"] is True
        assert result["status"] == "approval_accepted"
        assert result["approval_diagnostics"]["approval_status"] == "active"
        assert result["mutation_controls_enabled"] is True
        assert client.app.state.repository.get_run(run["run_id"]).status == "approved_for_mutation"
def test_mop_execution_phase_i_surfaces_agent_approval_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FailingMopExecutionApprovalAgent(accepted=True)
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/approval",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "rationale": "Dry-run evidence was reviewed but the execution agent approval endpoint fails.",
                "scope": {"phase": "dry_run", "target_namespace": "agent-testing"},
                "expires_minutes": 60,
                "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["accepted"] is False
        assert result["status"] == "approval_failed"
        assert result["mutation_controls_enabled"] is False
        assert result["agent_error"]["status_code"] == 500
        assert "approval_response" in result
        assert client.app.state.repository.get_run(run["run_id"]).status == "waiting_for_approval"

def test_mop_execution_phase_i_allows_retry_after_agent_approval_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        failing_agent = FailingMopExecutionApprovalAgent(accepted=True)
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], failing_agent)

        first = client.post(
            "/api/mop-execution/approval",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "rationale": "Dry-run evidence was reviewed but the execution agent approval endpoint fails.",
                "scope": {"phase": "dry_run", "target_namespace": "agent-testing", "dry_run_job_id": ""},
                "expires_minutes": 60,
                "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
            },
        )
        assert first.status_code == 200
        assert first.json()["status"] == "approval_failed"
        assert client.app.state.mop_execution_store.execution_metadata(run["run_id"])["current_state"] == "approval_failed"

        accepting_agent = FakeMopExecutionApprovalAgent(accepted=True)
        client.app.state.mop_execution_agent = accepting_agent
        second = client.post(
            "/api/mop-execution/approval",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "rationale": "Dry-run evidence, policy gates, and command fingerprints have now been reviewed again.",
                "scope": {"phase": "dry_run", "target_namespace": "agent-testing", "dry_run_job_id": ""},
                "expires_minutes": 60,
                "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
            },
        )

        assert second.status_code == 200
        result = second.json()
        assert result["accepted"] is True
        assert result["status"] == "approval_accepted"
        assert accepting_agent.approval_request is not None
        assert accepting_agent.approval_request["approval_scope"] == "mutation"
        assert client.app.state.repository.get_run(run["run_id"]).status == "approved_for_mutation"

def test_mop_execution_phase_i_rejects_scope_mismatch_before_agent_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionApprovalAgent(accepted=True)
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/approval",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "rationale": "Trying to approve an intentionally mismatched namespace scope.",
                "scope": {"phase": "dry_run", "target_namespace": "prod"},
                "command_fingerprints": ["fp-helm-template-001", "fp-kubectl-dry-run-002"],
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["accepted"] is False
        assert result["status"] == "approval_rejected_by_esda"
        assert "submit_approval" not in fake_agent.calls