from __future__ import annotations

import importlib
from copy import deepcopy
from pathlib import Path

from sqlalchemy import select

from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.tools.mop_execution_agent import MopExecutionAgentResponse
from backend.app.db.models import DigitalTwinExplanationLog


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


CORE_TWIN.update(
    {
        "visible_lifecycle": "awaiting_dry_run",
        "risk": {"level": "preliminary", "score": None},
        "autonomy_eligibility": "pending",
        "recommended_action": "Attach authoritative dry-run evidence.",
        "freshness": {
            "status": "fresh",
            "captured_at": CORE_TWIN["updated_at"],
            "expires_at": None,
            "superseded_by": None,
            "message": "Persisted facts are fresh.",
        },
        "target": {
            "cluster_id": "contract-cluster",
            "cluster_name": "contract-cluster",
            "namespace": "sample-target",
        },
        "bundle": {
            "bundle_id": "sha256:input",
            "bundle_name": "sample-bundle",
            "bundle_hash": "sha256:bundle",
            "release_version": "1.0.0",
        },
        "created_by": "admin",
        "created_by_display": "admin",
        "relationships": {
            "dry_run_job_id": None,
            "approval_id": None,
            "approval_status": "not_required",
            "execution_id": None,
            "execution_status": "unlinked",
        },
        "top_reasons": [
            {
                "code": "AUTHORITATIVE_DRY_RUN_PENDING",
                "summary": "A final decision requires authoritative dry-run evidence.",
                "severity": "review",
                "tab_slug": "dry-run",
            }
        ],
        "preliminary_summary": {
            "status": "preliminary",
            "headline": "Twin is awaiting dry-run evidence.",
            "observations": ["A final decision requires authoritative dry-run evidence."],
            "deterministic": True,
        },
        "final_summary": None,
    }
)


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
        self.release_delta_params: dict = {}

    async def create_namespace_twin(self, payload: dict) -> MopExecutionAgentResponse:
        created = deepcopy(self.twin)
        created["target_namespace"] = payload["target_namespace"]
        created["target_cluster"] = payload["target_cluster"]
        created["target_namespace"] = payload["target_namespace"]
        created["target"] = {
            "cluster_id": payload["target_cluster"],
            "cluster_name": payload["target_cluster"],
            "namespace": payload["target_namespace"],
        }
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

    async def get_namespace_twin_overview(self, twin_id: str) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/overview",
            {
                "schema_version": "1.0.0",
                "twin_id": twin_id,
                "decision_version": self.twin["decision_version"],
                "state": "available",
                "kind": "overview",
                "title": "Overview",
                "summary": self.twin["preliminary_summary"]["headline"],
                "metrics": [],
                "reasons": [],
                "recommended_action": self.twin["recommended_action"],
                "preliminary_summary": self.twin["preliminary_summary"],
                "final_summary": None,
                "risk": self.twin["risk"],
                "freshness": self.twin["freshness"],
                "actions": self.twin["actions"],
                "relationships": self.twin["relationships"],
                "fact_envelope": {"decision": "pending"},
            },
        )

    async def get_namespace_twin_release_delta(
        self, twin_id: str, params: dict | None = None
    ) -> MopExecutionAgentResponse:
        assert twin_id == self.twin["twin_id"]
        self.release_delta_params = deepcopy(params or {})
        return _response(
            "GET",
            f"v1/namespace-twins/{twin_id}/release-delta",
            {
                "schema_version": "1.0.0",
                "twin_id": twin_id,
                "decision_version": self.twin["decision_version"],
                "lifecycle_status": self.twin["lifecycle_status"],
                "freshness": self.twin["freshness"],
                "availability": {
                    "state": "available",
                    "message": "Authoritative canonical Release Delta facts are available.",
                },
                "data": {
                    "summary": {
                        "total": 1,
                        "create": 0,
                        "update": 1,
                        "explicit_delete": 0,
                        "no_op": 0,
                        "unknown": 0,
                        "immutable_conflict": 0,
                        "namespace_rewrite": 0,
                    },
                    "changes": [
                        {
                            "change_id": "delta_real_1",
                            "resource_identity": "v1:ConfigMap:sample-target:sample-app",
                            "api_version": "v1",
                            "kind": "ConfigMap",
                            "namespace": "sample-target",
                            "name": "sample-app",
                            "helm_release": None,
                            "action": "update",
                            "current_summary": "kind=ConfigMap",
                            "planned_summary": "kind=ConfigMap",
                            "risk": "low",
                            "reason": "Canonical intent differs at data.mode.",
                            "canonical_diff": "{\"current\":{},\"planned\":{},\"field_changes\":[]}",
                            "evidence_refs": [
                                {
                                    "evidence_id": "evidence_bundle",
                                    "source_type": "bundle",
                                    "summary": "generated/configmap.yaml",
                                    "captured_at": self.twin["updated_at"],
                                    "redacted": True,
                                }
                            ],
                            "redacted": True,
                        }
                    ],
                    "page": {
                        "limit": int((params or {}).get("limit") or 25),
                        "has_more": False,
                        "next_cursor": None,
                        "result_count": 1,
                    },
                    "artifacts": [],
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


async def _safe_llm_response(self, **kwargs) -> dict:
    return {
        "summary": "SIGMA explains the persisted preliminary twin facts.",
        "decision": "red",
        "actions": [{"code": "unsafe_override", "enabled": True}],
        "model_profile": kwargs.get("model_profile"),
        "prompt_version": "namespace_twin_overview_explanation_v1",
        "prompt_hash": "model-supplied-hash-is-not-authoritative",
        "token_usage": {"total_tokens": 42},
        "llm_fallback": {"used": False, "error": None},
    }


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

    monkeypatch.setattr(main_module.AzureGpt5Service, "structured_response", _safe_llm_response)

    monkeypatch.setattr(gateway_module, "MopExecutionAgentClient", FakeNamespaceTwinClient)
    main_module = importlib.reload(main_module)
    return TestClient(main_module.create_app())


def login(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200


def test_real_gateway_requires_auth_and_projects_execution_agent_core(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/digital-twins").status_code == 401
        login(client)

        config = client.get("/api/digital-twins/config")
        listed = client.get("/api/digital-twins")
        detail = client.get(f"/api/digital-twins/{CORE_TWIN['twin_id']}")
        overview = client.get(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/overview",
            params={"model_profile": "azure_gpt5_pro"},
        )
        with client.app.state.database.session() as session:
            explanation_logs = list(session.scalars(select(DigitalTwinExplanationLog)))

    assert config.status_code == 200
    assert config.headers["x-esda-data-mode"] == "real_core"
    assert config.json()["label"] == (
        "Real Lifecycle + Overview + Release Delta + Mock Remaining Modules"
    )
    assert listed.json()["items"][0]["data_mode"] == "real_core"
    assert listed.json()["warning"].startswith(
        "Lifecycle, Overview, Release Delta, summaries"
    )
    assert detail.json()["lifecycle_status"] == "awaiting_dry_run"
    assert detail.json()["decision_is_final"] is False
    safe_explanation = overview.json()["safe_explanation"]
    assert safe_explanation["content"].startswith("SIGMA explains")
    assert safe_explanation["status"] == "generated"
    assert safe_explanation["chain_of_thought_included"] is False
    assert "decision" not in safe_explanation
    assert "actions" not in safe_explanation
    assert safe_explanation["prompt_hash"] != "model-supplied-hash-is-not-authoritative"
    assert explanation_logs[0].explanation_id == safe_explanation["explanation_id"]
    assert explanation_logs[0].safe_output_json == safe_explanation
    assert len(explanation_logs) == 1


def test_real_create_events_cancel_and_invalid_browser_scenario_are_typed(
    tmp_path, monkeypatch
) -> None:
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
        list_script = client.get("/static/digital-twin/digital-twins-page.js")
        detail_script = client.get("/static/digital-twin/digital-twin-detail-page.js")
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

    assert "return Array.isArray(item.actions)" in detail_script.text
    assert "adapter.getTwin(item.twin_id)" in detail_script.text
    assert "tab.safe_explanation.content" in detail_script.text
    assert "limit: 25" in list_script.text
    assert "if (realCore)" in list_script.text
    assert "load({ silent: true })" in list_script.text
    assert "adapter.advanceGeneration(item.twin_id)" in list_script.text


def test_real_audit_history_cannot_be_cleared(tmp_path, monkeypatch) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.delete("/api/digital-twins/history")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "durable_history_not_clearable"
def test_release_delta_is_authoritative_filterable_and_audit_logged(
    tmp_path, monkeypatch
) -> None:
    with build_client(tmp_path, monkeypatch) as client:
        login(client)
        response = client.get(
            f"/api/digital-twins/{CORE_TWIN['twin_id']}/tabs/release-delta",
            params={
                "action": "update",
                "risk": "low",
                "kind": "ConfigMap",
                "limit": 25,
                "model_profile": "azure_gpt5_pro",
            },
        )
        forwarded = client.app.state.digital_twin_gateway.client.release_delta_params
        with client.app.state.database.session() as session:
            explanation_logs = list(session.scalars(select(DigitalTwinExplanationLog)))
        detail_script = client.get("/static/digital-twin/digital-twin-detail-page.js").text
        adapter_script = client.get("/static/digital-twin/twin-http-adapter.js").text

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "available"
    assert payload["kind"] == "delta"
    assert payload["data_mode"] == "real_core"
    assert payload["module_mode"] == "authoritative"
    assert payload["non_authoritative"] is False
    assert payload["data"]["summary"]["update"] == 1
    assert payload["data"]["changes"][0]["resource_identity"].endswith(":sample-app")
    assert payload["safe_explanation"]["chain_of_thought_included"] is False
    assert payload["safe_explanation"]["prompt_version"] == (
        "namespace_twin_release_delta_explanation_v1"
    )
    assert forwarded == {
        "action": "update",
        "risk": "low",
        "kind": "ConfigMap",
        "limit": "25",
    }
    assert explanation_logs[0].prompt_version == "namespace_twin_release_delta_explanation_v1"
    assert "tabData.changes" in detail_script
    assert "data-delta-filter" in detail_script
    assert "query = Object.assign" in adapter_script
