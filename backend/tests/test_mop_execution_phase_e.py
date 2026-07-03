from __future__ import annotations

from io import BytesIO
import json
import zipfile

from backend.tests.test_phase1_app import build_test_client


def _bundle_bytes(
    *,
    source_namespace: str = "signoz",
    target_namespace: str = "agent-testing",
    include_secret: bool = False,
    manifest_namespace: str | None = None,
    missing_required: bool = False,
) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if not missing_required:
            archive.writestr(
                "artifact.json",
                json.dumps(
                    {
                        "bundle_id": "bundle_test_1",
                        "source_namespace": source_namespace,
                        "target_namespace_placeholder": target_namespace,
                        "generated_release_name": "agent-ai-signoz",
                        "generated_at": "2026-06-30T00:00:00Z",
                        "target_environment": "kubernetes_with_helm",
                    }
                ),
            )
            archive.writestr(
                "machine_execution_plan.yaml",
                "steps:\n- command: kubectl apply --dry-run=server -f deployment-artifacts/kubernetes-manifests/app.yaml\n",
            )
            archive.writestr("mop-signoz-to-agent-testing.human-mop.md", "# Human MoP\n")
            archive.writestr("mop-signoz-to-agent-testing.pdf", b"%PDF-1.4\n")
            archive.writestr("deployment-artifacts.zip", b"PK\x03\x04")
            archive.writestr(
                "deployment-artifacts/artifact-index.json",
                json.dumps(
                    {
                        "values": ["values.yaml"],
                        "kubernetes_manifests": ["deployment-artifacts/kubernetes-manifests/app.yaml"],
                        "rendered_manifests": [],
                        "crds": [],
                        "warnings": [],
                    }
                ),
            )
        namespace = manifest_namespace or target_namespace
        archive.writestr(
            "deployment-artifacts/kubernetes-manifests/app.yaml",
            f"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: demo\n  namespace: {namespace}\n",
        )
        if include_secret:
            archive.writestr(
                "deployment-artifacts/kubernetes-manifests/secret.yaml",
                "apiVersion: v1\nkind: Secret\nmetadata:\n  name: copied\n  namespace: agent-testing\ndata:\n  password: YmFk\n",
            )
    return buffer.getvalue()


def _seed_bundle_run(client, user_id: str, content: bytes, *, run_id: str = "mop_generation_good") -> dict:
    repository = client.app.state.repository
    repository.create_run(
        run_id=run_id,
        user_id=user_id,
        goal="Generate portable MoP for namespace signoz",
        target_url="mop://signoz",
        namespace="signoz",
        workflow_type="mop_generation",
    )
    repository.update_status(run_id, "completed", final_report="MoP bundle generated")
    repository.add_event(
        run_id,
        "artifact_publish_completed",
        "MoP bundle published",
        {"artifact_publish": {"folder_name": "260630_120000_mop_signoz", "branch": "main"}},
    )
    return client.app.state.artifact_service.save_bytes(
        run_id=run_id,
        user_id=user_id,
        artifact_type="mop_bundle_zip",
        title="mop-bundle.zip",
        content=content,
        filename_suffix=".zip",
        mime_type="application/zip",
        metadata={
            "filename": "mop-bundle.zip",
            "namespace": "signoz",
            "target_namespace_placeholder": "agent-testing",
            "target_environment": "kubernetes_with_helm",
            "bundle_id": "bundle_test_1",
            "bundle_timestamp": "2026-06-30T00:00:00Z",
        },
    )


def test_mop_execution_lists_activity_run_bundles_and_preflights_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        artifact = _seed_bundle_run(client, user_id, _bundle_bytes())

        listed = client.get("/api/mop-execution/bundles")

        assert listed.status_code == 200
        bundles = listed.json()["bundles"]
        assert len(bundles) == 1
        assert bundles[0]["run_id"] == "mop_generation_good"
        assert bundles[0]["sha256"]
        assert bundles[0]["publish_folder"] == "260630_120000_mop_signoz"

        preflight = client.post(
            "/api/mop-execution/preflight",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_good",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
            },
        )

        assert preflight.status_code == 200
        result = preflight.json()
        assert result["valid"] is True
        assert result["status"] == "passed"
        assert result["metadata"]["source_namespace"] == "signoz"
        statuses = {check["id"]: check["status"] for check in result["checks"]}
        assert statuses["artifact_json"] == "passed"
        assert statuses["no_secret_material"] == "passed"
        assert statuses["manifest_namespace_rewrite"] == "passed"


def test_mop_execution_preflight_blocks_secret_and_source_namespace_reuse(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing,signoz")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        artifact = _seed_bundle_run(
            client,
            user_id,
            _bundle_bytes(include_secret=True, manifest_namespace="signoz"),
            run_id="mop_generation_risky",
        )

        preflight = client.post(
            "/api/mop-execution/preflight",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_risky",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "signoz",
            },
        )

        assert preflight.status_code == 200
        result = preflight.json()
        assert result["valid"] is False
        assert any("source namespace" in failure.lower() for failure in result["failures"])
        assert any("secret" in failure.lower() for failure in result["failures"])


def test_mop_execution_upload_preflight_reports_missing_required_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.post(
            "/api/mop-execution/preflight/upload",
            data={"target_namespace": "agent-testing"},
            files={"file": ("mop-bundle.zip", _bundle_bytes(missing_required=True), "application/zip")},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert "Missing required file: artifact.json" in result["failures"]
        assert result["bundle"]["filename"] == "mop-bundle.zip"


def test_mop_execution_artifact_repo_folder_maps_to_local_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        _seed_bundle_run(client, user_id, _bundle_bytes())

        response = client.post(
            "/api/mop-execution/preflight",
            json={
                "source_type": "artifact_repo_folder",
                "folder_name": "260630_120000_mop_signoz",
                "target_namespace": "agent-testing",
            },
        )

        assert response.status_code == 200
        assert response.json()["valid"] is True

def _latest_style_bundle_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "artifact.json",
            json.dumps(
                {
                    "bundle_id": "bundle_latest_style",
                    "source_namespace": "signoz",
                    "target_namespace_placeholder": "agent-testing",
                    "generated_release_name": "agent-ai-signoz",
                    "generated_at": "2026-07-01T12:52:47Z",
                    "target_environment": "kubernetes_with_helm",
                }
            ),
        )
        archive.writestr(
            "machine_execution_plan.yaml",
            "steps:\n"
            "- command: kubectl apply --dry-run=server -f deployment-artifacts/kubernetes-manifests/app.yaml\n"
            "- command: kubectl delete namespace agent-testing # only if created for this change and cleanup is approved\n",
        )
        archive.writestr("mop-signoz-to-agent-testing.human-mop.md", "# Human MoP\n")
        archive.writestr("mop-signoz-to-agent-testing.pdf", b"%PDF-1.4\n")
        archive.writestr("deployment-artifacts.zip", b"PK\x03\x04")
        archive.writestr(
            "deployment-artifacts/artifact-index.json",
            json.dumps(
                {
                    "values": ["values.yaml"],
                    "kubernetes_manifests": ["deployment-artifacts/kubernetes-manifests/app.yaml"],
                    "rendered_manifests": ["deployment-artifacts/rendered-manifests/agent-ai-signoz-rendered.yaml"],
                    "crds": ["deployment-artifacts/kubernetes-manifests/crds/clickhouseinstallations.clickhouse.altinity.com.yaml"],
                    "warnings": [],
                }
            ),
        )
        archive.writestr(
            "deployment-artifacts/kubernetes-manifests/app.yaml",
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: demo\n  namespace: agent-testing\n",
        )
        archive.writestr(
            "deployment-artifacts/rendered-manifests/agent-ai-signoz-rendered.yaml",
            "apiVersion: v1\nkind: Secret\nmetadata:\n  name: generated\n  namespace: agent-testing\ndata:\n  password: GENERATED\n",
        )
        archive.writestr(
            "deployment-artifacts/helm-chart/extracted/signoz/templates/secrets.yaml",
            "apiVersion: v1\nkind: Secret\nmetadata:\n  name: {{ include \"signoz.name\" . }}\ndata:\n  password: {{ randAlphaNum 16 | b64enc }}\n",
        )
        archive.writestr(
            "deployment-artifacts/kubernetes-manifests/crds/clickhouseinstallations.clickhouse.altinity.com.yaml",
            "apiVersion: apiextensions.k8s.io/v1\nkind: CustomResourceDefinition\nspec:\n  validation:\n    openAPIV3Schema:\n      properties:\n        namespace:\n          type: string\n",
        )
    return buffer.getvalue()


def test_mop_execution_preflight_allows_generated_secrets_and_approval_gated_cleanup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        user_id = login.json()["user"]["user_id"]
        artifact = _seed_bundle_run(client, user_id, _latest_style_bundle_bytes(), run_id="mop_generation_latest_style")

        preflight = client.post(
            "/api/mop-execution/preflight",
            json={
                "source_type": "activity_run",
                "run_id": "mop_generation_latest_style",
                "artifact_id": artifact["artifact_id"],
                "target_namespace": "agent-testing",
            },
        )

        assert preflight.status_code == 200
        result = preflight.json()
        assert result["valid"] is True
        statuses = {check["id"]: check["status"] for check in result["checks"]}
        details = {check["id"]: check["detail"] for check in result["checks"]}
        assert statuses["no_secret_material"] == "warning"
        assert statuses["no_cluster_scoped_destructive_actions"] == "warning"
        assert statuses["manifest_namespace_rewrite"] == "passed"
        assert "type:" not in details["manifest_namespace_rewrite"]
        assert not result["failures"]