import asyncio
from contextlib import contextmanager
import importlib
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.app.artifact_publisher import ArtifactPublishPayload
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
    monkeypatch.setenv("ARTIFACT_STORAGE_DIR", str(tmp_path / "artifacts"))
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


def test_phase1_signed_session_survives_transient_database_outage(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        import backend.app.dependencies as dependencies

        monkeypatch.setattr(dependencies, "_auth_database_retry_after", 0.0)

        @contextmanager
        def unavailable_database():
            raise OperationalError("SELECT user", {}, TimeoutError("database offline"))
            yield

        monkeypatch.setattr(client.app.state.database, "session", unavailable_database)

        page = client.get("/digital-twins")
        assert page.status_code == 200
        assert "Digital Twins" in page.text

        authenticated_api = client.get("/api/auth/me")
        assert authenticated_api.status_code == 200
        assert authenticated_api.json()["user"]["username"] == "admin"


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
        assert "SIGMA 5 PRO" in response.text
        assert "TRAINIUM BEHEMOTH" in response.text
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

def test_clear_transactions_filters_by_workflow(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        repository = client.app.state.repository

        repository.create_run(
            run_id="mop_execution_clear_all_1",
            user_id=user_id,
            goal="Execute MoP bundle",
            target_url=None,
            namespace="agent-testing",
            workflow_type="mop_execution",
        )
        repository.create_run(
            run_id="mop_execution_clear_all_2",
            user_id=user_id,
            goal="Cleanup MoP bundle",
            target_url=None,
            namespace="agent-testing",
            workflow_type="mop_execution",
        )
        repository.create_run(
            run_id="release_note_survives_clear_all",
            user_id=user_id,
            goal="Generate release notes for https://github.com/example/repo",
            target_url="https://github.com/example/repo",
            namespace=None,
            workflow_type="release_note_creation",
        )

        clear = client.post("/api/transactions/clear?workflow_type=mop_execution")
        assert clear.status_code == 200
        assert clear.json()["cleared"] == 2

        mop_visible = client.get("/api/transactions?workflow_type=mop_execution")
        assert mop_visible.status_code == 200
        assert mop_visible.json()["transactions"] == []

        release_visible = client.get("/api/transactions?workflow_type=release_note_creation")
        assert release_visible.status_code == 200
        assert [item["run_id"] for item in release_visible.json()["transactions"]] == [
            "release_note_survives_clear_all"
        ]

        mop_hidden = client.get("/api/transactions?workflow_type=mop_execution&include_hidden=true")
        assert mop_hidden.status_code == 200
        assert {item["run_id"] for item in mop_hidden.json()["transactions"]} == {
            "mop_execution_clear_all_1",
            "mop_execution_clear_all_2",
        }

def test_release_note_graph_has_artifact_publisher_wired(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        assert client.app.state.artifact_publisher is not None
        assert (
            client.app.state.release_note_graph.artifact_publisher
            is client.app.state.artifact_publisher
        )


def test_activity_page_renders_timeline_shell(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/activity")

        assert response.status_code == 200
        assert "Activity Timeline" in response.text
        assert 'id="activity-graph"' in response.text
        assert 'id="activity-status-filter"' in response.text
        assert 'id="activity-workflow-filter"' in response.text
        assert 'id="activity-model-filter"' not in response.text
        assert 'id="activity-repo-filter"' not in response.text
        assert 'id="activity-chat-canvas"' in response.text
        assert 'activity-selected-chips' not in response.text
        assert 'activity-chat-context' not in response.text
        assert 'id="activity-stage-chain"' in response.text
        assert "Artifact Chat" in response.text
        assert 'href="/activity"' in response.text
        assert 'src="/static/js/activity.js' in response.text
        assert 'activity-mop-integration-1' in response.text
        assert 'transaction-sidebar-toggle' not in response.text


def test_digital_twins_page_is_authenticated_and_renders_browser_mock(
    tmp_path, monkeypatch
) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        unauthenticated = client.get("/digital-twins", follow_redirects=False)
        assert unauthenticated.status_code == 303
        assert unauthenticated.headers["location"] == "/login"

        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/digital-twins")
        assert response.status_code == 200
        assert 'href="/digital-twins"' in response.text
        assert ">Digital Twins</a>" in response.text
        assert 'class="app-nav-link is-active"' in response.text
        assert 'id="digital-twins-frame"' in response.text
        assert 'src="/static/digital-twin/digital-twins.html"' in response.text
        assert 'transaction-sidebar-toggle' not in response.text

        mock = client.get("/static/digital-twin/digital-twins.html")
        assert mock.status_code == 200
        assert "Release Safety Intelligence" in mock.text
        assert 'id="twin-list-body"' in mock.text
        assert 'src="fixtures/v1/twin-fixtures.js"' in mock.text
        assert '<header class="app-header">' not in mock.text

def test_activity_release_note_timeline_api_and_detail(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        repository = client.app.state.repository
        run_id = "run_activity_test"

        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Generate release notes for https://github.com/aveeshek/bosgenesis-mop-creation-agent",
            target_url="https://github.com/aveeshek/bosgenesis-mop-creation-agent",
            namespace=None,
            workflow_type="release_note_creation",
        )
        repository.add_event(
            run_id,
            "run_started",
            "Release-note generation started",
            {
                "github_url": "https://github.com/aveeshek/bosgenesis-mop-creation-agent",
                "model_profile": {"profile_id": "azure_gpt5_pro", "label": "SIGMA 5 PRO", "short_label": "SIGMA 5 PRO"},
            },
        )
        repository.add_event(run_id, "workflow_classified", "Workflow classified", {"workflow_type": "release_note_creation"})
        repository.add_event(run_id, "plan_created", "Release-note plan created", {"release_name": "v0.0.1"})
        repository.add_event(run_id, "repo_clone_started", "Downloading repository", {})
        repository.add_event(run_id, "repo_clone_completed", "Repository download step completed", {"clone": {"status": "success"}})
        repository.add_event(run_id, "vulnerability_scan_completed", "Repository vulnerability scan completed", {"status": "completed", "finding_count": 0})
        repository.add_event(run_id, "quality_scan_completed", "Repository code quality scan completed", {"quality": {"status": "completed", "summary": "No quality issues."}})
        repository.add_event(run_id, "repo_cleanup_completed", "Temporary repository checkout removed", {"cleanup": {"removed": True}})
        repository.add_event(run_id, "draft_started", "Drafting release-note artifact", {})
        repository.add_event(run_id, "validation_completed", "Release-note draft validation completed", {"valid": True})
        repository.add_event(run_id, "recovery_recommendation", "Recovery recommendation: continue", {"action": "continue"})
        markdown_artifact = repository.create_artifact(
            artifact_id="art_activity_md",
            run_id=run_id,
            user_id=user_id,
            artifact_type="release_note",
            title="v0.0.1",
            mime_type="text/markdown; charset=utf-8",
            storage_path="release_note/run_activity_test/release-notes.md",
            metadata={"kind": "markdown"},
        )
        pdf_artifact = repository.create_artifact(
            artifact_id="art_activity_pdf",
            run_id=run_id,
            user_id=user_id,
            artifact_type="release_note_pdf",
            title="v0.0.1 PDF",
            mime_type="application/pdf",
            storage_path="release_note/run_activity_test/release-notes.pdf",
            metadata={"kind": "pdf"},
        )
        storage_root = client.app.state.artifact_service.storage_root
        markdown_path = storage_root / markdown_artifact["storage_path"]
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            "# Release Notes\n\n## Executive Summary\nGenerated activity document.\n\n## Vulnerability Matrix\nNo critical issues.",
            encoding="utf-8",
        )
        pdf_path = storage_root / pdf_artifact["storage_path"]
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4 activity pdf")

        repository.add_event(run_id, "artifact_created", "Release-note Markdown artifact saved", {"artifact": markdown_artifact})
        repository.add_event(run_id, "artifact_created", "Release-note PDF artifact saved", {"artifact": pdf_artifact})
        repository.add_event(
            run_id,
            "artifact_publish_completed",
            "Release-note artifacts published to GitHub artifact repository",
            {"artifact_publish": {"status": "success", "folder_name": "260627_173012_v0.0.1", "commit_hash": "abc123"}},
        )
        repository.add_event(run_id, "artifact_warning", "Legacy publish warning shape", {"artifact_publish": None})
        repository.add_event(
            run_id,
            "run_completed",
            "Release-note generation completed",
            {"artifact_publish": {"status": "success", "folder_name": "260627_173012_v0.0.1"}},
        )
        repository.update_status(run_id, "completed", final_report="# Release Notes")

        list_response = client.get("/api/activity/release-notes?status=published&published=true&model=azure_gpt5_pro")
        assert list_response.status_code == 200
        list_json = list_response.json()
        assert list_json["count"] == 1
        node = list_json["nodes"][0]
        assert node["run_id"] == run_id
        assert node["repository"] == "aveeshek/bosgenesis-mop-creation-agent"
        assert node["visual_status"] == "published"
        assert node["publish_state"]["published"] is True
        assert node["publish_state"]["folder_name"] == "260627_173012_v0.0.1"
        assert node["artifact_summary"]["has_markdown"] is True
        assert node["artifact_summary"]["has_pdf"] is True
        assert node["model_profile"]["label"] == "SIGMA 5 PRO"

        detail_response = client.get(f"/api/activity/release-notes/{run_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        stages = {stage["id"]: stage for stage in detail["stages"]}
        assert stages["classify"]["status"] == "success"
        assert stages["clone"]["status"] == "success"
        assert stages["security"]["status"] == "success"
        assert stages["quality"]["status"] == "success"
        assert stages["publish"]["status"] == "success"
        assert stages["complete"]["status"] == "success"
        artifact_kinds = {artifact["kind"] for artifact in detail["artifacts"]}
        assert artifact_kinds == {"markdown", "pdf"}
        assert detail["artifacts"][0]["download_url"].startswith("/api/artifacts/")
        assert detail["artifact_actions"]["actions"]["markdown"]["enabled"] is True
        assert detail["artifact_actions"]["actions"]["pdf"]["enabled"] is True
        assert detail["artifact_actions"]["actions"]["markdown"]["source"] == "published"

        activity_js = (Path(__file__).parents[1] / "app" / "static" / "js" / "activity.js").read_text(encoding="utf-8")
        assert "Upload Markdown GITHUB" in activity_js
        assert "Upload PDF GITHUB" in activity_js
        assert "Upload MoP Bundle Github" in activity_js

        artifacts_response = client.get(f"/api/activity/release-notes/{run_id}/artifacts")
        assert artifacts_response.status_code == 200
        artifacts_json = artifacts_response.json()
        assert artifacts_json["actions"]["open_repo"]["enabled"] is True
        assert artifacts_json["repo_folder_url"].endswith("/tree/main/260627_173012_v0.0.1")

        download_response = client.get(
            f"/api/activity/release-notes/{run_id}/artifact/markdown/download",
            follow_redirects=False,
        )
        assert download_response.status_code == 307
        assert "raw.githubusercontent.com" in download_response.headers["location"]
        assert download_response.headers["location"].endswith("/release-notes.md")

        chat_response = client.post(
            "/api/activity/chat",
            json={
                "message": "Summarize the vulnerability matrix for this run",
                "selected_run_ids": [run_id],
                "model_profile": "azure_configured",
            },
        )
        assert chat_response.status_code == 200
        chat_json = chat_response.json()
        assert chat_json["session_id"].startswith("chat_")
        assert "aveeshek/bosgenesis-mop-creation-agent" in chat_json["answer"]
        assert chat_json["citations"]

        chat_history = client.get(f"/api/activity/chat/{chat_json['session_id']}")
        assert chat_history.status_code == 200
        messages = chat_history.json()["messages"]
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[1]["payload"]["safe_summary"]

def test_activity_mop_timeline_api_detail_download_and_chat(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        repository = client.app.state.repository
        run_id = "mop_activity_test"

        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Generate MoP for namespace bosgenesis",
            target_url="k8s://kubernetes_generic/bosgenesis",
            namespace="bosgenesis",
            workflow_type="mop_generation",
        )
        repository.add_event(
            run_id,
            "run_started",
            "MoP Generation started",
            {
                "namespace": "bosgenesis",
                "target_environment": "kubernetes_generic",
                "model_profile": {"profile_id": "azure_gpt5_pro", "label": "SIGMA 5 PRO", "short_label": "SIGMA 5 PRO"},
            },
        )
        repository.add_event(run_id, "workflow_classified", "Workflow classified", {"workflow_type": "mop_generation"})
        repository.add_event(run_id, "planning_started", "Creating MoP plan", {})
        repository.add_event(run_id, "plan_created", "MoP Generation plan created", {})
        repository.add_event(run_id, "namespace_validated", "MoP namespace policy check completed", {"valid": True, "namespace": "bosgenesis"})
        repository.add_event(run_id, "k8s_evidence_completed", "Kubernetes evidence completed", {"result": {"status": "success"}})
        repository.add_event(run_id, "helm_evidence_completed", "Helm evidence completed", {"result": {"status": "success"}})
        repository.add_event(run_id, "mop_agent_completed", "MoP creation agent completed", {"result": {"status": "success"}})
        repository.add_event(run_id, "draft_completed", "MoP Markdown draft completed", {"reasoning_summary": "Drafted MoP from collected namespace evidence."})
        repository.add_event(run_id, "validation_completed", "MoP draft validation completed", {"valid": True})
        repository.add_event(run_id, "recovery_recommendation", "Recovery recommendation: continue", {"action": "continue"})
        markdown_artifact = repository.create_artifact(
            artifact_id="art_mop_activity_md",
            run_id=run_id,
            user_id=user_id,
            artifact_type="mop",
            title="MoP - bosgenesis",
            mime_type="text/markdown; charset=utf-8",
            storage_path="mop/mop_activity_test/mop.md",
            metadata={"kind": "markdown"},
        )
        pdf_artifact = repository.create_artifact(
            artifact_id="art_mop_activity_pdf",
            run_id=run_id,
            user_id=user_id,
            artifact_type="mop_pdf",
            title="MoP - bosgenesis PDF",
            mime_type="application/pdf",
            storage_path="mop/mop_activity_test/mop.pdf",
            metadata={"kind": "pdf"},
        )
        bundle_artifact = repository.create_artifact(
            artifact_id="art_mop_activity_bundle",
            run_id=run_id,
            user_id=user_id,
            artifact_type="mop_bundle_zip",
            title="MoP bundle - bosgenesis",
            mime_type="application/zip",
            storage_path="mop/mop_activity_test/mop-bundle.zip",
            metadata={"filename": "mop-bundle.zip", "kind": "bundle"},
        )
        storage_root = client.app.state.artifact_service.storage_root
        markdown_path = storage_root / markdown_artifact["storage_path"]
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            "# Method of Procedure: bosgenesis\n\n## Scope\nClone the source namespace.\n\n## Validation Plan\nRead-only checks.",
            encoding="utf-8",
        )
        pdf_path = storage_root / pdf_artifact["storage_path"]
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(b"%PDF-1.4 mop pdf")
        bundle_path = storage_root / bundle_artifact["storage_path"]
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path, "w") as archive:
            archive.writestr("mop.md", markdown_path.read_text(encoding="utf-8"))
            archive.writestr("mop.pdf", b"%PDF-1.4 mop pdf")
            archive.writestr("artifact.json", "{}")
            archive.writestr(
                "deployment-artifacts/kubernetes-manifests/raw/signoz-configmap.yaml",
                "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: signoz-dashboard-config\n  namespace: signoz\ndata:\n  enabled: 'true'\n",
            )
        repository.add_event(run_id, "artifact_created", "MoP Markdown artifact saved", {"artifact": markdown_artifact})
        repository.add_event(run_id, "artifact_created", "MoP PDF artifact saved", {"artifact": pdf_artifact})
        repository.add_event(run_id, "artifact_bundle_created", "MoP bundle artifact saved", {"bundle_artifact": bundle_artifact})
        repository.add_event(
            run_id,
            "artifact_publish_completed",
            "MoP artifacts exported to Github",
            {"artifact_publish": {"status": "success", "folder_name": "260628_112233_mop_bosgenesis", "commit_hash": "def456", "branch": "main"}},
        )
        repository.add_event(
            run_id,
            "run_completed",
            "MoP Generation completed",
            {"artifact_publish": {"status": "success", "folder_name": "260628_112233_mop_bosgenesis"}},
        )
        repository.update_status(run_id, "completed", final_report="# Method of Procedure: bosgenesis")

        list_response = client.get("/api/activity/runs?workflow_type=mop_generation&status=published&published=true")
        assert list_response.status_code == 200
        list_json = list_response.json()
        assert list_json["count"] == 1
        node = list_json["nodes"][0]
        assert node["workflow_type"] == "mop_generation"
        assert node["workflow_label"] == "MoP Generation"
        assert node["workflow_badge"] == "MOP"
        assert node["repository"] == "bosgenesis (kubernetes_generic)"
        assert node["namespace"] == "bosgenesis"
        assert node["artifact_summary"]["has_markdown"] is True
        assert node["artifact_summary"]["has_pdf"] is True
        assert node["artifact_summary"]["has_bundle"] is True

        detail_response = client.get(f"/api/activity/runs/{run_id}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        stages = {stage["id"]: stage for stage in detail["stages"]}
        assert stages["scope"]["status"] == "success"
        assert stages["k8s"]["status"] == "success"
        assert stages["helm"]["status"] == "success"
        assert stages["mop_agent"]["status"] == "success"
        assert stages["publish"]["status"] == "success"
        actions = detail["artifact_actions"]["actions"]
        assert actions["bundle"]["filename"] == "mop-bundle.zip"
        assert actions["bundle"]["label"] == "Download MoP Bundle"
        assert "markdown" not in actions
        assert "pdf" not in actions

        artifacts_response = client.get(f"/api/activity/runs/{run_id}/artifacts")
        assert artifacts_response.status_code == 200
        assert artifacts_response.json()["repo_folder_url"].endswith("/tree/main/260628_112233_mop_bosgenesis")

        old_markdown_download = client.get(
            f"/api/activity/runs/{run_id}/artifact/markdown/download",
            follow_redirects=False,
        )
        assert old_markdown_download.status_code == 404

        download_response = client.get(
            f"/api/activity/runs/{run_id}/artifact/bundle/download",
            follow_redirects=False,
        )
        assert download_response.status_code == 307
        assert download_response.headers["location"].endswith("/mop-bundle.zip")

        chat_response = client.post(
            "/api/activity/chat",
            json={
                "message": "What does this MoP do?",
                "selected_run_ids": [run_id],
                "model_profile": "azure_configured",
            },
        )
        assert chat_response.status_code == 200
        chat_json = chat_response.json()
        assert "bosgenesis" in chat_json["answer"]
        assert chat_json["citations"]

        configmap_response = client.post(
            "/api/activity/chat",
            json={
                "message": "Do I have any configmap in this mop bundle?",
                "selected_run_ids": [run_id],
                "model_profile": "azure_configured",
            },
        )
        assert configmap_response.status_code == 200
        configmap_json = configmap_response.json()
        assert "### ConfigMaps in Selected MoP Bundle" in configmap_json["answer"]
        assert "| # | Name | Namespace | Category | Source file |" in configmap_json["answer"]
        assert "Raw/rendered manifest" in configmap_json["answer"]
        assert "ConfigMap" in configmap_json["answer"]
        assert "signoz-dashboard-config" in configmap_json["answer"]
        assert "signoz-configmap.yaml" in configmap_json["answer"]

        configmap_history = client.get(f"/api/activity/chat/{configmap_json['session_id']}")
        assert configmap_history.status_code == 200
        assert configmap_history.json()["messages"][-1]["payload"]["used_direct_answer"] is True

def test_activity_artifact_upload_overwrites_published_github_file(tmp_path, monkeypatch) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("git executable is required for activity upload test")

    remote = tmp_path / "activity-upload-remote.git"
    subprocess.run([git, "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    monkeypatch.setenv("ARTIFACT_GIT_REPO_URL", str(remote))
    monkeypatch.setenv("ARTIFACT_GIT_BRANCH", "main")
    monkeypatch.setenv("ARTIFACT_GIT_WORKSPACE_DIR", str(tmp_path / "activity-upload-work"))
    monkeypatch.setenv("ARTIFACT_GIT_USER_NAME", "ESDA Test")
    monkeypatch.setenv("ARTIFACT_GIT_USER_EMAIL", "esda-test@example.com")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        run_id = "run_activity_upload"
        repository = client.app.state.repository
        publisher = client.app.state.artifact_publisher

        publish = asyncio.run(
            publisher.publish_release_artifacts(
                run_id=run_id,
                github_url="https://github.com/aveeshek/bosgenesis-mop-creation-agent",
                job_name="v0.0.1 Activity Upload",
                markdown=ArtifactPublishPayload(
                    filename="release-notes.md",
                    content=b"# Original Activity Release\n",
                    artifact_id="art_upload_md",
                    mime_type="text/markdown",
                ),
                pdf=ArtifactPublishPayload(
                    filename="release-notes.pdf",
                    content=b"%PDF-1.4 activity original\n",
                    artifact_id="art_upload_pdf",
                    mime_type="application/pdf",
                ),
            )
        )

        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Generate release notes for https://github.com/aveeshek/bosgenesis-mop-creation-agent",
            target_url="https://github.com/aveeshek/bosgenesis-mop-creation-agent",
            namespace=None,
            workflow_type="release_note_creation",
        )
        repository.add_event(run_id, "run_started", "Release-note generation started", {})
        repository.add_event(
            run_id,
            "artifact_publish_completed",
            "Release-note artifacts published to GitHub artifact repository",
            {"artifact_publish": publish},
        )
        repository.update_status(run_id, "completed", final_report="# Release Notes")

        response = client.post(
            f"/api/activity/release-notes/{run_id}/artifact/markdown/upload",
            files={"file": ("reviewed-release-notes.md", b"# Reviewed Activity Release\n", "text/markdown")},
        )

        assert response.status_code == 200
        result = response.json()["artifact_overwrite"]
        assert result["status"] == "success"
        assert result["folder_name"] == publish["folder_name"]
        assert result["filename"] == "release-notes.md"
        assert result["source_filename"] == "reviewed-release-notes.md"

        md_blob = subprocess.run(
            [git, "--git-dir", str(remote), "show", f"main:{publish['folder_name']}/release-notes.md"],
            check=True,
            capture_output=True,
        ).stdout
        pdf_blob = subprocess.run(
            [git, "--git-dir", str(remote), "show", f"main:{publish['folder_name']}/release-notes.pdf"],
            check=True,
            capture_output=True,
        ).stdout
        assert md_blob == b"# Reviewed Activity Release\n"
        assert pdf_blob == b"%PDF-1.4 activity original\n"

        events = repository.list_events(run_id)
        assert any(event["event_type"] == "artifact_overwrite_started" for event in events)
        assert any(event["event_type"] == "artifact_overwrite_completed" for event in events)

def test_activity_mop_bundle_upload_creates_github_folder(tmp_path, monkeypatch) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("git executable is required for activity upload test")

    remote = tmp_path / "activity-mop-bundle-remote.git"
    subprocess.run([git, "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    monkeypatch.setenv("ARTIFACT_GIT_REPO_URL", str(remote))
    monkeypatch.setenv("ARTIFACT_GIT_BRANCH", "main")
    monkeypatch.setenv("ARTIFACT_GIT_WORKSPACE_DIR", str(tmp_path / "activity-mop-bundle-work"))
    monkeypatch.setenv("ARTIFACT_GIT_USER_NAME", "ESDA Test")
    monkeypatch.setenv("ARTIFACT_GIT_USER_EMAIL", "esda-test@example.com")

    bundle_path = tmp_path / "reviewed-mop-bundle.zip"
    with zipfile.ZipFile(bundle_path, "w") as archive:
        archive.writestr("mop.md", "# Reviewed MoP\n")
        archive.writestr("deployment-artifacts/machine_execution_plan.yaml", "steps: []\n")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        run_id = "run_activity_mop_bundle_upload"
        repository = client.app.state.repository

        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Generate MoP bundle for namespace signoz",
            target_url="k8s://kubernetes_generic/signoz",
            namespace="signoz",
            workflow_type="mop_generation",
        )
        repository.add_event(run_id, "run_started", "MoP Generation started", {"namespace": "signoz"})
        repository.update_status(run_id, "completed", final_report="# Method of Procedure")

        markdown_response = client.post(
            f"/api/activity/runs/{run_id}/artifact/markdown/upload",
            files={"file": ("mop.md", b"# Wrong route\n", "text/markdown")},
        )
        assert markdown_response.status_code == 400

        response = client.post(
            f"/api/activity/runs/{run_id}/artifact/bundle/upload",
            files={"file": ("reviewed-mop-bundle.zip", bundle_path.read_bytes(), "application/zip")},
        )

        assert response.status_code == 200
        result = response.json()["artifact_overwrite"]
        assert result["status"] == "created"
        assert result["filename"] == "mop-bundle.zip"
        assert result["folder_name"]

        bundle_blob = subprocess.run(
            [git, "--git-dir", str(remote), "show", f"main:{result['folder_name']}/mop-bundle.zip"],
            check=True,
            capture_output=True,
        ).stdout
        published_bundle = tmp_path / "published-mop-bundle.zip"
        published_bundle.write_bytes(bundle_blob)
        assert zipfile.is_zipfile(published_bundle)

        detail_response = client.get(f"/api/activity/runs/{run_id}")
        assert detail_response.status_code == 200
        actions = detail_response.json()["artifact_actions"]["actions"]
        assert actions["bundle"]["label"] == "Download MoP Bundle"
        assert "markdown" not in actions
        assert "pdf" not in actions
def test_activity_artifact_upload_creates_github_folder_for_local_only_run(tmp_path, monkeypatch) -> None:
    git = shutil.which("git")
    if not git:
        pytest.skip("git executable is required for activity upload test")

    remote = tmp_path / "activity-create-remote.git"
    subprocess.run([git, "init", "--bare", str(remote)], check=True, capture_output=True, text=True)
    monkeypatch.setenv("ARTIFACT_GIT_REPO_URL", str(remote))
    monkeypatch.setenv("ARTIFACT_GIT_BRANCH", "main")
    monkeypatch.setenv("ARTIFACT_GIT_WORKSPACE_DIR", str(tmp_path / "activity-create-work"))
    monkeypatch.setenv("ARTIFACT_GIT_USER_NAME", "ESDA Test")
    monkeypatch.setenv("ARTIFACT_GIT_USER_EMAIL", "esda-test@example.com")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        run_id = "run_activity_create_upload"
        repository = client.app.state.repository

        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Generate release notes for https://github.com/aveeshek/bosgenesis-mop-creation-agent",
            target_url="https://github.com/aveeshek/bosgenesis-mop-creation-agent",
            namespace=None,
            workflow_type="release_note_creation",
        )
        repository.add_event(run_id, "run_started", "Release-note generation started", {})
        repository.update_status(run_id, "completed", final_report="# Release Notes")

        response = client.post(
            f"/api/activity/release-notes/{run_id}/artifact/markdown/upload",
            files={"file": ("reviewed-local-release-notes.md", b"# First GitHub Upload\n", "text/markdown")},
        )

        assert response.status_code == 200
        result = response.json()["artifact_overwrite"]
        assert result["status"] == "created"
        assert result["filename"] == "release-notes.md"
        assert result["folder_name"]

        md_blob = subprocess.run(
            [git, "--git-dir", str(remote), "show", f"main:{result['folder_name']}/release-notes.md"],
            check=True,
            capture_output=True,
        ).stdout
        assert md_blob == b"# First GitHub Upload\n"

        detail_response = client.get(f"/api/activity/release-notes/{run_id}")
        assert detail_response.status_code == 200
        publish_state = detail_response.json()["node"]["publish_state"]
        assert publish_state["published"] is True
        assert publish_state["folder_name"] == result["folder_name"]
        assert publish_state["commit_hash"] == result["commit_hash"]

        events = repository.list_events(run_id)
        assert any(event["event_type"] == "artifact_publish_completed" for event in events)
        assert any(event["event_type"] == "artifact_overwrite_completed" for event in events)
