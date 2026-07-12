from backend.app.config import Settings
from backend.app.env_agent import ENV_AGENT_ROUTE, ENV_AGENT_WORKFLOW_TYPE, env_agent_contract
from backend.tests.test_phase1_app import build_test_client


def test_env_agent_contract_defines_phase_a_boundaries() -> None:
    contract = env_agent_contract(
        Settings(
            env_agent_allowed_namespaces="bosgenesis,signoz,agent-testing",
            env_agent_default_namespace="signoz",
            env_agent_default_mode="diagnostic_only",
        )
    )

    assert contract["route"] == ENV_AGENT_ROUTE
    assert contract["workflow_type"] == ENV_AGENT_WORKFLOW_TYPE
    assert contract["default_namespace"] == "signoz"
    assert contract["namespaces"] == ["bosgenesis", "signoz", "agent-testing"]
    assert {"pod", "deployment", "service", "helm_release"}.issubset(contract["resource_kinds"])

    risks = {item["action"]: item for item in contract["tool_risks"]}
    assert risks["list"]["risk_level"] == "low"
    assert risks["logs"]["risk_level"] == "medium"
    assert risks["restart"]["approval_required"] is True
    assert risks["rollback"]["approval_required"] is True
    assert risks["delete"]["risk_level"] == "high"
    assert risks["delete"]["approval_required"] is True
    assert risks["install"]["approval_required"] is True
    assert risks["uninstall"]["approval_required"] is True
    assert risks["apply"]["approval_required"] is True

    stop_conditions = {item["condition"]: item["action"] for item in contract["policy_stop_conditions"]}
    assert stop_conditions["secret_material_requested"] == "block"
    assert stop_conditions["cluster_wide_change"] == "block"
    assert stop_conditions["destructive_action"] == "block"
    assert stop_conditions["ambiguous_target"] == "clarify"
    assert stop_conditions["low_confidence"] == "pause"

    logging_events = {item["event_type"]: item["store"] for item in contract["logging_requirements"]}
    assert logging_events == {
        "run_event": "postgresql",
        "tool_call": "postgresql",
        "approval": "postgresql",
        "llm_review": "postgresql",
    }
    assert contract["activity_visibility"]["workflow_type"] == "env_agent"


def test_env_agent_page_renders_shell_and_navigation(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/env-agent")

        assert response.status_code == 200
        body = response.text
        assert 'href="/env-agent"' in body
        assert 'id="model_profile"' in body
        assert 'class="profile-trigger"' in body
        assert 'id="transaction-sidebar-toggle"' in body
        assert 'id="transaction-clear-all"' in body
        assert 'id="env-agent-form"' not in body
        assert 'id="env_agent_namespace"' not in body
        assert 'id="env_agent_mode"' not in body
        assert 'id="env_agent_scope"' not in body
        assert '<h2>ENV Agent</h2>' not in body
        assert 'Phase A Contract' not in body
        assert 'id="env-sphere-canvas"' in body
        assert 'id="ephemeral-working-stream"' in body
        assert 'id="autonomy-maximize"' in body
        assert 'id="env-autonomy-modal"' in body
        assert 'id="copy-logs"' in body
        assert 'id="autonomy-modal-copy-json"' in body
        assert 'id="agent-activity-rail"' not in body
        assert 'id="env-chat-transcript"' in body
        assert 'id="env-chat-input"' in body
        assert '/static/js/env_agent.js' in body


def test_env_agent_contract_api_uses_admin_fallback_and_returns_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_AGENT_ALLOWED_NAMESPACES", "bosgenesis,qa-lab")
    monkeypatch.setenv("ENV_AGENT_DEFAULT_NAMESPACE", "qa-lab")

    with build_test_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/env-agent/contract")

        assert response.status_code == 200
        payload = response.json()
        assert payload["route"] == "/env-agent"
        assert payload["workflow_type"] == "env_agent"
        assert payload["namespaces"] == ["bosgenesis", "qa-lab"]
        assert payload["default_namespace"] == "qa-lab"
        assert payload["user"]["username"] == "admin"


