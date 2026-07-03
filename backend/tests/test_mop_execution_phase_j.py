from __future__ import annotations

import json
from typing import Any

from backend.app.tools.mop_execution_agent import MopExecutionAgentError, MopExecutionAgentResponse
from backend.tests.test_mop_execution_phase_i import FakeMopExecutionApprovalAgent, _waiting_for_approval_run
from backend.tests.test_phase1_app import build_test_client


class FakeMopExecutionMutationAgent(FakeMopExecutionApprovalAgent):
    def __init__(self, *, mutation_states: list[str] | None = None, instruction_accepted: bool = True) -> None:
        super().__init__(accepted=True)
        self.mutation_states = list(mutation_states or ["running", "succeeded"])
        self.instruction_accepted = instruction_accepted
        self.mutation_create_request: dict[str, Any] | None = None
        self.mutation_start_request: dict[str, Any] | None = None
        self.mutation_resume_request: dict[str, Any] | None = None
        self.mutation_instruction_request: dict[str, Any] | None = None
        self.mutation_instruction_requests: list[dict[str, Any]] = []

    async def create_job(self, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        if payload.get("mode") == "mutation" or payload.get("execution_mode") == "approved_mutation":
            self.calls.append("create_mutation_job")
            self.mutation_create_request = payload
            return self._response(
                "POST",
                "http://agent/v1/execution-jobs",
                {"job_id": "mutation_job_456", "state": "created", "current_phase": "mutation"},
                request=payload,
            )
        return await super().create_job(payload)

    async def start_job(self, job_id: str, payload: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        if job_id == "mutation_job_456" or (job_id == "dry_run_job_123" and (payload or {}).get("phase") == "mutation"):
            self.calls.append("start_mutation_job")
            self.mutation_start_request = {"job_id": job_id, **(payload or {})}
            return self._response(
                "POST",
                f"http://agent/v1/execution-jobs/{job_id}/start",
                {"job_id": job_id, "state": "running", "current_phase": "mutation"},
                request=payload or {},
            )
        return await super().start_job(job_id, payload)

    async def resume_job(self, job_id: str, payload: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        self.calls.append("resume_mutation_job")
        self.mutation_resume_request = {"job_id": job_id, **(payload or {})}
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/resume",
            {"job_id": job_id, "state": "running", "current_phase": "mutation"},
            request=payload or {},
        )

    async def get_job(self, job_id: str) -> MopExecutionAgentResponse:
        if job_id in {"mutation_job_456", "dry_run_job_123"} and "start_mutation_job" in self.calls or "resume_mutation_job" in self.calls:
            self.calls.append("get_mutation_job")
            state = self.mutation_states.pop(0) if self.mutation_states else "succeeded"
            payload = {"job_id": job_id, "state": state, "current_phase": "mutation"}
            if state == "decision_required":
                payload["reason"] = "mutation_instruction_required"
                payload["reason_code"] = "MUTATION_BLOCKED"
            return self._response(
                "GET",
                f"http://agent/v1/execution-jobs/{job_id}",
                payload,
            )
        return await super().get_job(job_id)

    async def submit_instruction(self, job_id: str, payload: dict[str, Any]) -> MopExecutionAgentResponse:
        self.calls.append("submit_mutation_instruction")
        self.mutation_instruction_request = {"job_id": job_id, **payload}
        self.mutation_instruction_requests.append(self.mutation_instruction_request)
        return self._response(
            "POST",
            f"http://agent/v1/execution-jobs/{job_id}/instructions",
            {"ok": self.instruction_accepted, "status": "accepted" if self.instruction_accepted else "rejected", "state": "executing" if self.instruction_accepted else "decision_required", "data": {"instruction": payload}},
            request=payload,
        )

    async def get_decision_required_context(self, job_id: str) -> MopExecutionAgentResponse:
        self.calls.append("get_decision_required_context")
        self.decision_requested = True
        return self._response(
            "GET",
            f"http://agent/v1/execution-jobs/{job_id}/decision-required",
            {"job_id": job_id, "reason_code": "MUTATION_BLOCKED", "reason": "mutation_instruction_required", "phase": "mutation"},
        )


class ObservationOnlyMutationDecisionAgent(FakeMopExecutionMutationAgent):
    async def get_job(self, job_id: str) -> MopExecutionAgentResponse:
        if job_id in {"mutation_job_456", "dry_run_job_123"} and ("start_mutation_job" in self.calls or "resume_mutation_job" in self.calls):
            self.calls.append("get_mutation_job")
            state = self.mutation_states.pop(0) if self.mutation_states else "succeeded"
            return self._response(
                "GET",
                f"http://agent/v1/execution-jobs/{job_id}",
                {"job_id": job_id, "state": state, "current_phase": "mutation"},
            )
        return await super().get_job(job_id)

    async def get_decision_required_context(self, job_id: str) -> MopExecutionAgentResponse:
        self.calls.append("get_decision_required_context")
        self.decision_requested = True
        raise MopExecutionAgentError(
            method="GET",
            url=f"http://agent/v1/execution-jobs/{job_id}/decision-required",
            status_code=404,
            payload={"text": "Not Found"},
        )

    async def get_observations(self, job_id: str, params: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        phase = (params or {}).get("phase") or "unknown"
        if phase == "mutation" and ("start_mutation_job" in self.calls or "resume_mutation_job" in self.calls):
            self.calls.append("get_observations")
            return self._response(
                "GET",
                f"http://agent/v1/execution-jobs/{job_id}/observations",
                {
                    "data": {
                        "observations": [
                            {
                                "observation_type": "mutation_result",
                                "summary": "mutation_instruction_required",
                                "phase_id": "apply_configmaps",
                                "step_id": "apply_configmaps-1-configmap-istio-ca-crl",
                                "policy_blocks": [
                                    {
                                        "code": "INSTRUCTION_REQUIRED",
                                        "message": "Mutation requires an explicit continue instruction.",
                                        "guardrail": "external_instruction",
                                    }
                                ],
                                "result": {"unknown_mutation_outcome": False},
                            }
                        ]
                    }
                },
                request={"params": params or {}},
            )
        return await super().get_observations(job_id, params)

def _approved_mutation_run(client, user_id: str, fake_agent: FakeMopExecutionMutationAgent) -> dict[str, Any]:
    run = _waiting_for_approval_run(client, user_id, fake_agent)
    approval = client.post(
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
    assert approval.status_code == 200
    result = approval.json()
    assert result["accepted"] is True
    return result


def test_mop_execution_phase_j_creates_starts_and_polls_mutation_job(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "3")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionMutationAgent(mutation_states=["running", "succeeded"])
        approved = _approved_mutation_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post("/api/mop-execution/mutation", json={"run_id": approved["run_id"], "strategy": "create_job"})

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "mutation_succeeded"
        assert result["mutation_job_id"] == "mutation_job_456"
        assert result["mutation_succeeded"] is True
        assert fake_agent.mutation_create_request is not None
        assert fake_agent.mutation_create_request["mutation_allowed"] is True
        assert fake_agent.mutation_create_request["approval"]["approval_id"]
        assert fake_agent.mutation_start_request is not None
        assert fake_agent.mutation_start_request["mutation_allowed"] is True
        event_types = [event["event_type"] for event in result["events"]]
        assert "mutation_job_created" in event_types
        assert "job_started" in event_types
        assert "job_state_polled" in event_types
        assert "observations_received" in event_types
        metadata = client.app.state.mop_execution_store.execution_metadata(approved["run_id"])
        assert metadata["mutation_job_id"] == "mutation_job_456"
        assert client.app.state.repository.get_run(approved["run_id"]).status == "mutation_succeeded"


def test_mop_execution_phase_j_blocks_without_accepted_approval(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionMutationAgent()
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post("/api/mop-execution/mutation", json={"run_id": run["run_id"]})

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["status"] == "mutation_blocked_by_approval"
        assert "create_mutation_job" not in fake_agent.calls


def test_mop_execution_phase_j_surfaces_rollback_required_without_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "3")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionMutationAgent(mutation_states=["rollback_required"])
        approved = _approved_mutation_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post("/api/mop-execution/mutation", json={"run_id": approved["run_id"], "strategy": "create_job"})

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["status"] == "rollback_required"
        assert result["rollback_required"] is True
        assert fake_agent.calls.count("get_mutation_job") == 1
        event_types = [event["event_type"] for event in result["events"]]
        assert "rollback_cleanup_updated" in event_types
        assert client.app.state.repository.get_run(approved["run_id"]).status == "rollback_required"


def test_mop_execution_phase_j_auto_continues_mutation_instruction_gate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "2")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionMutationAgent(mutation_states=["decision_required", "succeeded"])
        approved = _approved_mutation_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/mutation",
            json={"run_id": approved["run_id"]},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "mutation_succeeded"
        assert result["mutation_succeeded"] is True
        assert fake_agent.mutation_instruction_request is not None
        assert fake_agent.mutation_instruction_request["instruction_type"] == "continue"
        assert fake_agent.mutation_instruction_request["target_phase_id"] == "mutation"
        assert fake_agent.mutation_instruction_request["metadata"]["source"] == "esda_auto_continue_after_approval"
        llm_decision = fake_agent.mutation_instruction_request["metadata"]["llm_runtime_decision"]
        assert llm_decision["prompt_version"] == "mop_execution_runtime_planner_v1"
        assert llm_decision["action"] == "continue"
        assert llm_decision["deterministic_continue_gate"] is True
        event_types = [event["event_type"] for event in result["events"]]
        assert "decision_required" in event_types
        assert "instruction_submitted" in event_types
        assert client.app.state.repository.get_run(approved["run_id"]).status == "mutation_succeeded"


def test_mop_execution_phase_j_auto_continues_when_decision_context_missing_but_observations_show_instruction_gate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "2")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = ObservationOnlyMutationDecisionAgent(mutation_states=["decision_required", "succeeded"])
        approved = _approved_mutation_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/mutation",
            json={"run_id": approved["run_id"]},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "mutation_succeeded"
        assert fake_agent.mutation_instruction_request is not None
        assert fake_agent.mutation_instruction_request["instruction_type"] == "continue"
        assert fake_agent.mutation_instruction_request["target_phase_id"] == "apply_configmaps"
        assert fake_agent.mutation_instruction_request["target_step_id"] == "apply_configmaps-1-configmap-istio-ca-crl"
        assert fake_agent.mutation_instruction_request["metadata"]["requested_target_phase_id"] == "apply_configmaps"
        llm_decision = fake_agent.mutation_instruction_request["metadata"]["llm_runtime_decision"]
        assert llm_decision["action"] == "continue"
        assert llm_decision["deterministic_continue_gate"] is True
        assert "submit_mutation_instruction" in fake_agent.calls
        output_log = tmp_path / "logs" / "mop-execution-runs" / f"{approved['run_id']}.jsonl"
        assert output_log.exists()
        output_events = [json.loads(line)["event_type"] for line in output_log.read_text(encoding="utf-8").splitlines()]
        assert "runtime_planner_result" in output_events
        assert "instruction_payload_prepared" in output_events
        assert "instruction_result" in output_events
        assert "mutation_succeeded" in output_events
        assert client.app.state.repository.get_run(approved["run_id"]).status == "mutation_succeeded"

def test_mop_execution_phase_j_auto_continues_multiple_mutation_gates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "2")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionMutationAgent(
            mutation_states=["decision_required", "decision_required", "decision_required", "succeeded"]
        )
        approved = _approved_mutation_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/mutation",
            json={"run_id": approved["run_id"]},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "mutation_succeeded"
        assert result["mutation_succeeded"] is True
        assert len(fake_agent.mutation_instruction_requests) == 3
        assert all(item["instruction_type"] == "continue" for item in fake_agent.mutation_instruction_requests)
        assert client.app.state.repository.get_run(approved["run_id"]).status == "mutation_succeeded"


def test_mop_execution_phase_j_can_continue_existing_job_when_requested(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "2")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionMutationAgent(mutation_states=["succeeded"])
        approved = _approved_mutation_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post(
            "/api/mop-execution/mutation",
            json={"run_id": approved["run_id"]},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["mutation_succeeded"] is True
        assert fake_agent.mutation_start_request is not None
        assert fake_agent.mutation_start_request["job_id"] == approved["dry_run_job_id"]
        assert fake_agent.mutation_start_request["phase"] == "mutation"
        assert fake_agent.mutation_start_request["mutation_allowed"] is True
        assert fake_agent.mutation_start_request["dry_run_job_id"] == approved["dry_run_job_id"]


