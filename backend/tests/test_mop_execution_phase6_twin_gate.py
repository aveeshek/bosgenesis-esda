from __future__ import annotations

from typing import Any

import pytest

from backend.app.mop_execution import MopExecutionPreflightService
from backend.tests.test_mop_execution_phase_e import _bundle_bytes, _seed_bundle_run
from backend.tests.test_mop_execution_phase_f import FakeMopExecutionAgent
from backend.tests.test_mop_execution_phase_g import FakeMopExecutionDryRunAgent
from backend.tests.test_phase1_app import build_test_client


class FakeTransactionalTwinGateway:
    def __init__(self, gate: dict[str, Any]) -> None:
        self.gate_payload = gate
        self.calls: list[str] = []

    async def gate(self, twin_id: str) -> dict[str, Any]:
        self.calls.append(twin_id)
        return self.gate_payload


def _gate(bundle: bytes, **overrides: Any) -> dict[str, Any]:
    facts = {
        "twin_id": "twin-phase6-route",
        "decision_version": 4,
        "decision": "amber",
        "decision_is_final": True,
        "lifecycle_status": "succeeded",
        "bundle_hash": MopExecutionPreflightService._canonical_zip_sha256(bundle),
        "input_hash": "a" * 64,
        "target_cluster": "contract-cluster",
        "target_namespace": "agent-testing",
        "policy_version": "namespace-twin-policy-2026.07.1",
        "risk_rule_version": "namespace-twin-risk-1.0.0",
        "freshness": "fresh",
        "dry_run_job_id": "job-authoritative-phase6",
        "dry_run": "passed",
        "command_fingerprints": ["sha256:phase6-command"],
        "command_fingerprint_hash": "e" * 64,
        "rollback": "medium",
        "drift": "none",
        "approval": "required",
        "start_execution_enabled": False,
        "request_approval_enabled": True,
        "regenerate_enabled": False,
    }
    facts.update(overrides.pop("facts", {}))
    return {
        "schema_version": "1.0.0",
        "data_mode": "real_core",
        "module_mode": "authoritative_execution_gate",
        "gate_hash": overrides.pop("gate_hash", "gate-phase6-route"),
        "gate_facts": facts,
        "risk": {"level": "medium", "score": 55},
        "policy": overrides.pop("policy", "allow_with_approval"),
        "evidence": {"classification": "complete", "freshness": "fresh"},
        "decision_projection": {"level": facts["decision"], "approval_required": True},
        "reasons": [{"code": "HUMAN_APPROVAL_REQUIRED", "summary": "Approval required."}],
        "actions": {
            "start_execution": {"enabled": facts["start_execution_enabled"]},
            "request_approval": {"enabled": facts["request_approval_enabled"]},
            "regenerate_twin": {"enabled": facts["regenerate_enabled"]},
        },
        **overrides,
    }


def _validated_request(client, user_id: str, bundle: bytes, gate: dict[str, Any]):
    artifact = _seed_bundle_run(client, user_id, bundle)
    fake_agent = FakeMopExecutionAgent()
    fake_gateway = FakeTransactionalTwinGateway(gate)
    client.app.state.mop_execution_agent = fake_agent
    client.app.state.digital_twin_gateway = fake_gateway
    response = client.post(
        "/api/mop-execution/validate",
        json={
            "source_type": "activity_run",
            "run_id": "mop_generation_good",
            "artifact_id": artifact["artifact_id"],
            "target_namespace": "agent-testing",
            "execution_mode": "dry_run_then_approval",
            "twin_id": gate["gate_facts"]["twin_id"],
            "twin_decision_version": gate["gate_facts"]["decision_version"],
            "twin_gate_hash": gate["gate_hash"],
        },
    )
    return response, fake_agent, fake_gateway


def test_real_core_accepts_hash_bound_amber_gate_for_validation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    bundle = _bundle_bytes()
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        response, fake_agent, fake_gateway = _validated_request(
            client, login.json()["user"]["user_id"], bundle, _gate(bundle)
        )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["twin_gate"]["gate_hash"] == "gate-phase6-route"
    assert fake_gateway.calls == ["twin-phase6-route"]
    assert "register_bundle" in fake_agent.calls


def test_real_core_accepts_hash_bound_green_gate_under_baseline_approval_policy(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    bundle = _bundle_bytes()
    green_gate = _gate(
        bundle,
        facts={
            "decision": "green",
            "approval": "required",
            "start_execution_enabled": True,
            "request_approval_enabled": True,
        },
        policy="allow_with_approval",
    )
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        response, fake_agent, fake_gateway = _validated_request(
            client, login.json()["user"]["user_id"], bundle, green_gate
        )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["twin_gate"]["decision"] == "green"
    assert fake_gateway.calls == ["twin-phase6-route"]
    assert "register_bundle" in fake_agent.calls


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        ({"decision": "red"}, "Red Namespace Digital Twin"),
        ({"freshness": "stale"}, "stale"),
        ({"drift": "major"}, "Material drift"),
        ({"target_namespace": "other"}, "target namespace"),
    ],
)
def test_real_core_blocks_ineligible_twin_before_agent_invocation(
    tmp_path, monkeypatch, facts, expected
) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    bundle = _bundle_bytes()
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        response, fake_agent, _ = _validated_request(
            client,
            login.json()["user"]["user_id"],
            bundle,
            _gate(bundle, facts=facts),
        )

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["status"] == "twin_gate_blocked"
    assert expected.lower() in " ".join(response.json()["failures"]).lower()
    assert fake_agent.calls == []


def test_real_core_rejects_missing_twin_binding(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_GATE_REQUIRED", "true")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    bundle = _bundle_bytes()
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        artifact = _seed_bundle_run(client, login.json()["user"]["user_id"], bundle)
        fake_agent = FakeMopExecutionAgent()
        client.app.state.mop_execution_agent = fake_agent
        response = client.post(
            "/api/mop-execution/validate",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
                "execution_mode": "approved_mutation",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "twin_gate_blocked"
    assert fake_agent.calls == []


def test_real_core_allows_approved_execution_without_twin_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_GATE_REQUIRED", "false")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    bundle = _bundle_bytes()
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        artifact = _seed_bundle_run(client, login.json()["user"]["user_id"], bundle)
        fake_agent = FakeMopExecutionAgent()
        client.app.state.mop_execution_agent = fake_agent
        response = client.post(
            "/api/mop-execution/validate",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
                "execution_mode": "approved_mutation",
            },
        )

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json().get("twin_gate") in (None, {})
    assert "register_bundle" in fake_agent.calls



def test_provisional_twin_can_bootstrap_identity_bound_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")
    bundle = _bundle_bytes()
    gate = _gate(
        bundle,
        facts={
            "decision": "pending",
            "decision_is_final": False,
            "lifecycle_status": "awaiting_dry_run",
            "dry_run": "pending",
            "dry_run_job_id": None,
            "command_fingerprints": [],
            "command_fingerprint_hash": None,
            "request_approval_enabled": False,
        },
    )
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        artifact = _seed_bundle_run(client, login.json()["user"]["user_id"], bundle)
        fake_agent = FakeMopExecutionDryRunAgent(states=["succeeded"])
        client.app.state.mop_execution_agent = fake_agent
        client.app.state.digital_twin_gateway = FakeTransactionalTwinGateway(gate)
        validation = client.post(
            "/api/mop-execution/validate",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
                "execution_mode": "dry_run_only",
                "twin_id": gate["gate_facts"]["twin_id"],
                "twin_decision_version": gate["gate_facts"]["decision_version"],
                "twin_gate_hash": gate["gate_hash"],
            },
        )
        result = validation.json()
        dry_run = client.post(
            "/api/mop-execution/dry-run",
            json={
                "run_id": result["run_id"],
                "bundle_id": result["bundle_id"],
                "execution_mode": "dry_run_only",
                "twin_id": gate["gate_facts"]["twin_id"],
                "twin_decision_version": gate["gate_facts"]["decision_version"],
                "twin_gate_hash": gate["gate_hash"],
            },
        )

    assert validation.status_code == 200
    assert result["valid"] is True
    assert dry_run.status_code == 200
    assert dry_run.json()["dry_run_succeeded"] is True
    assert fake_agent.create_job_request is not None
    assert fake_agent.create_job_request["namespace_twin_id"] == gate["gate_facts"]["twin_id"]
    assert fake_agent.create_job_request["namespace_twin_input_hash"] == gate["gate_facts"]["input_hash"]
    assert fake_agent.create_job_request["bundle_hash"] == gate["gate_facts"]["bundle_hash"]
