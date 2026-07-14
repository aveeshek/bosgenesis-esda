from __future__ import annotations

import importlib
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.tools.mop_execution_agent import MopExecutionAgentResponse


CORE_TWIN = {
    "schema_version": "1.0.0",
    "twin_id": "twin_real_phase4",
    "display_name": "sample-bundle -> sample-target",
    "decision_version": 1,
    "decision": "pending",
    "decision_is_final": False,
    "lifecycle_status": "awaiting_dry_run",
    "target_cluster": "contract-cluster",
    "target_namespace": "sample-target",
    "bundle_name": "sample-bundle",
    "bundle_hash": "sha256:bundle",
    "input_hash": "sha256:input",
    "release_version": "1.0.0",
    "actor_id": "admin",
    "created_at": "2026-07-14T12:00:00+00:00",
    "updated_at": "2026-07-14T12:00:01+00:00",
    "expires_at": None,
    "superseded_by": None,
    "actions": [
        {"code": "open_twin", "label": "Open Twin", "visible": True, "enabled": True},
        {
            "code": "cancel_generation",
            "label": "Cancel generation",
            "visible": True,
            "enabled": True,
        },
    ],
    "facts": {
        "provisional": True,
        "module_modes": {"policy": "mock_non_authoritative"},
    },
}


def _response(method: str, path: str, data: dict) -> MopExecutionAgentResponse:
    return MopExecutionAgentResponse(
        method=method,
        url=f"http://execution-agent/{path}",
        status_code=200,
        payload={"ok": True, "data": deepcopy(data), "data_mode": "real_core"},
    )


class FakeNamespaceTwinClient:
    def __init__(self, *args, **kwargs) -> None:
        self.twin = deepcopy(CORE_TWIN)

    async def create_namespace_twin(self, payload: dict) -> MopExecutionAgentResponse:
        created = deepcopy(self.twin)
        created["target_namespace"] = payload["target_namespace"]
        created["target_cluster"] = payload["target_cluster"]
        return _response("POST", "v1/namespace-twins", created)

    async def list_namespace_twins(self, params: dict | None = None) -> MopExecutionAgentResponse:
        return _response(
            "GET",
            "v1/namespace-twins",
            {
                "items": [self.twin],
                "page": {
                    "limit": int((params or {}).get("limit") or 25),
                    "offset": int((params or {}).get("offset") or 0),
                    "result_count": 1,
                    "has_more": False,
                    "next_offset": None,
                },
            },
        )

    async def get_namespace_twin(self, twin_id: str) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        return _response("GET", f"v1/namespace-twins/{twin_id}", self.twin)

    async def get_namespace_twin_events(
        self, twin_id: str, params: dict | None = None
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/events",
            {
                "events": [
                    {
                        "event_id": "evt_1",
                        "sequence": 1,
                        "event_type": "twin_requested",
                        "message": "Twin generation requested.",
                        "payload": {"target_namespace": "sample-target"},
                    },
                    {
                        "event_id": "evt_2",
                        "sequence": 2,
                        "event_type": "twin_generating",
                        "message": "Bundle facts extracted.",
                        "payload": {},
                    },
                ],
                "page": {"has_more": False},
            },
        )

    async def cancel_namespace_twin(self, twin_id: str) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        self.twin["lifecycle_status"] = "cancelled"
        self.twin["decision"] = "cancelled"
        self.twin["decision_is_final"] = True
        return _response("POST", f"v1/namespace-twins/{twin_id}/cancel", self.twin)


def build_client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'phase4-esda.db'}")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.setenv("SECRET_KEY", "phase4-secret")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINTER", "disabled")
    monkeypatch.setenv("ARTIFACT_STORAGE_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DIGITAL_TWIN_BACKEND_MODE", "real_core")
    monkeypatch.setenv("DIGITAL_TWIN_EXECUTION_AGENT_URL", "http://execution-agent")
    get_settings.cache_clear()
    import backend.app.digital_twin_gateway as gateway_module
    import backend.app.main as main_module

    monkeypatch.setattr(gateway_module, "MopExecutionAgentClient", FakeNamespaceTwinClient)
    main_module = importlib.reload(main_module)
    return TestClient(main_module.create_app())


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def test_real_gateway_requires_auth_and_projects_execution_agent_core(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/digital-twins").status_code == 401
        login(client)

        config = client.get("/api/digital-twins/config")
        listed = client.get("/api/digital-twins")
        detail = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}")

    assert config.status_code == 200
    assert config.headers["x-esda-data-mode"] == "real_core"
    assert config.json()["label"] == "Real Core + Mock Modules"
    assert listed.json()["items"][0]["data_mode"] == "real_core"
    assert listed.json()["warning"].startswith("Lifecycle facts are real")
    assert detail.json()["lifecycle_status"] == "awaiting_dry_run"
    assert detail.json()["decision_is_final"] is False


def test_real_create_events_cancel_and_invalid_browser_scenario_are_typed(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        invalid = client.post("/api/digital-twins", json={"scenario_id": "green-helm"})
        created = client.post(
            "/api/digital-twins",
            json={
                "source": {"type": "local_path", "value": "C:/tmp/sample-bundle"},
                "target_namespace": "sample-target",
                "target_cluster": "contract-cluster",
                "idempotency_key": "phase4-gateway",
            },
        )
        events = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}/events")
        cancelled = client.post(f"/api/digital-twins/{CORE_TWIN['twin_id']}/cancel")

    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "real_bundle_required"
    assert created.status_code == 200
    assert created.json()["target"]["namespace"] == "sample-target"
    assert [event["sequence"] for event in events.json()["events"]] == [1, 2]
    assert cancelled.json()["lifecycle_status"] == "cancelled"


def test_mock_modules_are_labeled_and_cannot_supply_real_actions(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        tab = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/policy")
        actions = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}/actions")
        list_page = client.get("/digital-twins")
        static_page = client.get("/static/digital-twin/digital-twins.html")
        adapter = client.get("/static/digital-twin/twin-http-adapter.js")

    assert tab.status_code == 200
    assert tab.json()["data_mode"] == "mock_module"
    assert tab.json()["non_authoritative"] is True
    assert tab.json()["summary"].startswith("Mock / non-authoritative module preview")
    assert {action["code"] for action in actions.json()} == {
        "open_twin",
        "cancel_generation",
    }
    assert 'data-adapter-mode="real_core"' in list_page.text
    assert "Real Core + Mock Modules" in static_page.text or "data-mode-marker" in static_page.text
    assert '["mock_server", "real_core"]' in adapter.text


def test_real_audit_history_cannot_be_cleared(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.delete("/api/digital-twins/history")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "durable_history_not_clearable"
