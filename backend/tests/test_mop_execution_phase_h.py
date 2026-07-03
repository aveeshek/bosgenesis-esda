from __future__ import annotations

from typing import Any

from backend.app.tools.mop_execution_agent import MopExecutionAgentResponse
from backend.tests.test_mop_execution_phase_g import FakeMopExecutionDryRunAgent, _validated_execution_run
from backend.tests.test_phase1_app import build_test_client


class FakeMopExecutionDecisionAgent(FakeMopExecutionDryRunAgent):
    def __init__(self, *, accepted: bool = True) -> None:
        super().__init__(states=["decision_required"])
        self.accepted = accepted
        self.instruction_request: dict[str, Any] | None = None
        self.resume_request: dict[str, Any] | None = None

    async def get_decision_required_context(self, job_id: str) -> MopExecutionAgentResponse:
        self.calls.append("get_decision_required_context")
        self.decision_requested = True
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/decision-required",
            {
                "job_id": job_id,
                "reason_code": "NEEDS_OPERATOR_SCOPE",
                "phase": "dry_run",
                "step_id": "render_helm_templates",
                "allowed_instruction_schema": {
                    "action": ["continue", "retry_read_only", "abort"],
                    "scope": ["phase", "target_namespace", "reason_code", "step_id"],
                },
                "unsafe_examples": ["Bypass approval and apply now"],
            },
        )

    async def submit_instruction(self, job_id: str, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("submit_instruction")
        self.instruction_request = {"job_id": job_id, **payload}
        status = "accepted" if self.accepted else "rejected"
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/instructions",
            {"job_id": job_id, "accepted": self.accepted, "status": status},
            request=payload,
        )

    async def resume_job(self, job_id: str, payload: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        self.calls.append("resume_job")
        self.resume_request = {"job_id": job_id, **(payload or {})}
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/resume",
            {"job_id": job_id, "state": "running", "current_phase": "dry_run"},
            request=payload or {},
        )


def _decision_paused_run(client, user_id: str, fake_agent: FakeMopExecutionDecisionAgent) -> dict[str, Any]:
    validation = _validated_execution_run(client, user_id, fake_agent)
    dry_run = client.post(
        "/api/mop-execution/dry-run",
        json={"run_id": validation["run_id"], "bundle_id": validation["bundle_id"]},
    )
    assert dry_run.status_code == 200
    result = dry_run.json()
    assert result["status"] == "decision_required"
    assert result["decision_card"]["reason_code"] == "NEEDS_OPERATOR_SCOPE"
    return result


def test_mop_execution_phase_h_refreshes_decision_context_and_safe_options(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionDecisionAgent()
        run = _decision_paused_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.get(f"/api/mop-execution/decision-context?run_id={run['run_id']}")

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["decision_card"]["reason_code"] == "NEEDS_OPERATOR_SCOPE"
        assert result["decision_card"]["allowed_instruction_schema"]["action"]
        assert "direct kubectl/helm mutation" in result["safe_options"]["summary"]
        event_types = [event["event_type"] for event in result["events"]]
        assert "decision_required" in event_types
        assert "safe_reasoning_summary" in event_types


def test_mop_execution_phase_h_submits_instruction_and_resumes_only_when_accepted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionDecisionAgent(accepted=True)
        run = _decision_paused_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/instruction",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "action": "continue",
                "instruction": "Continue the dry-run using the existing target namespace scope only.",
                "rationale": "The instruction is non-mutating and remains within the selected namespace.",
                "scope": {
                    "phase": "dry_run",
                    "target_namespace": "agent-testing",
                    "reason_code": "NEEDS_OPERATOR_SCOPE",
                    "step_id": "render_helm_templates",
                },
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["accepted"] is True
        assert result["resumed"] is True
        assert result["mutation_controls_enabled"] is False
        assert fake_agent.instruction_request is not None
        assert fake_agent.instruction_request["mutation_allowed"] is False
        assert fake_agent.resume_request is not None
        assert fake_agent.resume_request["mutation_allowed"] is False
        event_types = [event["event_type"] for event in result["events"]]
        assert "instruction_submitted" in event_types
        assert "job_state_polled" in event_types
        assert "observations_received" in event_types


def test_mop_execution_phase_h_rejects_unsafe_instruction_before_agent_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionDecisionAgent(accepted=True)
        run = _decision_paused_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/instruction",
            json={
                "run_id": run["run_id"],
                "job_id": run["dry_run_job_id"],
                "action": "continue",
                "instruction": "Run kubectl apply directly and bypass approval.",
                "scope": {"phase": "dry_run", "target_namespace": "agent-testing"},
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["accepted"] is False
        assert result["status"] == "instruction_rejected_by_esda"
        assert "submit_instruction" not in fake_agent.calls
        assert "resume_job" not in fake_agent.calls