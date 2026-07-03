from __future__ import annotations

from typing import Any

from backend.app.tools.mop_execution_agent import MopExecutionAgentResponse
from backend.tests.test_mop_execution_phase_i import _waiting_for_approval_run
from backend.tests.test_mop_execution_phase_j import FakeMopExecutionMutationAgent, _approved_mutation_run
from backend.tests.test_phase1_app import build_test_client


class FakeMopExecutionValidationAgent(FakeMopExecutionMutationAgent):
    async def get_observations(self, job_id: str, params: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        phase = (params or {}).get("phase") or "unknown"
        if job_id in {"mutation_job_456", "dry_run_job_123"}:
            self.calls.append(f"get_observations_{phase}")
            payload = {
                "phase": phase,
                "validation_matrix": [
                    {
                        "kind": "Deployment",
                        "name": "agent-ai",
                        "namespace": "agent-testing",
                        "expected": "available replicas >= 1",
                        "observed": "1/1 available",
                        "status": "passed",
                    }
                ],
                "helm_status": {"release": "agent-ai", "status": "deployed", "revision": 2},
                "kubernetes_readiness": {"kind": "Deployment", "name": "agent-ai", "ready": True},
            }
            return self._response(
                "GET",
                f"http://agent/v1/execution-jobs/{job_id}/observations",
                payload,
                request={"params": params or {}},
            )
        return await super().get_observations(job_id, params)

    async def get_audit_events(self, job_id: str, params: dict[str, Any] | None = None) -> MopExecutionAgentResponse:
        if job_id in {"mutation_job_456", "dry_run_job_123"}:
            self.calls.append("get_validation_audit_events")
            return self._response(
                "GET",
                f"http://agent/v1/execution-jobs/{job_id}/audit-events",
                {"items": [{"event": "post_mutation_validation", "status": "passed"}]},
                request={"params": params or {}},
            )
        return await super().get_audit_events(job_id, params)

    async def list_reports(self, job_id: str) -> MopExecutionAgentResponse:
        if job_id in {"mutation_job_456", "dry_run_job_123"}:
            self.calls.append("list_mutation_reports")
            return self._response(
                "GET",
                f"http://agent/v1/execution-jobs/{job_id}/reports",
                {
                    "reports": [
                        {"report_id": "exec_report_456", "report_type": "execution", "title": "Execution Report"},
                        {"report_id": "validation_report_456", "report_type": "validation", "title": "Validation Report"},
                        {"report_id": "release_note_report_456", "report_type": "release_note", "title": "Change Evidence Notes"},
                    ]
                },
            )
        return await super().list_reports(job_id)

    async def get_report_metadata(self, job_id: str, report_id: str) -> MopExecutionAgentResponse:
        if job_id in {"mutation_job_456", "dry_run_job_123"}:
            self.calls.append(f"get_report_metadata_{report_id}")
            payload = {
                "report_id": report_id,
                "report_type": "validation" if "validation" in report_id else "release_note" if "release" in report_id else "execution",
                "summary": "Execution completed and post-mutation validation passed.",
                "validation_matrix": [
                    {
                        "kind": "Deployment",
                        "name": "agent-ai",
                        "namespace": "agent-testing",
                        "expected": "available replicas >= 1",
                        "observed": "1/1 available",
                        "status": "passed",
                    }
                ],
                "helm_status": {"release": "agent-ai", "status": "deployed", "history": [1, 2]},
                "kubernetes_readiness": [{"kind": "Deployment", "name": "agent-ai", "namespace": "agent-testing", "status": "ready"}],
            }
            return self._response(
                "GET",
                f"http://agent/v1/execution-jobs/{job_id}/reports/{report_id}",
                payload,
            )
        return await super().get_report_metadata(job_id, report_id)

    async def download_report(self, *, job_id: str, report_id: str, artifact: str = "pdf") -> tuple[bytes, str, str]:
        self.calls.append(f"download_{report_id}_{artifact}")
        suffix = {"markdown": "md", "pdf": "pdf", "html": "html"}.get(artifact, "bin")
        mime = {"markdown": "text/markdown; charset=utf-8", "pdf": "application/pdf", "html": "text/html; charset=utf-8"}.get(artifact, "application/octet-stream")
        return f"{report_id} {artifact} content".encode("utf-8"), mime, f"{report_id}.{suffix}"


class FakeArtifactPublisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def target_summary(self) -> dict[str, Any]:
        return {"enabled": True, "repo_url": "https://github.com/aveeshek/bosgenesis-artifacts.git", "branch": "main"}

    async def publish_artifact_files(self, *, run_id, github_url, job_name, files, commit_label="artifacts") -> dict:
        self.calls.append(
            {
                "run_id": run_id,
                "github_url": github_url,
                "job_name": job_name,
                "filenames": [item.filename for item in files],
                "commit_label": commit_label,
            }
        )
        return {
            "status": "success",
            "folder_name": "260630_010203_mop_execution_agent-testing",
            "tree_url": "https://github.com/aveeshek/bosgenesis-artifacts/tree/main/260630_010203_mop_execution_agent-testing",
            "files": [{"filename": item.filename, "artifact_id": item.artifact_id, "mime_type": item.mime_type} for item in files],
        }


def test_mop_execution_phase_k_collects_validation_reports_artifacts_and_publish(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "2")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionValidationAgent(mutation_states=["succeeded"])
        fake_publisher = FakeArtifactPublisher()
        client.app.state.artifact_publisher = fake_publisher
        approved = _approved_mutation_run(client, login.json()["user"]["user_id"], fake_agent)
        mutation = client.post("/api/mop-execution/mutation", json={"run_id": approved["run_id"]})
        assert mutation.status_code == 200
        assert mutation.json()["status"] == "mutation_succeeded"

        response = client.post("/api/mop-execution/validation-report", json={"run_id": approved["run_id"]})

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is True
        assert result["status"] == "completed"
        assert result["validation"]["validation_matrix"][0]["status"] == "passed"
        assert result["reports"]["phase"] == "post_mutation"
        assert "job_id=dry_run_job_123" in result["reports"]["reports"][0]["downloads"][0]["url"]
        assert result["artifact_publish"]["status"] == "success"
        assert fake_publisher.calls
        assert fake_publisher.calls[0]["filenames"] == ["mop-execution-report-bundle.zip"]
        artifacts = client.app.state.repository.list_artifacts(approved["run_id"])
        assert any((artifact.get("metadata") or {}).get("filename") == "mop-execution-report-bundle.zip" for artifact in artifacts)
        event_types = [event["event_type"] for event in result["events"]]
        assert "validation_completed" in event_types
        assert "reports_updated" in event_types
        assert "artifact_publish_completed" in event_types
        assert "run_completed" in event_types
        assert client.app.state.repository.get_run(approved["run_id"]).status == "completed"


def test_mop_execution_phase_k_blocks_before_mutation_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_EXECUTION_ALLOWED_TARGET_NAMESPACES", "agent-testing")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_ATTEMPTS", "1")
    monkeypatch.setenv("MOP_EXECUTION_AGENT_POLL_INTERVAL_SECONDS", "0")

    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200
        fake_agent = FakeMopExecutionValidationAgent()
        run = _waiting_for_approval_run(client, login.json()["user"]["user_id"], fake_agent)

        response = client.post("/api/mop-execution/validation-report", json={"run_id": run["run_id"]})

        assert response.status_code == 200
        result = response.json()
        assert result["valid"] is False
        assert result["status"] == "validation_missing_mutation_job"
        event_types = [event["event_type"] for event in result["events"]]
        assert "validation_completed" not in event_types