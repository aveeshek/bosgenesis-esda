import asyncio
import importlib
from pathlib import Path
import shutil
import subprocess

import pytest

from fastapi.testclient import TestClient

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


def test_activity_page_renders_timeline_shell(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/activity")

        assert response.status_code == 200
        assert "Activity Timeline" in response.text
        assert 'id="activity-graph"' in response.text
        assert 'id="activity-status-filter"' in response.text
        assert 'id="activity-model-filter"' not in response.text
        assert 'id="activity-repo-filter"' not in response.text
        assert 'id="activity-chat-canvas"' in response.text
        assert 'activity-selected-chips' not in response.text
        assert 'activity-chat-context' not in response.text
        assert 'id="activity-stage-chain"' in response.text
        assert "Artifact Chat" in response.text
        assert 'href="/activity"' in response.text
        assert 'src="/static/js/activity.js' in response.text
        assert 'activity-layout-fixes-2' in response.text
        assert 'transaction-sidebar-toggle' not in response.text


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
                "model_profile": {"profile_id": "azure_gpt5_pro", "label": "GPT-5 Pro", "short_label": "GPT-5"},
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
        assert node["model_profile"]["label"] == "GPT-5 Pro"

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
