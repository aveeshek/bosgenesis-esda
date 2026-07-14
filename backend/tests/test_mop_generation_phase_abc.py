import asyncio
from io import BytesIO
import json
import zipfile

import httpx

from backend.app.config import Settings
from backend.app.tools.contracts import ToolExecutionRequest
from backend.app.tools.mop_agents import (
    HelmManagerEvidenceTool,
    K8sInspectorEvidenceTool,
    MopCreationAgentTool,
    redact_sensitive,
)
from backend.tests.test_phase1_app import build_test_client


def test_mop_generation_namespace_api_and_run_skeleton(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_ALLOWED_NAMESPACES", "bosgenesis,platform")
    monkeypatch.setenv("MOP_DEFAULT_ENVIRONMENT", "dev")
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        namespaces = client.get("/api/mop-generation/namespaces")
        assert namespaces.status_code == 200
        payload = namespaces.json()
        assert [item["name"] for item in payload["namespaces"]] == ["bosgenesis", "platform"]
        assert payload["source"] == "settings_allowlist"

        denied = client.post(
            "/api/mop-generation",
            json={"namespace": "kube-system", "change_intent": "Create a restart MoP"},
        )
        assert denied.status_code == 422
        assert denied.json()["detail"]["namespace"] == "kube-system"

        started = client.post(
            "/api/mop-generation",
            json={
                "namespace": "bosgenesis",
                "change_intent": "Create a read-only validation MoP for current workloads",
                "analysis_depth": "fast",
                "model_profile": "azure_gpt5_pro",
            },
        )
        assert started.status_code == 200
        result = started.json()
        assert result["workflow_type"] == "mop_generation"
        assert result["namespace"] == "bosgenesis"
        assert result["target_environment"] == "dev"
        assert result["events_url"].startswith("/api/runs/")

        run = client.app.state.repository.get_run(result["run_id"])
        assert run is not None
        assert run.workflow_type == "mop_generation"
        assert run.namespace == "bosgenesis"
        assert run.target_url == "k8s://dev/bosgenesis"


def test_mop_generation_graph_is_wired(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        assert client.app.state.mop_generation_graph is not None


def test_k8s_inspector_mcp_adapter_maps_tool_and_redacts_secret_payload() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content.decode()) if request.content else {}
        captured["query"] = request.url.query.decode() if isinstance(request.url.query, bytes) else str(request.url.query)
        return httpx.Response(
            200,
            json={
                "result": {
                    "kind": "Secret",
                    "metadata": {"name": "db-credentials"},
                    "data": {"password": "c2VjcmV0"},
                    "token": "raw-token",
                }
            },
        )

    tool = K8sInspectorEvidenceTool(
        Settings(k8s_inspector_agent_mcp_url="http://k8s-inspector.local"),
        transport=httpx.MockTransport(handler),
    )
    result, duration_ms = asyncio.run(
        tool.execute(
            ToolExecutionRequest(
                run_id="run_mop",
                step_id="k8s",
                tool_name="mop.k8s_inspector",
                workflow_type="mop_generation",
                environment="dev",
                namespace="bosgenesis",
                user_id="usr_1",
                arguments={"tool_name": "namespace_summary", "arguments": {"include_events": True}},
            )
        )
    )

    assert duration_ms >= 0
    assert captured["path"] == "/namespace/summary"
    assert captured["query"] == "namespace=bosgenesis&actor=esda"
    assert result.status == "success"
    assert result.output is not None
    redacted = result.output["result"]["result"]
    assert redacted["data"] == {"password": "***"}
    assert redacted["token"] == "***"


def test_helm_manager_mcp_adapter_maps_release_tool() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content.decode()) if request.content else {}
        captured["query"] = request.url.query.decode() if isinstance(request.url.query, bytes) else str(request.url.query)
        return httpx.Response(200, json={"result": {"releases": [{"name": "bosgenesis-app"}]}})

    tool = HelmManagerEvidenceTool(
        Settings(helm_manager_agent_mcp_url="http://helm-manager.local"),
        transport=httpx.MockTransport(handler),
    )
    result, _ = asyncio.run(
        tool.execute(
            ToolExecutionRequest(
                run_id="run_mop",
                step_id="helm",
                tool_name="mop.helm_manager",
                workflow_type="mop_generation",
                environment="dev",
                namespace="bosgenesis",
                user_id="usr_1",
                arguments={"tool_name": "list_releases", "arguments": {"namespace": "bosgenesis"}},
            )
        )
    )

    assert captured["path"] == "/releases"
    assert result.status == "success"
    assert result.output["result"]["result"]["releases"][0]["name"] == "bosgenesis-app"


def test_mop_creation_agent_mcp_adapter_maps_draft_tool() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content.decode()) if request.content else {}
        captured["query"] = request.url.query.decode() if isinstance(request.url.query, bytes) else str(request.url.query)
        return httpx.Response(
            200,
            json={"result": {"markdown": "# Method of Procedure", "status": "drafted"}},
        )

    tool = MopCreationAgentTool(
        Settings(mop_creation_agent_mcp_url="http://mop-creation.local/mcp"),
        transport=httpx.MockTransport(handler),
    )
    result, _ = asyncio.run(
        tool.execute(
            ToolExecutionRequest(
                run_id="run_mop",
                step_id="mop_agent",
                tool_name="mop.creation_agent",
                workflow_type="mop_generation",
                environment="dev",
                namespace="bosgenesis",
                user_id="usr_1",
                arguments={
                    "tool_name": "mop_create_draft",
                    "arguments": {
                        "namespace": "bosgenesis",
                        "change_intent": "Create validation MoP",
                    },
                },
            )
        )
    )

    assert captured["path"] == "/mcp/tools/mop_creation_generate"
    assert captured["payload"]["source_namespace"] == "bosgenesis"
    assert captured["payload"]["target_namespace"] == "generic-namespace"
    assert captured["payload"]["return_content"] is True
    assert result.status == "success"
    assert result.output["result"]["markdown"] == "# Method of Procedure"


def test_mop_mcp_adapter_blocks_unknown_tool() -> None:
    tool = MopCreationAgentTool(Settings(mop_creation_agent_mcp_url="http://mop-creation.local/mcp"))
    result, _ = asyncio.run(
        tool.execute(
            ToolExecutionRequest(
                run_id="run_mop",
                step_id="mop_agent",
                tool_name="mop.creation_agent",
                workflow_type="mop_generation",
                environment="dev",
                namespace="bosgenesis",
                user_id="usr_1",
                arguments={"tool_name": "delete_namespace"},
            )
        )
    )

    assert result.status == "blocked"
    assert result.error["code"] == "MOP_CREATION_AGENT_TOOL_NOT_ALLOWED"


def test_redact_sensitive_handles_nested_secret_shapes() -> None:
    payload = {
        "items": [
            {"kind": "Secret", "data": {"api-key": "abc"}, "stringData": {"token": "def"}},
            {"metadata": {"name": "safe"}, "passwordRef": "plain"},
        ],
        "authorization": "Bearer token",
    }

    assert redact_sensitive(payload) == {
        "items": [
            {"kind": "Secret", "data": {"api-key": "***"}, "stringData": {"token": "***"}},
            {"metadata": {"name": "safe"}, "passwordRef": "***"},
        ],
        "authorization": "***",
    }



def test_mop_generation_page_renders_phase_e_shell(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MOP_ALLOWED_NAMESPACES", "bosgenesis,platform")
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert login.status_code == 200

        response = client.get("/mop-generation")
        assert response.status_code == 200
        assert "Bundle Generation" in response.text
        assert "mop_generation.js" in response.text
        assert "mop-generation-form" in response.text
        assert (
            "Generate MoP in both markdown and PDF format so we can fully clone the source namespace"
            in response.text
        )
        assert "Live Working Stream &amp; Safe Reasoning Summaries" in response.text
        assert "Download links appear after artifact rendering." in response.text


def test_mop_generation_chains_produce_structured_fallbacks() -> None:
    from backend.app.chains.mop_generation import (
        MopGenerationIntentClassifierChain,
        MopGenerationPlannerChain,
        MopGenerationRecoveryRecommendationChain,
        MopGenerationReportWriterChain,
        MopGenerationVerifierChain,
    )

    class FallbackOnlyLlm:
        pass

    llm = FallbackOnlyLlm()
    classifier = MopGenerationIntentClassifierChain(llm)
    planner = MopGenerationPlannerChain(llm)
    writer = MopGenerationReportWriterChain(llm)
    verifier = MopGenerationVerifierChain(llm)
    recovery = MopGenerationRecoveryRecommendationChain(llm)

    classification = asyncio.run(
        classifier.run(
            namespace="bosgenesis",
            change_intent="Create validation MoP",
            target_environment="dev",
        )
    )
    assert classification.workflow_type == "mop_generation"
    assert classification.confidence >= 0.9

    plan = asyncio.run(
        planner.run(
            namespace="bosgenesis",
            change_intent="Create validation MoP",
            target_environment="dev",
            helm_release=None,
            analysis_depth="fast",
        )
    )
    assert plan.workflow_type == "mop_generation"
    assert any(step.tool == "mop.creation_agent" for step in plan.steps)

    failed_tool = {"status": "failed", "error": {"message": "not configured"}}
    draft = asyncio.run(
        writer.run(
            namespace="bosgenesis",
            change_intent="Create validation MoP",
            target_environment="dev",
            helm_release=None,
            plan=plan.model_dump(),
            k8s_result=failed_tool,
            helm_result=failed_tool,
            mop_agent_result=failed_tool,
        )
    )
    assert "# Method of Procedure: bosgenesis" in draft.markdown
    assert "## 11. Rollback Plan" in draft.markdown

    verification = asyncio.run(
        verifier.run(
            markdown=draft.markdown,
            namespace="bosgenesis",
            tool_results={"k8s": failed_tool},
        )
    )
    assert verification.valid is True

    recommendation = asyncio.run(
        recovery.run(
            tool_results={"k8s": failed_tool},
            verification=verification.model_dump(),
        )
    )
    assert recommendation.action == "continue"


def test_mop_generation_graph_executes_phase_d_with_fallback_tools(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ARTIFACT_GIT_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("MOP_ALLOWED_NAMESPACES", "bosgenesis")
    monkeypatch.setenv("MOP_DEFAULT_ENVIRONMENT", "dev")
    with build_test_client(tmp_path, monkeypatch) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        user_id = login.json()["user"]["user_id"]
        repository = client.app.state.repository
        run_id = "mop_phase_d"
        repository.create_run(
            run_id=run_id,
            user_id=user_id,
            goal="Generate MoP for namespace bosgenesis",
            target_url="k8s://dev/bosgenesis",
            namespace="bosgenesis",
            workflow_type="mop_generation",
        )

        from backend.app.graphs.mop_generation import MopGenerationInput

        class FakeArtifactPublisher:
            is_enabled = True

            def __init__(self) -> None:
                self.calls = []

            def target_summary(self) -> dict:
                return {"enabled": True, "repo_url": "memory://artifact-repo", "branch": "main"}

            async def publish_artifact_files(self, *, run_id, github_url, job_name, files, commit_label="artifacts") -> dict:
                filenames = [item.filename for item in files]
                self.calls.append({"run_id": run_id, "github_url": github_url, "job_name": job_name, "filenames": filenames, "commit_label": commit_label})
                return {
                    "status": "success",
                    "repo_url": "memory://artifact-repo",
                    "branch": "main",
                    "folder_name": "260101_010101_mop_bosgenesis",
                    "files": [{"filename": item.filename, "artifact_id": item.artifact_id, "mime_type": item.mime_type} for item in files],
                    "github_url": github_url,
                }

        fake_publisher = FakeArtifactPublisher()
        client.app.state.mop_generation_graph.artifact_publisher = fake_publisher

        asyncio.run(
            client.app.state.mop_generation_graph.run(
                MopGenerationInput(
                    run_id=run_id,
                    user_id=user_id,
                    namespace="bosgenesis",
                    target_environment="dev",
                    target_namespace="generic-namespace",
                    change_intent="Create a read-only workload validation MoP",
                    analysis_depth="fast",
                    user_roles=["admin"],
                )
            )
        )

        run = repository.get_run(run_id)
        assert run is not None
        assert run.status == "completed"
        assert "# MoP: Namespace Recreation MoP - bosgenesis to generic-namespace" in run.final_report
        event_types = [event["event_type"] for event in repository.list_events(run_id)]
        assert "workflow_classified" in event_types
        assert "k8s_evidence_completed" in event_types
        assert "helm_evidence_completed" in event_types
        assert "mop_agent_completed" in event_types
        assert "validation_completed" in event_types
        assert "artifact_created" in event_types
        assert "artifact_bundle_created" in event_types
        assert "safe_reasoning_summary" in event_types
        safe_events = [event for event in repository.list_events(run_id) if event["event_type"] == "safe_reasoning_summary"]
        assert len(safe_events) >= 8
        assert any(event["payload"]["phase"] == "k8s" for event in safe_events)
        assert any(event["payload"]["phase"] == "complete" for event in safe_events)
        artifacts = repository.list_artifacts(run_id)
        assert len(artifacts) >= 6
        filenames = {artifact["metadata"].get("filename") for artifact in artifacts}
        assert "artifact.json" in filenames
        assert "machine_execution_plan.yaml" in filenames
        assert "deployment-artifacts.zip" in filenames
        assert "mop-bundle.zip" in filenames
        assert any(str(filename).endswith(".human-mop.md") for filename in filenames)
        assert any(str(filename).endswith(".installation.md") for filename in filenames)
        assert any(str(filename).endswith(".pdf") for filename in filenames)
        assert any(artifact["mime_type"] == "text/markdown; charset=utf-8" for artifact in artifacts)
        assert any(artifact["mime_type"] == "application/pdf" for artifact in artifacts)
        assert any(artifact["mime_type"] == "application/zip" for artifact in artifacts)
        assert fake_publisher.calls
        published_filenames = set(fake_publisher.calls[0]["filenames"])
        assert published_filenames == {"mop-bundle.zip"}
        assert fake_publisher.calls[0]["commit_label"] == "MoP bundle zip"
        bundle_zip_artifact = next(
            artifact
            for artifact in artifacts
            if artifact["artifact_type"] == "mop_bundle_zip"
        )
        zip_bytes = client.app.state.artifact_service.read_artifact_bytes(bundle_zip_artifact["storage_path"])
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            names = set(archive.namelist())
        assert "artifact.json" in names
        assert "machine_execution_plan.yaml" in names
        assert "deployment-artifacts.zip" in names
        assert "deployment-artifacts/artifact-index.json" in names
        assert "deployment-artifacts/helm-commands.md" in names
        assert any(name.endswith(".human-mop.md") for name in names)
        assert any(name.endswith(".installation.md") for name in names)
        assert any(name.endswith(".pdf") for name in names)

        response = client.get(f"/api/runs/{run_id}/bundle")
        assert response.status_code == 200
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            dynamic_names = set(archive.namelist())
        assert "artifact.json" in dynamic_names
        assert "machine_execution_plan.yaml" in dynamic_names
        assert "mop-bundle.zip" in dynamic_names
