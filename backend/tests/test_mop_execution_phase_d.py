from backend.tests.test_phase1_app import build_test_client


def test_mop_execution_page_renders_shell_and_shared_navigation(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/mop-execution")

        assert response.status_code == 200
        body = response.text
        assert 'href="/mop-execution"' in body
        assert 'is-active{% endif %}' not in body
        assert 'id="model_profile"' in body
        assert 'class="profile-trigger"' in body
        assert 'id="transaction-sidebar-toggle"' in body
        assert 'id="mop-execution-form"' in body
        assert 'id="mop_execution_bundle_source"' in body
        assert 'id="mop_execution_activity_run"' in body
        assert 'id="mop_execution_repo_folder"' in body
        assert 'id="mop_execution_bundle_file"' in body
        assert 'id="mop-execution-bundle-metadata"' in body
        assert 'id="mop_execution_target_namespace"' in body
        assert 'id="mop_execution_correlation_id"' in body
        assert 'id="mop-sphere-canvas"' in body
        assert 'id="ephemeral-working-stream"' in body
        assert 'id="safe-summary-list"' in body
        assert 'id="final-report"' in body
        assert 'id="agent-activity-rail"' in body
        assert 'data-auto-hide-ms="30000"' in body
        assert '/static/js/mop_execution.js' in body


def test_mop_execution_page_uses_configured_target_namespaces(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing,qa-lab")
    monkeypatch.setenv("MOP_EXECUTION_DEFAULT_TARGET_NAMESPACE", "qa-lab")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/mop-execution")

        assert response.status_code == 200
        assert '<option value="agent-testing"' in response.text
        assert '<option value="qa-lab" selected' in response.text