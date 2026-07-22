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


def test_mop_execution_history_uses_compact_accessible_restore_cards() -> None:
    script = (
        __import__("pathlib").Path(__file__).parents[1]
        / "app"
        / "static"
        / "js"
        / "mop_execution.js"
    ).read_text(encoding="utf-8")

    assert 'document.createElement("article")' in script
    assert 'card.setAttribute("role", "button")' in script
    assert 'snapshot?compact=true' in script
    assert 'Loading persisted execution state...' in script
    assert 'Selected execution restored.' in script


def test_compact_snapshot_restores_deep_agent_evidence(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        user_id = login.json()["user"]["user_id"]
        repository = client.app.state.repository
        run_id = "mopx_deep_history_restore"
        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Restore a deeply nested Bundle Execution run",
            target_url=None,
            namespace="agent-testing",
            workflow_type="mop_execution",
        )
        payload = {"value": "kept"}
        for _ in range(40):
            payload = {"nested": payload}
        repository.add_event(run_id, "agent_response_received", "Deep agent response", payload)
        repository.update_status(run_id, "running")

        response = client.get(f"/api/runs/{run_id}/snapshot?compact=true")

        assert response.status_code == 200
        snapshot = response.json()
        assert snapshot["run"]["run_id"] == run_id
        assert snapshot["events"][0]["sequence"] == 1
        assert snapshot["events"][0]["payload"] == {"payload_omitted": True}