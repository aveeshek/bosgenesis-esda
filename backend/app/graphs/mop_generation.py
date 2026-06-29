from contextlib import suppress
from dataclasses import dataclass

import httpx

from backend.app.approvals import ApprovalService
from backend.app.artifact_publisher import ArtifactGitPublisher, ArtifactPublishPayload
from backend.app.artifacts import ArtifactService
from backend.app.chains.mop_generation import (
    MopGenerationIntentClassifierChain,
    MopGenerationPlannerChain,
    MopGenerationRecoveryRecommendationChain,
    MopGenerationReportWriterChain,
    MopGenerationVerifierChain,
)
from backend.app.config import Settings
from backend.app.db.database import RunRepository
from backend.app.graphs.event_bus import RunEventBus
from backend.app.llm.azure_gpt5 import AzureGpt5Service
from backend.app.logging.postgres_logger import PostgresLogger
from backend.app.mop_bundle import MopBundleBuilder, MopBundleResult
from backend.app.policy.evaluator import PolicyGuard
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult
from backend.app.tools.mop_agents import (
    HelmManagerEvidenceTool,
    K8sInspectorEvidenceTool,
    MopCreationAgentTool,
)
from backend.app.tools.registry import ToolRegistry

WORKFLOW_TYPE = "mop_generation"


@dataclass
class MopGenerationInput:
    run_id: str
    user_id: str
    namespace: str
    target_environment: str
    target_namespace: str
    change_intent: str
    helm_release: str | None = None
    implementation_window: str | None = None
    analysis_depth: str = "standard"
    model_profile: str | None = None
    user_roles: list[str] | None = None


class MopGenerationGraph:
    def __init__(
        self,
        *,
        repository: RunRepository,
        event_bus: RunEventBus,
        logger: PostgresLogger,
        settings: Settings,
        llm: AzureGpt5Service,
        k8s_inspector: K8sInspectorEvidenceTool,
        helm_manager: HelmManagerEvidenceTool,
        mop_creation_agent: MopCreationAgentTool,
        tool_registry: ToolRegistry,
        policy_guard: PolicyGuard,
        approval_service: ApprovalService,
        artifact_service: ArtifactService,
        artifact_publisher: ArtifactGitPublisher | None = None,
    ) -> None:
        self.repository = repository
        self.event_bus = event_bus
        self.logger = logger
        self.settings = settings
        self.llm = llm
        self.intent_classifier = MopGenerationIntentClassifierChain(llm)
        self.planner = MopGenerationPlannerChain(llm)
        self.report_writer = MopGenerationReportWriterChain(llm)
        self.verifier = MopGenerationVerifierChain(llm)
        self.recovery = MopGenerationRecoveryRecommendationChain(llm)
        self.k8s_inspector = k8s_inspector
        self.helm_manager = helm_manager
        self.mop_creation_agent = mop_creation_agent
        self.tool_registry = tool_registry
        self.policy_guard = policy_guard
        self.approval_service = approval_service
        self.artifact_service = artifact_service
        self.artifact_publisher = artifact_publisher

    async def _emit(self, run_id: str, event_type: str, message: str, payload: dict) -> None:
        event = self.repository.add_event(run_id, event_type, message, payload)
        await self.event_bus.publish(run_id, event)

    async def _emit_ephemeral(
        self, run_id: str, phase: str, message: str, detail: str = ""
    ) -> None:
        await self.event_bus.publish(
            run_id,
            {
                "event_id": f"live_{phase}",
                "run_id": run_id,
                "event_type": "ephemeral_working_note",
                "message": message,
                "payload": {
                    "phase": phase,
                    "detail": detail,
                    "ephemeral": True,
                    "persisted": False,
                },
            },
        )

    async def _emit_safe_summary(
        self,
        run_id: str,
        phase: str,
        message: str,
        reasoning_summary: str,
        details: dict | None = None,
    ) -> None:
        await self._emit(
            run_id,
            "safe_reasoning_summary",
            message,
            {
                "phase": phase,
                "stage_label": self._stage_label(phase),
                "reasoning_summary": reasoning_summary,
                "details": details or {},
                "persisted": True,
                "safe_for_audit": True,
            },
        )

    async def run(self, mop: MopGenerationInput) -> None:
        try:
            await self._run(mop)
        except Exception as exc:
            error_payload = {"error": {"type": exc.__class__.__name__, "message": str(exc)}}
            final_report = f"MoP Generation failed unexpectedly: {exc}"
            self.repository.update_status(mop.run_id, "failed", final_report)
            with suppress(Exception):
                await self._emit(
                    mop.run_id,
                    "run_failed",
                    "MoP Generation failed unexpectedly",
                    error_payload,
                )
            with suppress(Exception):
                await self.logger.event(
                    run_id=mop.run_id,
                    user_id=mop.user_id,
                    graph_node="unhandled_error",
                    event_type="run_failed",
                    message="MoP Generation failed unexpectedly",
                    payload=error_payload,
                    workflow_type=WORKFLOW_TYPE,
                )

    async def _run(self, mop: MopGenerationInput) -> None:
        self.repository.update_status(mop.run_id, "running")
        selected_model = self._model_profile_payload(mop.model_profile)
        model_deployment = str(
            selected_model.get("model_display")
            or selected_model.get("deployment")
            or selected_model.get("model_name")
            or selected_model.get("profile_id")
            or "not_configured"
        )
        await self._emit_ephemeral(
            mop.run_id,
            "creating_plan",
            "Creating MoP execution plan.",
            "The model is reading namespace, change intent, selected model, and guardrails.",
        )
        start_payload = {
            "namespace": mop.namespace,
            "target_environment": mop.target_environment,
            "target_namespace_placeholder": mop.target_namespace,
            "change_intent": mop.change_intent,
            "helm_release": mop.helm_release,
            "implementation_window": mop.implementation_window,
            "analysis_depth": mop.analysis_depth,
            "model_profile": selected_model,
        }
        await self._emit(mop.run_id, "run_started", "MoP Generation started", start_payload)
        await self.logger.event(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="start",
            event_type="run_started",
            message="MoP Generation started",
            payload=start_payload,
            workflow_type=WORKFLOW_TYPE,
        )

        await self._emit_ephemeral(
            mop.run_id,
            "classify",
            "Classifying MoP workflow intent.",
            "The model is confirming this is document generation, not execution.",
        )
        classification = await self.intent_classifier.run(
            namespace=mop.namespace,
            change_intent=mop.change_intent,
            target_environment=mop.target_environment,
            model_profile=mop.model_profile,
        )
        classification_payload = classification.model_dump()
        classification_payload["model_profile"] = selected_model
        await self.logger.llm_review(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="classify_intent",
            user_intent=mop.change_intent,
            plan=classification_payload,
            reasoning_summary=classification.reasoning_summary,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )
        await self._emit(
            mop.run_id,
            "workflow_classified",
            f"Workflow classified as {classification.workflow_type}",
            classification_payload,
        )
        await self._emit_safe_summary(
            mop.run_id,
            "classify",
            "Classifier safe reasoning summary",
            (
                f"{classification.reasoning_summary} The classifier selected "
                f"{classification.workflow_type} with confidence {classification.confidence:.2f}; "
                f"clarification required: {classification.needs_clarification}."
            ),
            {
                "workflow_type": classification.workflow_type,
                "confidence": classification.confidence,
                "needs_clarification": classification.needs_clarification,
                "input_summary": classification.input_summary,
            },
        )

        await self._emit(mop.run_id, "planning_started", "Creating MoP plan", start_payload)
        await self._emit_ephemeral(
            mop.run_id,
            "plan",
            "Building the evidence collection and drafting plan.",
            "The planner is deciding Kubernetes, Helm, MoP-agent, validation, and review steps.",
        )
        plan_model = await self.planner.run(
            namespace=mop.namespace,
            change_intent=mop.change_intent,
            target_environment=mop.target_environment,
            helm_release=mop.helm_release,
            analysis_depth=mop.analysis_depth,
            model_profile=mop.model_profile,
        )
        plan = plan_model.model_dump()
        plan["model_profile"] = selected_model
        self.repository.add_plan_steps(run_id=mop.run_id, steps=plan.get("steps") or [])
        await self.logger.llm_review(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="plan",
            user_intent=mop.change_intent,
            plan=plan,
            reasoning_summary=plan_model.reasoning_summary,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )
        await self._emit(mop.run_id, "plan_created", "MoP Generation plan created", plan)
        await self._emit(
            mop.run_id,
            "reasoning_summary",
            "MoP planning summary",
            {"reasoning_summary": plan_model.reasoning_summary},
        )
        await self._emit_safe_summary(
            mop.run_id,
            "plan",
            "Planner safe reasoning summary",
            (
                f"{plan_model.reasoning_summary} The planner produced {len(plan.get('steps') or [])} "
                "bounded steps: scope validation, read-only evidence collection, MoP-agent drafting, "
                "verification, artifact rendering, and optional Github export."
            ),
            {"step_count": len(plan.get("steps") or []), "steps": plan.get("steps") or []},
        )

        allowed = set(self.settings.mop_allowed_namespace_list)
        namespace_valid = mop.namespace in allowed
        await self._emit_ephemeral(
            mop.run_id,
            "namespace",
            "Checking namespace policy boundary.",
            "The workflow stays inside the configured read-only MoP Generation ODD.",
        )
        await self._emit(
            mop.run_id,
            "namespace_validated",
            "MoP namespace policy check completed",
            {
                "namespace": mop.namespace,
                "valid": namespace_valid,
                "allowed_namespaces": sorted(allowed),
            },
        )
        await self._emit_safe_summary(
            mop.run_id,
            "scope",
            "Scope guardrail safe reasoning summary",
            (
                f"Namespace {mop.namespace} was {'inside' if namespace_valid else 'outside'} the configured "
                f"MoP allowlist for environment {mop.target_environment}. Target namespace binding is deferred to MoP Execution; generation uses placeholder {mop.target_namespace}. The run remains read-only and "
                "will not perform Kubernetes or Helm mutations."
            ),
            {
                "namespace": mop.namespace,
                "target_environment": mop.target_environment,
                "target_namespace_placeholder": mop.target_namespace,
                "allowed_namespaces": sorted(allowed),
                "valid": namespace_valid,
            },
        )
        if not namespace_valid:
            final_report = f"MoP Generation blocked: namespace '{mop.namespace}' is not allowlisted."
            self.repository.update_status(mop.run_id, "failed", final_report)
            await self._emit(
                mop.run_id,
                "run_failed",
                "MoP Generation blocked by namespace policy",
                {"final_report": final_report, "allowed_namespaces": sorted(allowed)},
            )
            return

        await self._emit_ephemeral(
            mop.run_id,
            "k8s_evidence",
            "Collecting Kubernetes evidence.",
            "The k8s-inspector MCP adapter is reading namespace state without mutations.",
        )
        k8s_result = await self._tool(
            mop,
            "step_k8s_evidence",
            "mop.k8s_inspector",
            self.k8s_inspector,
            {
                "tool_name": "k8s_get_namespace_summary",
                "arguments": {
                    "namespace": mop.namespace,
                    "include_events": mop.analysis_depth != "fast",
                    "include_configmaps": mop.analysis_depth == "deep",
                    "change_intent": mop.change_intent,
                },
            },
        )
        await self._emit(
            mop.run_id,
            "k8s_evidence_completed",
            f"Kubernetes evidence completed with status {k8s_result.status}",
            {"result": k8s_result.model_dump()},
        )
        await self._emit_ephemeral(
            mop.run_id,
            "k8s_evidence_result",
            "Interpreting Kubernetes evidence result.",
            self._tool_safe_reasoning("k8s-inspector", k8s_result),
        )
        await self._emit_safe_summary(
            mop.run_id,
            "k8s",
            "Kubernetes evidence safe reasoning summary",
            self._tool_safe_reasoning("k8s-inspector", k8s_result),
            {"status": k8s_result.status, "result": k8s_result.model_dump()},
        )

        await self._emit_ephemeral(
            mop.run_id,
            "helm_evidence",
            "Collecting Helm release evidence.",
            "The helm-manager MCP adapter is reading release status and rollback candidates.",
        )
        helm_result = await self._tool(
            mop,
            "step_helm_evidence",
            "mop.helm_manager",
            self.helm_manager,
            {
                "tool_name": "helm_release_status" if mop.helm_release else "helm_list_releases",
                "arguments": {
                    "namespace": mop.namespace,
                    "release_name": mop.helm_release,
                    "include_history": mop.analysis_depth == "deep",
                },
            },
        )
        await self._emit(
            mop.run_id,
            "helm_evidence_completed",
            f"Helm evidence completed with status {helm_result.status}",
            {"result": helm_result.model_dump()},
        )
        await self._emit_ephemeral(
            mop.run_id,
            "helm_evidence_result",
            "Interpreting Helm evidence result.",
            self._tool_safe_reasoning("helm-manager", helm_result),
        )
        await self._emit_safe_summary(
            mop.run_id,
            "helm",
            "Helm evidence safe reasoning summary",
            self._tool_safe_reasoning("helm-manager", helm_result),
            {"status": helm_result.status, "result": helm_result.model_dump()},
        )

        helm_chart_hints = self._helm_chart_hints(helm_result)
        await self._emit_ephemeral(
            mop.run_id,
            "mop_agent",
            "Calling MoP creation agent for the initial document.",
            "The MoP creation agent prepares the professional MoP bundle and PDF from Kubernetes and Helm evidence.",
        )
        mop_agent_result = await self._tool(
            mop,
            "step_mop_creation_agent",
            "mop.creation_agent",
            self.mop_creation_agent,
            {
                "tool_name": "mop_creation_generate",
                "arguments": {
                    "namespace": mop.namespace,
                    "target_environment": mop.target_environment,
                    "target_namespace_placeholder": mop.target_namespace,
                    "change_intent": mop.change_intent,
                    "helm_release": mop.helm_release,
                    "implementation_window": mop.implementation_window,
                    "analysis_depth": mop.analysis_depth,
                    "k8s_evidence": k8s_result.model_dump(),
                    "helm_evidence": helm_result.model_dump(),
                    "helm_chart_hints": helm_chart_hints,
                    "mode": "read-only",
                },
            },
        )
        await self._emit(
            mop.run_id,
            "mop_agent_completed",
            f"MoP creation agent completed with status {mop_agent_result.status}",
            {"result": mop_agent_result.model_dump()},
        )
        await self._emit_ephemeral(
            mop.run_id,
            "mop_agent_result",
            "Interpreting MoP creation agent output.",
            self._tool_safe_reasoning("mop-creation-agent", mop_agent_result),
        )
        await self._emit_safe_summary(
            mop.run_id,
            "mop_agent",
            "MoP creation agent safe reasoning summary",
            self._tool_safe_reasoning("mop-creation-agent", mop_agent_result),
            {"status": mop_agent_result.status, "result": mop_agent_result.model_dump()},
        )

        await self._emit(mop.run_id, "draft_started", "Drafting MoP artifact", start_payload)
        await self._emit_ephemeral(
            mop.run_id,
            "draft",
            "Drafting the MoP from collected evidence.",
            "The selected model is turning MCP outputs into a human-reviewable Markdown document.",
        )
        agent_artifact_payloads = await self._download_mop_agent_artifacts(mop_agent_result)
        tool_results = {
            "k8s_inspector": k8s_result.model_dump(),
            "helm_manager": helm_result.model_dump(),
            "mop_creation_agent": mop_agent_result.model_dump(),
            "mop_creation_agent_downloaded_artifacts": sorted(agent_artifact_payloads),
        }
        draft = await self.report_writer.run(
            namespace=mop.namespace,
            change_intent=mop.change_intent,
            target_environment=mop.target_environment,
            helm_release=mop.helm_release,
            plan=plan,
            k8s_result=k8s_result.model_dump(),
            helm_result=helm_result.model_dump(),
            mop_agent_result=mop_agent_result.model_dump(),
            model_profile=mop.model_profile,
        )
        draft_payload = draft.model_dump()
        draft_payload["model_profile"] = selected_model
        final_report = draft.markdown
        await self._emit(
            mop.run_id,
            "draft_completed",
            "MoP Markdown draft completed",
            {
                "preview": final_report[:2000],
                "source_evidence_summary": draft.source_evidence_summary,
                "limitations": draft.limitations,
                "reasoning_summary": draft.reasoning_summary,
                "model_profile": selected_model,
            },
        )
        await self._emit_safe_summary(
            mop.run_id,
            "draft",
            "Draft writer safe reasoning summary",
            (
                f"{draft.reasoning_summary} Source evidence summary: {draft.source_evidence_summary} "
                f"Known limitations captured in the draft: {self._compact_list(draft.limitations)}"
            ),
            {
                "source_evidence_summary": draft.source_evidence_summary,
                "limitations": draft.limitations,
                "markdown_length": len(final_report),
            },
        )
        await self.logger.llm_review(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="draft",
            user_intent=mop.change_intent,
            plan=draft_payload,
            reasoning_summary=draft.reasoning_summary,
            final_answer=final_report,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )

        await self._emit_ephemeral(
            mop.run_id,
            "validate",
            "Validating MoP structure and evidence coverage.",
            "The verifier checks rollback, validation, risk, and human-review sections.",
        )
        verification = await self.verifier.run(
            markdown=final_report,
            namespace=mop.namespace,
            tool_results=tool_results,
            model_profile=mop.model_profile,
        )
        validation = verification.model_dump()
        validation["model_profile"] = selected_model
        await self._emit(
            mop.run_id,
            "validation_completed",
            "MoP draft validation completed",
            validation,
        )
        await self._emit_safe_summary(
            mop.run_id,
            "validate",
            "Verifier safe reasoning summary",
            (
                f"{verification.reasoning_summary} Validation result: {validation.get('message')}. "
                f"Missing sections: {self._compact_list(validation.get('missing_sections') or [])}. "
                f"Evidence gaps: {self._compact_list(validation.get('evidence_gaps') or [])}."
            ),
            {
                "valid": validation.get("valid"),
                "confidence": validation.get("confidence"),
                "checks": validation.get("checks"),
                "missing_sections": validation.get("missing_sections"),
                "evidence_gaps": validation.get("evidence_gaps"),
            },
        )
        await self.logger.llm_review(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="verify_draft",
            user_intent=mop.change_intent,
            plan=validation,
            reasoning_summary=verification.reasoning_summary,
            final_answer=final_report,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )

        await self._emit_ephemeral(
            mop.run_id,
            "recover",
            "Selecting bounded continue, retry, or escalation behavior.",
            "The agent is using tool status and validation results to decide next action.",
        )
        recovery = await self.recovery.run(
            tool_results=tool_results,
            verification=validation,
            model_profile=mop.model_profile,
        )
        recovery_payload = recovery.model_dump()
        recovery_payload["model_profile"] = selected_model
        await self._emit(
            mop.run_id,
            "recovery_recommendation",
            f"Recovery recommendation: {recovery.action}",
            recovery_payload,
        )
        await self._emit_safe_summary(
            mop.run_id,
            "recover",
            "Recovery safe reasoning summary",
            (
                f"{recovery.reasoning_summary} Selected action: {recovery.action}. "
                f"Retryable: {recovery.retryable}. Escalation required: {recovery.escalation_required}. "
                f"Recommendation: {self._compact_list(recovery.recommendations)}"
            ),
            {
                "action": recovery.action,
                "retryable": recovery.retryable,
                "escalation_required": recovery.escalation_required,
                "recommendations": recovery.recommendations,
            },
        )
        await self.logger.llm_review(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="recover_or_continue",
            user_intent=mop.change_intent,
            plan=recovery_payload,
            reasoning_summary=recovery.reasoning_summary,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )

        await self._emit_ephemeral(
            mop.run_id,
            "artifacts",
            "Generating MoP deployment artifact bundle.",
            "The final MoP, installation notes, machine execution plan, metadata, deployment tree, and zip archive are being assembled from the controlling MoP bundle spec.",
        )
        bundle = MopBundleBuilder(
            settings=self.settings,
            storage_root=self.artifact_service.storage_root,
        ).build(
            run_id=mop.run_id,
            user_id=mop.user_id,
            source_namespace=mop.namespace,
            target_namespace=mop.target_namespace,
            target_environment=mop.target_environment,
            change_intent=mop.change_intent,
            helm_release=mop.helm_release,
            implementation_window=mop.implementation_window,
            analysis_depth=mop.analysis_depth,
            model_profile=selected_model,
            plan=plan,
            classification=classification_payload,
            tool_results=tool_results,
            validation=validation,
            recovery=recovery_payload,
            draft_markdown=final_report,
            source_evidence_summary=draft.source_evidence_summary,
            limitations=draft.limitations,
            agent_artifact_payloads=agent_artifact_payloads,
        )
        final_report = bundle.human_mop_markdown
        artifact_metadata = {
            "namespace": mop.namespace,
            "target_namespace_placeholder": mop.target_namespace,
            "target_environment": mop.target_environment,
            "change_intent": mop.change_intent,
            "classification": classification_payload,
            "plan": plan,
            "tool_results": tool_results,
            "validation": validation,
            "recovery": recovery_payload,
            "model_profile": selected_model,
            "bundle_root": str(bundle.bundle_root),
            "bundle_id": bundle.bundle_id,
            "bundle_timestamp": bundle.timestamp,
            "bundle_validation": bundle.validation,
            "artifact_index": bundle.artifact_index,
            "warnings": bundle.warnings,
        }
        artifacts = []
        markdown_artifact = None
        pdf_artifact = None
        for bundle_file in bundle.files:
            file_metadata = artifact_metadata | {
                "filename": bundle_file.filename,
                "bundle_relative_path": bundle_file.relative_path,
            }
            if bundle_file.mime_type.startswith("text/markdown"):
                artifact = self.artifact_service.save_markdown(
                    run_id=mop.run_id,
                    user_id=mop.user_id,
                    artifact_type=bundle_file.artifact_type,
                    title=bundle_file.title,
                    markdown=bundle_file.absolute_path.read_text(encoding="utf-8"),
                    metadata=file_metadata,
                )
            else:
                artifact = self.artifact_service.save_bytes(
                    run_id=mop.run_id,
                    user_id=mop.user_id,
                    artifact_type=bundle_file.artifact_type,
                    title=bundle_file.title,
                    content=bundle_file.absolute_path.read_bytes(),
                    filename_suffix=bundle_file.absolute_path.suffix or ".bin",
                    mime_type=bundle_file.mime_type,
                    metadata=file_metadata,
                )
            artifacts.append(artifact)
            if bundle_file.artifact_type == "mop":
                markdown_artifact = artifact
            elif bundle_file.artifact_type == "mop_pdf":
                pdf_artifact = artifact
            await self._emit(
                mop.run_id,
                "artifact_created",
                f"{bundle_file.filename} artifact saved",
                {
                    "artifact": artifact,
                    "artifact_url": f"/api/artifacts/{artifact['artifact_id']}",
                    "bundle_relative_path": bundle_file.relative_path,
                    "preview": final_report[:2000] if bundle_file.artifact_type == "mop" else None,
                },
            )
        if markdown_artifact is None or pdf_artifact is None:
            raise RuntimeError("MoP bundle did not produce primary Markdown and PDF artifacts")
        bundle_artifact = next(
            (artifact for artifact in artifacts if artifact.get("artifact_type") == "mop_bundle_zip"),
            None,
        )
        await self._emit(
            mop.run_id,
            "artifact_bundle_created",
            "Complete MoP bundle saved",
            {
                "artifact_count": len(artifacts),
                "bundle_artifact": bundle_artifact,
                "bundle_artifact_url": f"/api/artifacts/{bundle_artifact['artifact_id']}"
                if bundle_artifact
                else None,
                "bundle_validation": bundle.validation,
                "artifact_types": [artifact.get("artifact_type") for artifact in artifacts],
            },
        )
        await self._emit_safe_summary(
            mop.run_id,
            "artifacts",
            "Artifact bundle rendering safe reasoning summary",
            (
                f"Generated a MoP artifact bundle rooted at {bundle.bundle_root}. The bundle includes "
                "operator Markdown, PDF, installation notes, machine execution plan, artifact metadata, "
                "deployment-artifacts.zip, complete mop-bundle.zip, and deployment-artifacts index files. "
                f"Bundle validation: {bundle.validation.get('message')}."
            ),
            {
                "artifact_count": len(artifacts),
                "bundle_root": str(bundle.bundle_root),
                "bundle_validation": bundle.validation,
                "warnings": bundle.warnings,
                "primary_markdown_artifact_id": markdown_artifact["artifact_id"],
                "primary_pdf_artifact_id": pdf_artifact["artifact_id"],
            },
        )
        await self.logger.event(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="save_artifacts",
            event_type="artifact_created",
            message="MoP artifact bundle saved",
            payload={"artifacts": artifacts, "validation": validation, "bundle_validation": bundle.validation},
            workflow_type=WORKFLOW_TYPE,
        )

        final_status = "completed" if validation["valid"] and bundle.validation.get("valid") else "failed"
        artifact_publish = None
        if final_status == "completed" and self.artifact_publisher and self.artifact_publisher.is_enabled:
            await self._emit_ephemeral(
                mop.run_id,
                "publish",
                "Exporting MoP bundle to Github.",
                "The complete unextracted mop-bundle.zip is being committed to the configured artifact repository.",
            )
            await self._emit(
                mop.run_id,
                "artifact_publish_started",
                "Exporting MoP bundle to Github",
                {
                    "target": self.artifact_publisher.target_summary(),
                    "job_name": self._artifact_job_name(mop),
                    "artifact_ids": [artifact["artifact_id"] for artifact in artifacts],
                },
            )
            try:
                artifact_publish = await self._publish_artifacts(
                    mop=mop,
                    artifacts=artifacts,
                    bundle=bundle,
                )
                await self._emit(
                    mop.run_id,
                    "artifact_publish_completed",
                    "MoP bundle exported to Github",
                    {"artifact_publish": artifact_publish},
                )
                await self._emit_safe_summary(
                    mop.run_id,
                    "publish",
                    "Github export safe reasoning summary",
                    (
                        "The run reached the export stage successfully, so the unextracted mop-bundle.zip "
                        "was published to the configured artifact repository for later Activity timeline lookup."
                    ),
                    {"artifact_publish": artifact_publish},
                )
                await self.logger.event(
                    run_id=mop.run_id,
                    user_id=mop.user_id,
                    graph_node="publish_artifacts",
                    event_type="artifact_publish_completed",
                    message="MoP bundle exported to Github",
                    payload={"artifact_publish": artifact_publish},
                    workflow_type=WORKFLOW_TYPE,
                )
            except Exception as exc:
                final_status = "failed"
                artifact_publish = {
                    "status": "failed",
                    "target": self.artifact_publisher.target_summary(),
                    "error": {"type": exc.__class__.__name__, "message": str(exc)},
                }
                await self._emit(
                    mop.run_id,
                    "artifact_publish_failed",
                    "MoP artifact Github export failed",
                    {"artifact_publish": artifact_publish},
                )
                await self._emit_safe_summary(
                    mop.run_id,
                    "publish",
                    "Github export safe reasoning summary",
                    (
                        "Local Markdown and PDF artifacts were created, but Github export did not complete. "
                        "The run is marked for review so the publish error can be corrected without losing local artifacts."
                    ),
                    {"artifact_publish": artifact_publish},
                )
                await self.logger.event(
                    run_id=mop.run_id,
                    user_id=mop.user_id,
                    graph_node="publish_artifacts",
                    event_type="artifact_publish_failed",
                    message="MoP artifact Github export failed",
                    payload={"artifact_publish": artifact_publish},
                    workflow_type=WORKFLOW_TYPE,
                )

        await self._emit_safe_summary(
            mop.run_id,
            "complete",
            "Final safe reasoning summary",
            (
                f"The MoP run ended with status {final_status}. Validation was "
                f"{'successful' if validation.get('valid') else 'not successful'}, recovery action was "
                f"{recovery.action}, and artifact export status was "
                f"{(artifact_publish or {}).get('status', 'not attempted')}."
            ),
            {
                "final_status": final_status,
                "validation_valid": validation.get("valid"),
                "recovery_action": recovery.action,
                "artifact_publish": artifact_publish,
            },
        )
        self.repository.update_status(mop.run_id, final_status, final_report)
        await self._emit(
            mop.run_id,
            "run_completed" if final_status == "completed" else "run_failed",
            f"MoP Generation {final_status}",
            {
                "final_report": final_report,
                "classification": classification_payload,
                "plan": plan,
                "tool_results": tool_results,
                "validation": validation,
                "recovery": recovery_payload,
                "model_profile": selected_model,
                "artifact": markdown_artifact,
                "artifact_url": f"/api/artifacts/{markdown_artifact['artifact_id']}",
                "artifacts": artifacts,
                "artifact_urls": [f"/api/artifacts/{artifact['artifact_id']}" for artifact in artifacts],
                "artifact_publish": artifact_publish,
            },
        )
        await self.logger.event(
            run_id=mop.run_id,
            user_id=mop.user_id,
            graph_node="final_report",
            event_type="run_completed" if final_status == "completed" else "run_failed",
            message=f"MoP Generation {final_status}",
            payload={
                "artifacts": artifacts,
                "validation": validation,
                "recovery": recovery_payload,
                "tool_results": tool_results,
                "artifact_publish": artifact_publish,
            },
            workflow_type=WORKFLOW_TYPE,
        )


    @staticmethod
    def _compact_list(items) -> str:
        values = [str(item) for item in (items or []) if str(item).strip()]
        if not values:
            return "none"
        return "; ".join(values[:4]) + ("; ..." if len(values) > 4 else "")

    @staticmethod
    def _stage_label(phase: str) -> str:
        labels = {
            "classify": "Classifier",
            "plan": "Planner",
            "scope": "Scope",
            "k8s": "K8s Evidence",
            "helm": "Helm Evidence",
            "mop_agent": "MoP Agent",
            "draft": "Draft Writer",
            "validate": "Verifier",
            "recover": "Recovery",
            "artifacts": "Artifacts",
            "publish": "Github Export",
            "complete": "Final",
        }
        return labels.get(phase, phase.replace("_", " ").title())

    @staticmethod
    def _tool_safe_reasoning(label: str, result: ToolExecutionResult) -> str:
        if result.status == "success":
            output = result.output or {}
            observed = []
            if isinstance(output, dict):
                candidate = output.get("result") if isinstance(output.get("result"), dict) else output
                if isinstance(candidate, dict):
                    observed = sorted(str(key) for key in candidate.keys())[:6]
            field_note = f" Key evidence fields observed: {', '.join(observed)}." if observed else ""
            return (
                f"{label} returned read-only evidence successfully, so the workflow can use it as source "
                f"context for the MoP draft without performing cluster mutation.{field_note}"
            )
        error = result.error or {}
        message = error.get("message") or "No successful evidence payload was returned."
        if result.status in {"blocked", "approval_required"}:
            return (
                f"{label} did not provide usable evidence because the request needs review or approval: "
                f"{message}. The run continues with an explicit evidence limitation instead of inventing facts."
            )
        return (
            f"{label} evidence is limited because the tool returned status {result.status}: {message}. "
            "The draft must disclose this gap and avoid unsupported operational claims."
        )



    def _helm_chart_hints(self, helm_result: ToolExecutionResult) -> list[dict]:
        body = (helm_result.output or {}).get("result") if isinstance(helm_result.output, dict) else None
        if not isinstance(body, dict):
            return []
        output = body.get("output")
        candidates = output if isinstance(output, list) else [output] if isinstance(output, dict) else []
        if not candidates and (body.get("release_name") or body.get("chart_ref")):
            candidates = [body]
        hints = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            release_name = str(item.get("name") or item.get("release_name") or body.get("release_name") or "").strip()
            chart_ref = str(item.get("chart_ref") or body.get("chart_ref") or item.get("chart") or "").strip()
            chart_name, chart_version = self._split_helm_chart(chart_ref)
            if not release_name and chart_name:
                release_name = chart_name
            if not release_name:
                continue
            hint = {
                "release_name": release_name,
                "target_release_name": f"{self.settings.mop_generated_name_prefix}-{release_name}",
                "chart_ref": chart_ref or None,
                "chart_name": chart_name or None,
                "chart_version": chart_version or None,
                "repo_name": None,
                "repo_url": None,
                "source_type": "unknown",
                "values_overrides": {},
            }
            if chart_name == "signoz" or release_name == "signoz":
                hint.update(
                    {
                        "chart_ref": "signoz/signoz",
                        "chart_name": "signoz",
                        "chart_version": chart_version or "0.122.0",
                        "repo_name": "signoz",
                        "repo_url": "https://charts.signoz.io",
                        "source_type": "public",
                    }
                )
            hints.append({key: value for key, value in hint.items() if value is not None})
        return hints

    @staticmethod
    def _split_helm_chart(chart: str) -> tuple[str | None, str | None]:
        value = str(chart or "").strip()
        if not value:
            return None, None
        if "/" in value and not value.rsplit("/", 1)[-1].count("-"):
            return value.rsplit("/", 1)[-1], None
        name, sep, version = value.rpartition("-")
        if sep and version[:1].isdigit():
            return name or value, version
        return value.rsplit("/", 1)[-1], None

    async def _download_mop_agent_artifacts(self, mop_agent_result: ToolExecutionResult) -> dict[str, bytes]:
        body = (mop_agent_result.output or {}).get("result") if isinstance(mop_agent_result.output, dict) else None
        if not isinstance(body, dict):
            return {}
        mop_id = str(body.get("mop_id") or "").strip()
        files = body.get("artifact_files") if isinstance(body.get("artifact_files"), list) else []
        if not mop_id or not files:
            return {}
        base_url = str(self.settings.mop_creation_agent_mcp_url or self.settings.mop_creation_agent_url or "").rstrip("/")
        if not base_url:
            return {}
        headers = {"x-api-key": self.settings.mop_creation_agent_api_key} if self.settings.mop_creation_agent_api_key else {}
        downloads: dict[str, bytes] = {}
        async with httpx.AsyncClient(timeout=float(self.settings.mop_creation_agent_timeout_seconds)) as client:
            for item in files:
                rel_path = str((item or {}).get("path") or "").strip()
                if not rel_path or rel_path.startswith(("/", "\\")) or ".." in rel_path.replace("\\", "/").split("/"):
                    continue
                response = await client.get(
                    f"{base_url}/mop-creation/{mop_id}/artifacts/download",
                    params={"path": rel_path},
                    headers=headers,
                )
                response.raise_for_status()
                downloads[rel_path] = response.content
        return downloads

    async def _publish_artifacts(
        self,
        *,
        mop: MopGenerationInput,
        artifacts: list[dict],
        bundle: MopBundleResult,
    ) -> dict:
        if not self.artifact_publisher:
            return {"status": "disabled", "message": "No artifact publisher configured."}
        bundle_file = next(
            (item for item in bundle.publish_files if item.artifact_type == "mop_bundle_zip"),
            None,
        )
        if not bundle_file:
            raise RuntimeError("MoP bundle did not produce mop-bundle.zip for publishing")
        artifact = next(
            (
                item
                for item in artifacts
                if item.get("artifact_type") == "mop_bundle_zip"
                or (item.get("metadata") or {}).get("filename") == bundle_file.filename
            ),
            None,
        )
        payloads = [
            ArtifactPublishPayload(
                filename="mop-bundle.zip",
                content=self.artifact_service.read_artifact_bytes(artifact["storage_path"])
                if artifact
                else bundle_file.absolute_path.read_bytes(),
                artifact_id=artifact.get("artifact_id") if artifact else None,
                mime_type=artifact.get("mime_type") if artifact else bundle_file.mime_type,
            )
        ]
        return await self.artifact_publisher.publish_artifact_files(
            run_id=mop.run_id,
            github_url=f"k8s://{mop.target_environment}/{mop.namespace}",
            job_name=self._artifact_job_name(mop),
            files=payloads,
            commit_label="MoP bundle zip",
        )

    @staticmethod
    def _published_filename(filename: str, artifact_type: str) -> str:
        if artifact_type == "mop":
            return "mop.md"
        if artifact_type == "mop_pdf":
            return "mop.pdf"
        if artifact_type == "mop_bundle_zip":
            return "mop-bundle.zip"
        return filename

    def _artifact_job_name(self, mop: MopGenerationInput) -> str:
        return f"{self.settings.mop_artifact_folder_prefix}_{mop.namespace}"

    @staticmethod
    def _artifact_title(mop: MopGenerationInput) -> str:
        return f"MoP - {mop.namespace}"

    async def _tool(
        self,
        mop: MopGenerationInput,
        step_id: str,
        tool_name: str,
        tool,
        arguments: dict,
    ) -> ToolExecutionResult:
        request = ToolExecutionRequest(
            run_id=mop.run_id,
            step_id=step_id,
            tool_name=tool_name,
            workflow_type=WORKFLOW_TYPE,
            environment=mop.target_environment,
            namespace=mop.namespace,
            user_id=mop.user_id,
            arguments=arguments,
        )
        await self._emit(
            mop.run_id,
            "tool_call_started",
            f"Starting {tool_name}",
            {"tool_name": tool_name, "arguments": arguments},
        )
        await self._emit_ephemeral(
            mop.run_id,
            f"{step_id}_policy",
            "Checking policy and tool contract before execution.",
            f"{tool_name} remains read-only and must pass the BOS Genesis guardrails.",
        )
        policy_guard = PolicyGuard(settings=self.settings, tool_registry=self.tool_registry)
        policy_decision = policy_guard.evaluate_tool(
            request,
            user_roles=mop.user_roles or [],
        )
        if policy_decision.decision == "deny":
            result = ToolExecutionResult(
                status="blocked",
                error={
                    "code": "POLICY_DENIED",
                    "message": "; ".join(policy_decision.reasons),
                    "retryable": False,
                },
            )
            duration_ms = 0
        elif policy_decision.decision == "approval_required":
            approval = self.approval_service.create_request(
                request=request,
                requested_by_user_id=mop.user_id,
                decision=policy_decision,
            )
            result = ToolExecutionResult(
                status="approval_required",
                output={"approval": approval},
                error={
                    "code": "APPROVAL_REQUIRED",
                    "message": "; ".join(policy_decision.reasons),
                    "retryable": False,
                },
            )
            duration_ms = 0
        else:
            await self._emit_ephemeral(
                mop.run_id,
                f"{step_id}_wait",
                "Waiting for external agent evidence.",
                f"{tool_name} is collecting read-only MoP evidence through MCP.",
            )
            result, duration_ms = await tool.execute(request)
        self.repository.add_tool_call(
            run_id=mop.run_id,
            tool_name=tool_name,
            status=result.status,
            request_json=request.model_dump(),
            response_json=result.model_dump(),
        )
        await self.logger.tool(
            run_id=mop.run_id,
            tool_name=tool_name,
            tool_category=tool_name.split(".", 1)[0],
            status=result.status,
            request=request.model_dump(),
            response_summary=result.model_dump(),
            error_message=(result.error or {}).get("message", ""),
            duration_ms=duration_ms,
            risk_level=policy_decision.risk_level,
            policy_decision=policy_decision.decision,
        )
        await self._emit(
            mop.run_id,
            "tool_call_completed",
            f"Completed {tool_name} with status {result.status}",
            {"tool_name": tool_name, "result": result.model_dump()},
        )
        return result

    def _model_profile_payload(self, model_profile: str | None) -> dict:
        if hasattr(self.llm, "describe_model_profile"):
            with suppress(Exception):
                profile = self.llm.describe_model_profile(model_profile)
                if isinstance(profile, dict):
                    return profile
        fallback_profile = model_profile or self.settings.llm_default_model_profile or "default"
        return {
            "profile_id": fallback_profile,
            "label": fallback_profile,
            "model_display": fallback_profile,
        }
