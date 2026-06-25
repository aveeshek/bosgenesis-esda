from backend.tests.test_phase1_app import build_test_client


def test_workflow_classification_endpoint_requires_auth_and_classifies_release_notes(
    tmp_path,
    monkeypatch,
) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        unauthenticated = client.post(
            "/api/workflows/classify",
            json={
                "message": "Generate release notes",
                "github_url": "https://github.com/example/repo",
            },
        )
        assert unauthenticated.status_code == 401

        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.post(
            "/api/workflows/classify",
            json={
                "message": "Generate release notes",
                "github_url": "https://github.com/example/repo",
            },
        )

        assert response.status_code == 200
        result = response.json()
        assert result["workflow_type"] == "release_note_creation"
        assert result["prompt_version"] == "release_note_intent_classifier_v1"
        assert len(result["prompt_hash"]) == 64
        assert result["user"] == "admin"
