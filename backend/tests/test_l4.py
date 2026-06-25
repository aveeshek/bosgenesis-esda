from pathlib import Path

from backend.tests.test_phase1_app import build_test_client


def write_l4_policy(path: Path, *, enabled: bool, requires_procedure: bool = False) -> None:
    path.write_text(
        f"""
version: 1
operational_design_domain:
  allowed_workflows:
    - health_check_diagnostic
    - release_note_creation
    - k8s_management
    - helm_management
    - mop_execution
  allowed_environments:
    - local
    - dev
  allowed_namespaces:
    - bosgenesis
  production:
    mutation_allowed: false
  conditional_l4:
    enabled: {str(enabled).lower()}
    allowed_workflows:
      - k8s_management
      - helm_management
    allowed_environments:
      - local
      - dev
    allowed_namespaces:
      - bosgenesis
    allowed_roles:
      - admin
      - operator
    allowed_tools:
      - mcp.k8s_inspector
      - rest.get
      - helm.status
    allowed_risk_levels:
      - low
      - medium_preapproved
    approved_procedures: []
    max_retries: 2
    max_duration_seconds: 900
    requires_approved_procedure: {str(requires_procedure).lower()}
    requires_rollback_metadata: true
    requires_validation_rules: true
    requires_postgresql_logging: true
mutation_policy:
  always_requires_approval:
    - kubernetes_restart
    - kubernetes_patch
    - helm_upgrade
    - helm_rollback
  denied:
    - raw_shell
    - raw_powershell
    - kubernetes_secret_read
    - namespace_delete
    - helm_uninstall
rollback_requirements:
  fields:
    - rollback_plan
    - pre_change_state
    - validation_plan
    - owner
""".strip(),
        encoding="utf-8",
    )


def login_admin(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def eligible_payload(**overrides):
    payload = {
        "workflow_type": "k8s_management",
        "environment": "local",
        "namespace": "bosgenesis",
        "tool_sequence": [
            {
                "tool_name": "mcp.k8s_inspector",
                "arguments": {"tool_name": "list_pods", "arguments": {"namespace": "bosgenesis"}},
                "risk_level": "low",
            }
        ],
        "rollback_metadata": {
            "rollback_plan": "No mutation in read-only L4 probe.",
            "pre_change_state": "No pre-change state required for read-only probe.",
            "validation_plan": "Validate MCP success envelope.",
            "owner": "admin",
        },
        "validation_checks": [{"type": "mcp_status", "expected": "success"}],
        "logging_available": True,
    }
    payload.update(overrides)
    return payload


def test_l4_eligibility_creates_audit_when_odd_enabled(tmp_path, monkeypatch) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_l4_policy(policy_path, enabled=True)
    monkeypatch.setenv("POLICY_RULES_PATH", str(policy_path))

    with build_test_client(tmp_path, monkeypatch) as client:
        login_admin(client)
        response = client.post("/api/l4/eligibility", json=eligible_payload())

        assert response.status_code == 200
        result = response.json()
        assert result["eligible"] is True
        assert result["decision"] == "eligible"
        assert result["audit_id"]

        audits = client.get("/api/l4/audit")
        assert audits.status_code == 200
        assert audits.json()["audits"][0]["audit_id"] == result["audit_id"]

        export = client.get(f"/api/l4/audit/{result['audit_id']}/export")
        assert export.status_code == 200
        assert "# L4 Audit Report" in export.text


def test_l4_eligibility_requires_approved_procedure_when_policy_demands_it(
    tmp_path,
    monkeypatch,
) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_l4_policy(policy_path, enabled=True, requires_procedure=True)
    monkeypatch.setenv("POLICY_RULES_PATH", str(policy_path))

    with build_test_client(tmp_path, monkeypatch) as client:
        login_admin(client)
        procedure = client.post(
            "/api/procedures",
            json={
                "name": "Read-only k8s inspection",
                "workflow_type": "k8s_management",
                "version": "v1",
                "status": "approved",
                "steps": eligible_payload()["tool_sequence"],
                "policies": [{"environment": "local", "namespace": "bosgenesis"}],
            },
        )
        assert procedure.status_code == 200
        procedure_id = procedure.json()["procedure"]["procedure_id"]

        response = client.post(
            "/api/l4/eligibility",
            json=eligible_payload(procedure_id=procedure_id, procedure_version="v1"),
        )

        assert response.status_code == 200
        result = response.json()
        assert result["eligible"] is True
        assert result["procedure"]["procedure_id"] == procedure_id


def test_l4_eligibility_rejects_high_risk_tool(tmp_path, monkeypatch) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_l4_policy(policy_path, enabled=True)
    monkeypatch.setenv("POLICY_RULES_PATH", str(policy_path))

    with build_test_client(tmp_path, monkeypatch) as client:
        login_admin(client)
        response = client.post(
            "/api/l4/eligibility",
            json=eligible_payload(
                tool_sequence=[
                    {
                        "tool_name": "k8s.restart",
                        "arguments": {"action": "restart", "resource": "deployment/api"},
                        "risk_level": "high",
                    }
                ]
            ),
        )

        assert response.status_code == 200
        result = response.json()
        assert result["eligible"] is False
        assert any("outside L4 threshold" in reason for reason in result["reasons"])


def test_l4_eligibility_rejects_when_odd_disabled(tmp_path, monkeypatch) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_l4_policy(policy_path, enabled=False)
    monkeypatch.setenv("POLICY_RULES_PATH", str(policy_path))

    with build_test_client(tmp_path, monkeypatch) as client:
        login_admin(client)
        response = client.post("/api/l4/eligibility", json=eligible_payload())

        assert response.status_code == 200
        result = response.json()
        assert result["eligible"] is False
        assert "Conditional L4 is disabled by ODD." in result["reasons"]


def test_l4_stop_condition_endpoint_reports_policy_denial(tmp_path, monkeypatch) -> None:
    policy_path = tmp_path / "policy.yaml"
    write_l4_policy(policy_path, enabled=True)
    monkeypatch.setenv("POLICY_RULES_PATH", str(policy_path))

    with build_test_client(tmp_path, monkeypatch) as client:
        login_admin(client)
        response = client.post(
            "/api/l4/stop-check",
            json={"policy_decision": "deny", "risk_level": "low"},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["stop"] is True
        assert "policy_denial" in result["reasons"]
