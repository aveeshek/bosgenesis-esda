import importlib

from fastapi.testclient import TestClient

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

def test_llm_model_profiles_endpoint_requires_auth_and_lists_profiles(tmp_path, monkeypatch) -> None:
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
        assert {"azure_gpt5_pro", "azure_gpt41_mini", "ollama_llama70b", "ollama_gemma4"}.issubset(profile_ids)

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
