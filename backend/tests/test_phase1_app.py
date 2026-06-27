import importlib

from fastapi.testclient import TestClient

from backend.app.auth.security import SessionPrincipal, create_session_cookie

from backend.app.config import get_settings


def build_test_client(tmp_path, monkeypatch):
    db_file = tmp_path / "phase1.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_file}")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTER", "disabled")
    monkeypatch.setenv("LLM_REVIEW_LOGGING_ENABLED", "true")
    monkeypatch.setenv("LLM_DEFAULT_MODEL_PROFILE", "azure_configured")
    monkeypatch.setenv("AZURE_OPENAI_AUTH_MODE", "api_key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_GPT5_DEPLOYMENT", "")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "")
    monkeypatch.setenv("OPENAI_DEPLOYMENT", "")
    monkeypatch.setenv("OPENAI_API_VERSION", "")
    get_settings.cache_clear()
    import backend.app.main as main_module

    main_module = importlib.reload(main_module)
    return TestClient(main_module.create_app())


def test_phase1_auth_api_roundtrip(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        assert login.json()["user"]["username"] == "admin"

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["user"]["roles"] == ["admin", "operator", "approver"]

        logout = client.post("/api/auth/logout")
        assert logout.status_code == 200


def test_phase1_auth_api_rejects_missing_session(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/auth/me")
        assert response.status_code == 401


def test_phase1_auth_cookie_normalizes_stale_user_id(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        stale_principal = SessionPrincipal(
            user_id="usr_from_previous_database",
            username="admin",
            roles=["admin", "unexpected_cookie_role"],
        )
        client.cookies.set("esda_session", create_session_cookie(stale_principal, "test-secret"))

        response = client.get("/api/auth/me")

        assert response.status_code == 200
        user = response.json()["user"]
        assert user["username"] == "admin"
        assert user["user_id"] != stale_principal.user_id
        assert user["roles"] == ["admin", "operator", "approver"]

        transactions = client.get("/api/transactions?workflow_type=release_note_creation")
        assert transactions.status_code == 200


def test_llm_smoke_test_endpoint_requires_auth_and_reports_fallback(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        unauthenticated = client.post("/api/llm/smoke-test")
        assert unauthenticated.status_code == 401

        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        response = client.post("/api/llm/smoke-test")
        assert response.status_code == 200
        result = response.json()
        assert result["ok"] is False
        assert result["configured"] is False
        assert result["used_fallback"] is True


def test_llm_chat_endpoint_requires_auth_and_reports_fallback(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        unauthenticated = client.post("/api/llm/chat", json={"message": "Hello"})
        assert unauthenticated.status_code == 401

        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        response = client.post(
            "/api/llm/chat",
            json={"message": "Hello from the UI", "model_profile": "azure_configured"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["ok"] is False
        assert result["configured"] is False
        assert result["used_fallback"] is True
        assert "not configured" in result["message"]


def test_llm_model_profiles_endpoint_requires_auth_and_lists_profiles(
    tmp_path, monkeypatch
) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        unauthenticated = client.get("/api/llm/model-profiles")
        assert unauthenticated.status_code == 401

        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        response = client.get("/api/llm/model-profiles")
        assert response.status_code == 200
        result = response.json()
        profile_ids = {profile["profile_id"] for profile in result["profiles"]}
        assert result["default_model_profile"] == "azure_configured"
        assert {"azure_gpt5_pro", "azure_gpt41_mini", "ollama_llama70b", "ollama_gemma4"}.issubset(
            profile_ids
        )


def test_release_notes_page_renders_model_selectors(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/release-notes")
        assert response.status_code == 200
        assert 'id="model_profile"' in response.text
        assert 'id="llm-model-profile"' not in response.text
        assert "GPT 5 Pro" in response.text
        assert "Llama70B" in response.text
        assert 'id="release-progress-panel"' in response.text
        assert 'id="release-sphere-canvas"' in response.text
        assert "col-xl-6 release-progress-column" in response.text
        assert 'id="ephemeral-working-stream"' in response.text
        assert 'id="safe-summary-list"' in response.text
        assert 'id="agent-activity-graph"' in response.text
        assert 'class="agent-activity-rail is-dormant is-collapsed"' in response.text
        assert 'id="agent-activity-toggle"' in response.text
        assert 'id="agent-activity-pin"' in response.text
        assert 'data-auto-hide-ms="30000"' in response.text


def test_run_transactions_list_snapshot_and_clear(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        repository = client.app.state.repository
        run_id = "run_transaction_test"

        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Generate release notes for https://github.com/example/repo",
            target_url="https://github.com/example/repo",
            namespace=None,
            workflow_type="release_note_creation",
        )
        first_event = repository.add_event(run_id, "run_started", "Run started", {"step": 1})
        second_event = repository.add_event(run_id, "run_completed", "Run completed", {"step": 2})
        repository.update_status(run_id, "completed", final_report="## Release Notes")

        transactions = client.get("/api/transactions?workflow_type=release_note_creation")
        assert transactions.status_code == 200
        transaction_rows = transactions.json()["transactions"]
        assert [item["run_id"] for item in transaction_rows] == [run_id]
        assert transaction_rows[0]["title"] == transaction_rows[0]["session_name"]
        assert "Generate release notes" not in transaction_rows[0]["title"]
        assert transaction_rows[0]["title"].endswith("TEST")
        assert transaction_rows[0]["last_event_sequence"] == 2

        snapshot = client.get(f"/api/runs/{run_id}/snapshot")
        assert snapshot.status_code == 200
        snapshot_json = snapshot.json()
        assert snapshot_json["run"]["run_id"] == run_id
        assert snapshot_json["run"]["final_report"] == "## Release Notes"
        assert [event["event_id"] for event in snapshot_json["events"]] == [
            first_event["event_id"],
            second_event["event_id"],
        ]
        assert [event["sequence"] for event in snapshot_json["events"]] == [1, 2]
        assert snapshot_json["last_event_id"] == second_event["event_id"]
        assert snapshot_json["last_event_sequence"] == 2

        after_first = repository.list_events(run_id, after_event_id=first_event["event_id"])
        assert [event["event_id"] for event in after_first] == [second_event["event_id"]]
        after_sequence = repository.list_events(run_id, after_sequence=1)
        assert [event["event_id"] for event in after_sequence] == [second_event["event_id"]]

        clear = client.post(f"/api/transactions/{run_id}/clear")
        assert clear.status_code == 200
        assert clear.json()["status"] == "hidden"

        visible = client.get("/api/transactions?workflow_type=release_note_creation")
        assert visible.status_code == 200
        assert visible.json()["transactions"] == []

        hidden = client.get(
            "/api/transactions?workflow_type=release_note_creation&include_hidden=true"
        )
        assert hidden.status_code == 200
        hidden_rows = hidden.json()["transactions"]
        assert [item["run_id"] for item in hidden_rows] == [run_id]
        assert hidden_rows[0]["hidden_at"] is not None


def test_release_note_graph_has_artifact_publisher_wired(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        assert client.app.state.artifact_publisher is not None
        assert (
            client.app.state.release_note_graph.artifact_publisher
            is client.app.state.artifact_publisher
        )
