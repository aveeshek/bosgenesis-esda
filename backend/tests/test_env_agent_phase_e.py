from backend.tests.test_phase1_app import build_test_client


def test_env_agent_phase_e_chat_creates_persisted_diagnostic_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENV_AGENT_ALLOWED_NAMESPACES", "bosgenesis,agent-testing")
    monkeypatch.setenv("ENV_AGENT_DEFAULT_NAMESPACE", "agent-testing")
    monkeypatch.setenv("K8S_INSPECTOR_AGENT_MCP_URL", "")
    monkeypatch.setenv("MCP_K8S_INSPECTOR_URL", "")
    monkeypatch.setenv("HELM_MANAGER_AGENT_MCP_URL", "")

    with build_test_client(tmp_path, monkeypatch) as client:
        unauthenticated = client.post(
            "/api/env-agent/chat",
            json={"message": "Tell me how many pods have issues in this namespace."},
        )
        assert unauthenticated.status_code == 200
        assert unauthenticated.json()["snapshot"]["run"]["user_id"].startswith("usr_")

        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        outside = client.post(
            "/api/env-agent/chat",
            json={
                "message": "Check namespace health in outside-policy namespace",
                "model_profile": "azure_configured",
            },
        )
        assert outside.status_code == 200
        assert outside.json()["snapshot"]["run"]["namespace"] == "outside-policy"

        response = client.post(
            "/api/env-agent/chat",
            json={
                "message": "Tell me how many pods have issues in agent-testing namespace.",
                "model_profile": "azure_configured",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"].startswith("env_")
        assert payload["status"] in {"completed", "needs_review", "blocked", "proposal_ready", "needs_clarification"}
        assert "## Evidence" in payload["answer"]
        assert payload["snapshot"]["run"]["workflow_type"] == "env_agent"
        assert payload["snapshot"]["run"]["namespace"] == "agent-testing"

        event_types = [event["event_type"] for event in payload["snapshot"]["events"]]
        assert "run_started" in event_types
        assert "safe_reasoning_summary" in event_types
        assert "plan_created" in event_types
        assert "inspection_completed" in event_types
        assert "diagnosis_completed" in event_types
        assert {"run_completed", "run_needs_review"}.intersection(event_types)

        tool_calls = payload["snapshot"].get("tool_calls") or []
        assert tool_calls
        assert all(call["tool_name"].startswith("env.") for call in tool_calls)

        transactions = client.get("/api/transactions?workflow_type=env_agent")
        assert transactions.status_code == 200
        rows = transactions.json()["transactions"]
        assert rows
        assert rows[0]["run_id"] == payload["run_id"]

        sessions = client.get("/api/env-agent/sessions")
        assert sessions.status_code == 200
        session_rows = sessions.json()["sessions"]
        assert session_rows
        assert session_rows[0]["session_id"] == payload["chat_session_id"]
        assert session_rows[0]["latest_run_id"] == payload["run_id"]

        follow_up = client.post(
            "/api/env-agent/chat",
            json={
                "message": "Now summarize the same namespace status in one sentence.",
                "session_id": payload["chat_session_id"],
                "model_profile": "azure_configured",
            },
        )
        assert follow_up.status_code == 200
        follow_up_payload = follow_up.json()
        assert follow_up_payload["chat_session_id"] == payload["chat_session_id"]
        assert follow_up_payload["snapshot"]["run"]["namespace"] == "agent-testing"

        session_snapshot = client.get(f"/api/env-agent/sessions/{payload['chat_session_id']}")
        assert session_snapshot.status_code == 200
        restored_session = session_snapshot.json()
        assert restored_session["session"]["session_id"] == payload["chat_session_id"]
        assert len(restored_session["messages"]) == 4
        assert restored_session["latest_snapshot"]["run"]["run_id"] == follow_up_payload["run_id"]
        assert restored_session["memory_context"]["short_term"]["message_count"] == 4
        assert restored_session["memory_context"]["long_term"]["memory_count"] >= 1
        assert restored_session["memory_context"]["latest_namespace"] == "agent-testing"

        snapshot = client.get(f"/api/runs/{payload['run_id']}/snapshot")
        assert snapshot.status_code == 200
        restored = snapshot.json()
        assert restored["run"]["run_id"] == payload["run_id"]
        assert restored["events"][-1]["sequence"] >= 1
