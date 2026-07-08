import asyncio
import json

import httpx

from backend.app.config import Settings
from backend.app.mop_execution import MopExecutionRunRequest, WORKFLOW_TYPE
from backend.app.tools.mop_execution_agent import MopExecutionAgentClient
from backend.tests.test_phase1_app import build_test_client


def test_mop_execution_settings_and_app_wiring(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing,qa-lab")
    monkeypatch.setenv("MOP_EXECUTION_DEFAULT_TARGET_NAMESPACE", "qa-lab")

    with build_test_client(tmp_path, monkeypatch) as client:
        settings = client.app.state.settings
        assert settings.mop_execution_allowed_target_namespace_list == ["agent-testing", "qa-lab"]
        assert settings.mop_execution_default_target_namespace == "qa-lab"
        assert client.app.state.mop_execution_agent.configured is True
        assert client.app.state.mop_execution_store.allowed_target_namespaces() == ["agent-testing", "qa-lab"]


def test_mop_execution_agent_client_maps_rest_calls_and_redacts_payload() -> None:
    captured = {"paths": []}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode()) if request.content else {}
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["paths"].append(request.url.path)
        captured["headers"] = dict(request.headers)
        captured["payload"] = payload
        return httpx.Response(
            200,
            json={
                "valid": True,
                "bundle_id": "bundle_123",
                "echo": payload,
            },
        )

    client = MopExecutionAgentClient(
        Settings(
            mop_execution_agent_url="http://mop-execution.local",
            mop_execution_agent_api_key="secret-api-key",
            mop_execution_agent_auth_header="authorization",
        ),
        transport=httpx.MockTransport(handler),
    )

    registration = asyncio.run(
        client.register_bundle(
            {
                "source": {"type": "object_store", "value": "https://raw.githubusercontent.com/org/repo/main/mop-bundle.zip"},
                "target_namespace": "agent-testing",
                "api_key": "must-not-persist",
            }
        )
    )
    response = asyncio.run(
        client.validate_bundle(
            {
                "bundle_id": "bundle_123",
                "source": {"type": "object_store", "value": "https://raw.githubusercontent.com/org/repo/main/mop-bundle.zip"},
                "target_namespace": "agent-testing",
                "api_key": "must-not-persist",
            }
        )
    )

    assert captured["method"] == "POST"
    assert captured["paths"] == ["/v1/artifact-bundles", "/v1/artifact-bundles/bundle_123/validate"]
    assert captured["headers"]["authorization"] == "Bearer secret-api-key"
    assert registration.payload["bundle_id"] == "bundle_123"
    assert response.payload["bundle_id"] == "bundle_123"
    assert response.redacted_request["json"]["api_key"] == "***"
    assert response.redacted_response["echo"]["api_key"] == "***"
    assert client.report_download_url(job_id="job 1", report_id="dry/run", artifact="pdf").endswith(
        "/v1/execution-jobs/job%201/reports/dry%2Frun/download?artifact=pdf"
    )


def test_mop_execution_agent_client_falls_back_to_mcp_for_approval_500() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/approvals"):
            return httpx.Response(500, json={"message": "Internal Server Error"})
        if request.url.path == "/mcp":
            payload = json.loads(request.content.decode())
            captured["payload"] = payload
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"accepted": True, "status": "approved", "job_id": "job 1"}),
                            }
                        ]
                    },
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    client = MopExecutionAgentClient(
        Settings(mop_execution_agent_url="http://mop-execution.local"),
        transport=httpx.MockTransport(handler),
    )

    response = asyncio.run(client.submit_approval("job 1", {"approval_id": "approval_1"}))

    assert response.method == "MCP"
    assert response.payload["accepted"] is True
    assert captured["payload"]["params"]["name"] == "mop_execution_submit_approval"
    assert captured["payload"]["params"]["arguments"]["job_id"] == "job 1"
    assert captured["payload"]["params"]["arguments"]["approval"]["approval_id"] == "approval_1"

def test_mop_execution_store_persists_redacted_metadata_and_activity_projection(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        store = client.app.state.mop_execution_store

        created = store.create_execution_run(
            MopExecutionRunRequest(
                user_id=user_id,
                bundle_source={"run_id": "mop_run_1", "secret": "bundle-secret"},
                target_namespace="agent-testing",
                execution_mode="dry_run_then_approval",
                model_profile={"profile_id": "azure_gpt5_pro", "label": "SIGMA 5 PRO"},
                operator="admin",
            )
        )
        run_id = created["run_id"]
        store.record_preflight(run_id=run_id, passed=True, checks={"required_files": "ok"})
        store.record_agent_health(run_id=run_id, healthy=True, response={"status": "ok"})
        store.record_bundle_validation(run_id=run_id, validation={"valid": True, "bundle_id": "bundle_123"})
        store.record_job_created(run_id=run_id, job={"job_id": "dry_123", "state": "created"}, job_kind="dry_run")
        store.record_job_started(run_id=run_id, job_id="dry_123", phase="dry_run", response={"state": "running"})
        store.record_job_state(run_id=run_id, job={"state": "waiting_for_approval", "current_phase": "dry_run"})
        store.record_policy_decision(run_id=run_id, decision={"decision": "allow", "token": "raw-token"})
        store.record_approval(
            run_id=run_id,
            approval={"approval_id": "approval_1", "approved_by": "admin"},
            response={"status": "accepted"},
        )
        store.record_reports(run_id=run_id, reports={"report_type": "dry-run", "download_url": "/reports/dry.pdf"})
        store.record_safe_summary(run_id=run_id, stage="dry_run", summary="Dry-run completed and is waiting for approval.")

        metadata = store.execution_metadata(run_id)
        assert metadata["bundle_id"] == "bundle_123"
        assert metadata["dry_run_job_id"] == "dry_123"
        assert metadata["target_namespace"] == "agent-testing"
        assert metadata["current_state"] == "waiting_for_approval"
        assert metadata["reports"][0]["report_type"] == "dry-run"
        assert metadata["policy_decisions"][0]["token"] == "***"
        assert metadata["safe_summaries"][0]["summary"].startswith("Dry-run completed")

        detail = client.app.state.activity_service.get_activity_detail(run_id)
        assert detail is not None
        assert detail["node"]["workflow_type"] == WORKFLOW_TYPE
        assert detail["node"]["workflow_badge"] == "EXEC"
        assert detail["artifact_actions"]["actions"]["bundle"]["label"] == "Download Execution Reports"
        stage_status = {stage["id"]: stage["status"] for stage in detail["stages"]}
        assert stage_status["bundle_validate"] == "success"
        assert stage_status["dry_run"] == "success"
        assert stage_status["approval"] == "success"

        first_event = client.app.state.repository.list_events(run_id)[0]
        assert first_event["payload"]["bundle_source"]["secret"] == "***"
