from __future__ import annotations

import importlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings


TAB_SLUGS = [
    "overview",
    "release-delta",
    "dependency-graph",
    "policy",
    "dry-run",
    "rollback",
    "drift",
    "mop-replay",
    "runtime-behavior",
    "release-note-validation",
    "audit",
]


def build_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'phase3.db'}")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("SECRET_KEY", "phase3-secret")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTER", "disabled")
    monkeypatch.setenv("ARTIFACT_STORAGE_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DIGITAL_TWIN_MOCK_ENABLED", "true")
    monkeypatch.setenv("DIGITAL_TWIN_MOCK_DELAY_MS", "0")
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "mock_server")
    get_settings.cache_clear()
    import backend.app.main as main_module

    main_module = importlib.reload(main_module)
    return TestClient(main_module.create_app())


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def test_mock_gateway_requires_auth_and_is_server_marked(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/digital-twins").status_code == 401
        login(client)

        config = client.get("/api/digital-twins/config")
        assert config.status_code == 200
        assert config.headers["x-esda-data-mode"] == "mock_server"
        assert config.json() == {
            "schema_version": "1.0.0",
            "data_mode": "mock_server",
            "enabled": True,
            "non_production": True,
            "fixture_version": "1.0.0",
        }


def test_list_filters_sort_and_cursor_pagination(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        first = client.get(
            "/api/digital-twins",
            params={"decision": "amber", "namespace": "agent-testing", "limit": 2},
        )
        assert first.status_code == 200
        payload = first.json()
        assert payload["schema_version"] == "1.0.0"
        assert payload["page"]["limit"] == 2
        assert len(payload["items"]) <= 2
        assert all(item["decision"] == "amber" for item in payload["items"])
        assert all(item["target"]["namespace"] == "agent-testing" for item in payload["items"])

        paged = client.get("/api/digital-twins", params={"limit": 3})
        assert paged.status_code == 200
        page = paged.json()["page"]
        assert page["result_count"] == 13
        assert page["next_cursor"] == "cursor_3"
        second = client.get("/api/digital-twins", params={"limit": 3, "cursor": page["next_cursor"]})
        assert second.status_code == 200
        assert second.json()["page"]["offset"] == 3
        assert second.json()["page"]["previous_cursor"] == "cursor_0"


def test_all_frozen_tabs_and_named_endpoints_are_served(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        twin_id = client.get("/api/digital-twins", params={"limit": 1}).json()["items"][0]["twin_id"]
        detail = client.get(f"/api/digital-twins/{twin_id}")
        assert detail.status_code == 200
        assert detail.headers["x-esda-data-mode"] == "mock_server"

        for slug in TAB_SLUGS:
            tab = client.get(f"/api/digital-twins/{twin_id}/tabs/{slug}")
            assert tab.status_code == 200, slug
            assert tab.json()["state"] in {
                "loading",
                "available",
                "empty",
                "not_run",
                "not_available",
                "failed",
                "stale",
            }

        for endpoint in [
            "summary",
            "delta",
            "graph",
            "policy",
            "dry-run",
            "rollback",
            "drift",
            "replay",
            "runtime-risk",
            "release-note-validation",
            "audit",
            "report",
            "events",
            "safe-explanation",
        ]:
            response = client.get(f"/api/digital-twins/{twin_id}/{endpoint}")
            assert response.status_code == 200, endpoint
            assert response.headers["x-esda-data-mode"] == "mock_server"


def test_generation_progress_history_and_reset(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        scenarios = client.get("/api/digital-twins/scenarios").json()
        created = client.post("/api/digital-twins", json={"scenario_id": scenarios[0]["id"]})
        assert created.status_code == 200
        twin = created.json()
        assert twin["twin_id"].startswith("twin_mock_")
        assert twin["lifecycle_status"] == "requested"
        assert client.get("/api/digital-twins/active").json()["twin_id"] == twin["twin_id"]

        for _ in range(4):
            progressed = client.post(f"/api/digital-twins/{twin['twin_id']}/advance")
            assert progressed.status_code == 200
            twin = progressed.json()
        assert twin["decision"] == "green"
        assert twin["decision_is_final"] is True
        assert client.get("/api/digital-twins/active").json() is None
        assert len(client.get("/api/digital-twins/history").json()) == 1

        cleared = client.delete("/api/digital-twins/history")
        assert cleared.status_code == 200
        assert cleared.json()["cleared"] is True
        assert client.get("/api/digital-twins/history").json() == []
        assert client.get("/api/digital-twins").json()["page"]["result_count"] == 13


def test_approval_and_regeneration_state_transitions(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        amber = client.get("/api/digital-twins", params={"decision": "amber", "limit": 1}).json()["items"][0]
        requested = client.post(f"/api/digital-twins/{amber['twin_id']}/approval/request", json={})
        assert requested.status_code == 200
        assert requested.json()["relationships"]["approval_status"] == "pending"
        approved = client.post(f"/api/digital-twins/{amber['twin_id']}/approval/approve", json={})
        assert approved.status_code == 200
        assert approved.json()["relationships"]["approval_status"] == "approved"

        regenerated = client.post(f"/api/digital-twins/{amber['twin_id']}/regenerate")
        assert regenerated.status_code == 200
        assert regenerated.json()["prior_decision"]["twin_id"] == amber["twin_id"]
        prior = client.get(f"/api/digital-twins/{amber['twin_id']}").json()
        assert prior["decision"] == "superseded"
        assert prior["freshness"]["superseded_by"] == regenerated.json()["twin_id"]


def test_fault_injection_returns_typed_error_bodies(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        failed = client.get("/api/digital-twins", params={"mock_state": "failed"})
        assert failed.status_code == 503
        assert failed.headers["x-esda-data-mode"] == "mock_server"
        assert failed.json()["error"]["code"] == "mock_service_unavailable"
        assert failed.json()["error"]["retryable"] is True

        timed_out = client.get("/api/digital-twins", params={"mock_state": "timeout"})
        assert timed_out.status_code == 504
        assert timed_out.json()["error"]["code"] == "mock_timeout"

        missing = client.get("/api/digital-twins/twin_does_not_exist")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "twin_not_found"

        invalid_cursor = client.get("/api/digital-twins", params={"cursor": "bad"})
        assert invalid_cursor.status_code == 422
        assert invalid_cursor.json()["error"]["code"] == "invalid_cursor"


def test_page_routes_select_http_adapter_and_preserve_server_urls(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        list_page = client.get("/digital-twins?decision=amber")
        assert list_page.status_code == 200
        assert 'data-adapter-mode="mock_server"' in list_page.text
        assert 'data-host-path="/digital-twins"' in list_page.text
        assert "decision=amber" in list_page.text

        twin_id = client.get("/api/digital-twins", params={"limit": 1}).json()["items"][0]["twin_id"]
        detail_page = client.get(f"/digital-twins/{twin_id}?tab=policy")
        assert detail_page.status_code == 200
        assert f'data-host-path="/digital-twins/{twin_id}"' in detail_page.text
        assert "tab=policy" in detail_page.text


def test_mock_gateway_is_never_effective_in_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DIGITAL_TWIN_MOCK_ENABLED", "true")
    settings = Settings(_env_file=None)
    assert settings.digital_twin_mock_enabled is True
    assert settings.digital_twin_mock_effective_enabled is False

    monkeypatch.setenv("APP_ENV", "local-ingress")
    local_settings = Settings(_env_file=None)
    assert local_settings.digital_twin_mock_effective_enabled is True


def test_runtime_adapter_is_http_backed_and_real_integrations_are_absent() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    adapter = (repo_root / "backend/app/static/digital-twin/twin-http-adapter.js").read_text(encoding="utf-8")
    gateway = (repo_root / "backend/app/digital_twin_mock.py").read_text(encoding="utf-8")
    fixture = json.loads(
        (repo_root / "backend/app/fixtures/digital_twin/v1/server-fixtures.json").read_text(encoding="utf-8")
    )
    assert "function HttpTwinAdapter" in adapter
    assert "AbortController" in adapter
    assert "maxGetRetries = 1" in adapter
    assert "/api/digital-twins" in adapter
    assert len(fixture["twins"]) == 13
    assert set(fixture["tab_slugs"]) == set(TAB_SLUGS)
    for forbidden in ["AzureGpt5Service", "RunRepository", "MopExecutionAgentClient", "K8s", "Helm"]:
        assert forbidden not in gateway
