from backend.tests.test_phase1_app import build_test_client


def test_approval_lifecycle_for_high_risk_policy_decision(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        unauthenticated = client.get("/api/approvals")
        assert unauthenticated.status_code == 401

        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.post(
            "/api/policy/evaluate",
            json={
                "tool_name": "k8s.restart",
                "workflow_type": "k8s_management",
                "environment": "local",
                "namespace": "bosgenesis",
                "arguments": {"action": "restart", "resource": "deployment/api"},
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["decision"]["decision"] == "approval_required"
        assert result["approval"]["status"] == "pending"
        assert result["approval"]["run_id"] is None

        approval_id = result["approval"]["approval_id"]
        approvals = client.get("/api/approvals?status=pending")
        assert approvals.status_code == 200
        assert approvals.json()["approvals"][0]["approval_id"] == approval_id

        approved = client.post(
            f"/api/approvals/{approval_id}/approve",
            json={"notes": "Approved for test."},
        )
        assert approved.status_code == 200
        assert approved.json()["approval"]["status"] == "approved"
        assert approved.json()["approval"]["review_notes"] == "Approved for test."


def test_modify_and_recheck_rejects_denied_policy_change(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        created = client.post(
            "/api/policy/evaluate",
            json={
                "tool_name": "k8s.restart",
                "workflow_type": "k8s_management",
                "environment": "local",
                "namespace": "bosgenesis",
                "arguments": {"action": "restart", "resource": "deployment/api"},
            },
        ).json()
        approval_id = created["approval"]["approval_id"]

        modified = client.post(
            f"/api/approvals/{approval_id}/modify-and-recheck",
            json={
                "tool_name": "powershell.raw",
                "workflow_type": "k8s_management",
                "environment": "local",
                "namespace": "bosgenesis",
                "arguments": {"command": "Get-Secret"},
            },
        )

        assert modified.status_code == 200
        approval = modified.json()["approval"]
        assert approval["status"] == "rejected"
        assert approval["policy_decision"]["decision"] == "deny"


def test_policy_evaluate_denied_action_does_not_create_approval(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.post(
            "/api/policy/evaluate",
            json={
                "tool_name": "powershell.raw",
                "workflow_type": "k8s_management",
                "environment": "local",
                "namespace": "bosgenesis",
                "arguments": {"command": "Get-Secret"},
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["decision"]["decision"] == "deny"
        assert result["approval"] is None
