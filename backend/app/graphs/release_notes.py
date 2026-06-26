from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlparse

from backend.app.approvals import ApprovalService
from backend.app.artifacts import ArtifactService
from backend.app.chains.release_notes import (
    ReleaseNoteIntentClassifierChain,
    ReleaseNotePlannerChain,
    ReleaseNoteRecoveryRecommendationChain,
    ReleaseNoteReportWriterChain,
    ReleaseNoteVerifierChain,
)
from backend.app.db.database import RunRepository
from backend.app.graphs.event_bus import RunEventBus
from backend.app.llm.azure_gpt5 import AzureGpt5Service
from backend.app.logging.postgres_logger import PostgresLogger
from backend.app.policy.evaluator import PolicyGuard
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult
from backend.app.tools.registry import ToolRegistry
from backend.app.tools.release_note_agent import ReleaseNoteAgentTool

WORKFLOW_TYPE = "release_note_creation"


@dataclass
class ReleaseNoteInput:
    run_id: str
    user_id: str
    github_url: str
    release_name: str | None = None
    branch: str | None = None
    tag: str | None = None
    commit_sha: str | None = None
    analysis_depth: str = "fast"
    model_profile: str | None = None
    user_roles: list[str] | None = None


class ReleaseNoteGraph:
    def __init__(
        self,
        *,
        repository: RunRepository,
        event_bus: RunEventBus,
        logger: PostgresLogger,
        llm: AzureGpt5Service,
        release_note_agent: ReleaseNoteAgentTool,
        artifact_service: ArtifactService,
        tool_registry: ToolRegistry,
        policy_guard: PolicyGuard | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.repository = repository
        self.event_bus = event_bus
        self.logger = logger
        self.llm = llm
        self.intent_classifier = ReleaseNoteIntentClassifierChain(llm)
        self.planner = ReleaseNotePlannerChain(llm)
        self.verifier = ReleaseNoteVerifierChain(llm)
        self.recovery = ReleaseNoteRecoveryRecommendationChain(llm)
        self.report_writer = ReleaseNoteReportWriterChain(llm)
        self.release_note_agent = release_note_agent
        self.artifact_service = artifact_service
        self.tool_registry = tool_registry
        self.policy_guard = policy_guard or PolicyGuard(
            settings=llm.settings,
            tool_registry=tool_registry,
        )
        self.approval_service = approval_service or ApprovalService(
            repository=repository,
            settings=llm.settings,
            policy_guard=self.policy_guard,
        )

    async def _emit(self, run_id: str, event_type: str, message: str, payload: dict) -> None:
        event = self.repository.add_event(run_id, event_type, message, payload)
        await self.event_bus.publish(run_id, event)

    async def _tool(
        self,
        release_note: ReleaseNoteInput,
        step_id: str,
        tool_name: str,
        tool,
        arguments: dict,
    ) -> ToolExecutionResult:
        request = ToolExecutionRequest(
            run_id=release_note.run_id,
            step_id=step_id,
            tool_name=tool_name,
            workflow_type=WORKFLOW_TYPE,
            user_id=release_note.user_id,
            arguments=arguments,
        )
        await self._emit(
            release_note.run_id,
            "tool_call_started",
            f"Starting {tool_name}",
            {"tool_name": tool_name, "arguments": arguments},
        )
        policy_decision = self.policy_guard.evaluate_tool(
            request,
            user_roles=release_note.user_roles or [],
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
                requested_by_user_id=release_note.user_id,
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
            result, duration_ms = await tool.execute(request)
        self.repository.add_tool_call(
            run_id=release_note.run_id,
            tool_name=tool_name,
            status=result.status,
            request_json=request.model_dump(),
            response_json=result.model_dump(),
        )
        await self.logger.tool(
            run_id=release_note.run_id,
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
            release_note.run_id,
            "tool_call_completed",
            f"Completed {tool_name} with status {result.status}",
            {"tool_name": tool_name, "result": result.model_dump()},
        )
        return result

    async def run(self, release_note: ReleaseNoteInput) -> None:
        try:
            await self._run(release_note)
        except Exception as exc:
            error_payload = {
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                }
            }
            final_report = f"Release-note generation failed unexpectedly: {exc}"
            self.repository.update_status(release_note.run_id, "failed", final_report)
            with suppress(Exception):
                await self._emit(
                    release_note.run_id,
                    "run_failed",
                    "Release-note generation failed unexpectedly",
                    error_payload,
                )
            with suppress(Exception):
                await self.logger.event(
                    run_id=release_note.run_id,
                    user_id=release_note.user_id,
                    graph_node="unhandled_error",
                    event_type="run_failed",
                    message="Release-note generation failed unexpectedly",
                    payload=error_payload,
                    workflow_type=WORKFLOW_TYPE,
                )

    async def _run(self, release_note: ReleaseNoteInput) -> None:
        self.repository.update_status(release_note.run_id, "running")
        selected_model = self._model_profile_payload(release_note.model_profile)
        model_deployment = str(
            selected_model.get("model_display")
            or selected_model.get("deployment")
            or selected_model.get("model_name")
            or selected_model.get("profile_id")
            or "not_configured"
        )
        start_payload = {"github_url": release_note.github_url, "model_profile": selected_model}
        await self._emit(
            release_note.run_id,
            "run_started",
            "Release-note generation started",
            start_payload,
        )
        await self.logger.event(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="start",
            event_type="run_started",
            message="Release-note generation started",
            payload=start_payload,
            workflow_type=WORKFLOW_TYPE,
        )

        classification = await self.intent_classifier.run(
            user_text=f"Generate release notes for {release_note.github_url}",
            github_url=release_note.github_url,
            release_name=release_note.release_name,
            model_profile=release_note.model_profile,
        )
        classification_payload = classification.model_dump()
        classification_payload["model_profile"] = selected_model
        await self.logger.llm_review(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="classify_intent",
            user_intent=f"Generate release notes for {release_note.github_url}",
            plan=classification_payload,
            reasoning_summary=classification.reasoning_summary,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )
        await self._emit(
            release_note.run_id,
            "workflow_classified",
            f"Workflow classified as {classification.workflow_type}",
            classification_payload,
        )

        await self._emit(
            release_note.run_id,
            "planning_started",
            "Creating release-note plan",
            {"github_url": release_note.github_url, "model_profile": selected_model},
        )

        plan_model = await self.planner.run(
            github_url=release_note.github_url,
            release_name=release_note.release_name,
            branch=release_note.branch,
            tag=release_note.tag,
            commit_sha=release_note.commit_sha,
            model_profile=release_note.model_profile,
        )
        plan = plan_model.model_dump()
        plan["model_profile"] = selected_model
        await self.logger.llm_review(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="plan",
            user_intent=f"Generate release notes for {release_note.github_url}",
            plan=plan,
            reasoning_summary=plan_model.reasoning_summary,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )
        await self._emit(release_note.run_id, "plan_created", "Release-note plan created", plan)
        await self._emit(
            release_note.run_id,
            "reasoning_summary",
            "GPT-5 planning summary",
            {"reasoning_summary": plan_model.reasoning_summary},
        )

        agent_arguments = ReleaseNoteAgentTool.normalize_ref_arguments(
            {
                "github_url": release_note.github_url,
                "release_name": release_note.release_name,
                "branch": release_note.branch,
                "tag": release_note.tag,
                "commit_sha": release_note.commit_sha,
                "analysis_depth": release_note.analysis_depth,
                "output_formats": ["markdown", "html", "pdf", "json"],
            }
        )
        agent_result = await self._tool(
            release_note,
            "step_release_note_agent",
            "release_notes.agent_scan",
            self.release_note_agent,
            agent_arguments,
        )

        await self._emit(
            release_note.run_id,
            "draft_started",
            "Drafting release-note artifact",
            {
                "github_url": release_note.github_url,
                "agent_status": agent_result.status,
                "model_profile": selected_model,
            },
        )

        draft = await self.report_writer.run(
            github_url=release_note.github_url,
            release_name=release_note.release_name,
            plan=plan,
            agent_result=agent_result.model_dump(),
            model_profile=release_note.model_profile,
        )
        draft_payload = draft.model_dump()
        draft_payload["model_profile"] = selected_model
        final_report = draft.markdown.strip()
        if not final_report:
            final_report = self._fallback_markdown(release_note, agent_result)
        await self.logger.llm_review(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="draft",
            user_intent=f"Draft release notes for {release_note.github_url}",
            plan=draft_payload,
            reasoning_summary=draft.reasoning_summary,
            final_answer=final_report,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )

        verification = await self.verifier.run(
            markdown=final_report,
            github_url=release_note.github_url,
            agent_result=agent_result.model_dump(),
            plan=plan,
            model_profile=release_note.model_profile,
        )
        validation = verification.model_dump()
        validation["model_profile"] = selected_model
        await self._emit(
            release_note.run_id,
            "validation_completed",
            "Release-note draft validation completed",
            validation,
        )
        await self.logger.llm_review(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="verify_draft",
            user_intent=f"Verify release-note draft for {release_note.github_url}",
            plan=validation,
            reasoning_summary=verification.reasoning_summary,
            final_answer=final_report,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )

        recovery = await self.recovery.run(
            agent_result=agent_result.model_dump(),
            verification=validation,
            github_url=release_note.github_url,
            model_profile=release_note.model_profile,
        )
        recovery_payload = recovery.model_dump()
        recovery_payload["model_profile"] = selected_model
        await self._emit(
            release_note.run_id,
            "recovery_recommendation",
            f"Recovery recommendation: {recovery.action}",
            recovery_payload,
        )
        await self.logger.llm_review(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="recover_or_continue",
            user_intent=f"Complete release-note run for {release_note.github_url}",
            plan=recovery_payload,
            reasoning_summary=recovery.reasoning_summary,
            workflow_type=WORKFLOW_TYPE,
            model_deployment=model_deployment,
        )
        artifact = self.artifact_service.save_markdown(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            artifact_type="release_note",
            title=release_note.release_name or self._repo_name(release_note.github_url) or "Release Notes",
            markdown=final_report,
            metadata={
                "github_url": release_note.github_url,
                "classification": classification_payload,
                "validation": validation,
                "recovery": recovery_payload,
                "agent_status": agent_result.status,
                "model_profile": selected_model,
            },
        )
        await self._emit(
            release_note.run_id,
            "artifact_created",
            "Release-note Markdown artifact saved",
            {
                "artifact": artifact,
                "artifact_url": f"/api/artifacts/{artifact['artifact_id']}",
                "preview": final_report[:2000],
            },
        )
        await self.logger.event(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="save_artifact",
            event_type="artifact_created",
            message="Release-note Markdown artifact saved",
            payload={"artifact": artifact, "validation": validation},
            workflow_type=WORKFLOW_TYPE,
        )

        artifacts = [artifact]
        pdf_artifact = await self._save_pdf_artifact(
            release_note=release_note,
            agent_result=agent_result,
            markdown_artifact=artifact,
            classification=classification_payload,
            validation=validation,
            recovery=recovery_payload,
        )
        if pdf_artifact:
            artifacts.append(pdf_artifact)
            await self._emit(
                release_note.run_id,
                "artifact_created",
                "Release-note PDF artifact saved",
                {
                    "artifact": pdf_artifact,
                    "artifact_url": f"/api/artifacts/{pdf_artifact['artifact_id']}",
                },
            )
            await self.logger.event(
                run_id=release_note.run_id,
                user_id=release_note.user_id,
                graph_node="save_pdf_artifact",
                event_type="artifact_created",
                message="Release-note PDF artifact saved",
                payload={"artifact": pdf_artifact, "validation": validation},
                workflow_type=WORKFLOW_TYPE,
            )
        elif agent_result.status == "success":
            await self._emit(
                release_note.run_id,
                "artifact_warning",
                "Release-note PDF artifact was not available from release-note-agent",
                {"agent_status": agent_result.status},
            )

        final_status = "failed" if agent_result.status == "blocked" or not validation["valid"] else "completed"
        self.repository.update_status(release_note.run_id, final_status, final_report)
        await self._emit(
            release_note.run_id,
            "run_completed" if final_status == "completed" else "run_failed",
            f"Release-note generation {final_status}",
            {
                "final_report": final_report,
                "agent_status": agent_result.status,
                "artifact": artifact,
                "artifact_url": f"/api/artifacts/{artifact['artifact_id']}",
                "artifacts": artifacts,
                "artifact_urls": [f"/api/artifacts/{item['artifact_id']}" for item in artifacts],
                "classification": classification_payload,
                "validation": validation,
                "recovery": recovery_payload,
                "model_profile": selected_model,
            },
        )
        await self.logger.event(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            graph_node="final_report",
            event_type="run_completed" if final_status == "completed" else "run_failed",
            message=f"Release-note generation {final_status}",
            payload={
                "agent_status": agent_result.status,
                "artifacts": artifacts,
                "validation": validation,
                "model_profile": selected_model,
            },
            workflow_type=WORKFLOW_TYPE,
        )

    async def _save_pdf_artifact(
        self,
        *,
        release_note: ReleaseNoteInput,
        agent_result: ToolExecutionResult,
        markdown_artifact: dict,
        classification: dict,
        validation: dict,
        recovery: dict,
    ) -> dict | None:
        output = agent_result.output or {}
        job_id = output.get("job_id")
        source_pdf = self._pdf_source_artifact(output.get("artifacts") or [])
        if not job_id or not source_pdf or not hasattr(self.release_note_agent, "fetch_artifact_bytes"):
            return None
        source_artifact_id = source_pdf.get("artifact_id") or source_pdf.get("id")
        if not source_artifact_id:
            return None
        try:
            pdf_bytes, content_type = await self.release_note_agent.fetch_artifact_bytes(
                str(job_id),
                str(source_artifact_id),
            )
        except Exception as exc:
            await self._emit(
                release_note.run_id,
                "artifact_warning",
                "Release-note PDF download failed",
                {"source_artifact_id": source_artifact_id, "error": str(exc)},
            )
            return None
        if not pdf_bytes:
            return None
        title = release_note.release_name or self._repo_name(release_note.github_url) or "Release Notes"
        return self.artifact_service.save_bytes(
            run_id=release_note.run_id,
            user_id=release_note.user_id,
            artifact_type="release_note_pdf",
            title=f"{title} PDF",
            content=pdf_bytes,
            filename_suffix=".pdf",
            mime_type=content_type.split(";", 1)[0] or "application/pdf",
            metadata={
                "github_url": release_note.github_url,
                "classification": classification,
                "validation": validation,
                "recovery": recovery,
                "agent_status": agent_result.status,
                "agent_job_id": job_id,
                "source_agent_artifact_id": source_artifact_id,
                "paired_markdown_artifact_id": markdown_artifact["artifact_id"],
                "source_mime_type": source_pdf.get("content_type") or source_pdf.get("mime_type"),
                "source_relative_path": source_pdf.get("relative_path") or source_pdf.get("path"),
            },
        )

    @classmethod
    def _pdf_source_artifact(cls, artifacts: list[dict]) -> dict | None:
        for artifact in cls._flatten_artifacts(artifacts):
            artifact_type = str(artifact.get("artifact_type") or artifact.get("type") or "").lower()
            content_type = str(artifact.get("content_type") or artifact.get("mime_type") or "").lower()
            path = str(artifact.get("relative_path") or artifact.get("path") or "").lower()
            if artifact_type == "pdf" or content_type.startswith("application/pdf") or path.endswith(".pdf"):
                return artifact
        return None

    @classmethod
    def _flatten_artifacts(cls, artifacts: list[dict]) -> list[dict]:
        flattened: list[dict] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            flattened.append(artifact)
            nested = artifact.get("artifacts")
            if isinstance(nested, list):
                flattened.extend(cls._flatten_artifacts(nested))
        return flattened
    @staticmethod
    def validate_draft(markdown: str) -> dict:
        required_sections = ["## Summary", "## Source Evidence"]
        missing_sections = [section for section in required_sections if section not in markdown]
        has_title = markdown.lstrip().startswith("# ")
        has_subsection = "## " in markdown
        valid = has_title and has_subsection and not missing_sections
        return {
            "valid": valid,
            "message": "Release-note draft has required Markdown structure."
            if valid
            else "Release-note draft is missing required Markdown structure.",
            "missing_sections": missing_sections,
            "checks": {
                "has_title": has_title,
                "has_subsection": has_subsection,
            },
        }

    def _model_profile_payload(self, model_profile: str | None) -> dict:
        if hasattr(self.llm, "describe_model_profile"):
            with suppress(Exception):
                profile = self.llm.describe_model_profile(model_profile)
                if isinstance(profile, dict):
                    return profile
        fallback_profile = model_profile or "default"
        return {
            "profile_id": fallback_profile,
            "label": fallback_profile,
            "model_display": fallback_profile,
        }

    def _fallback_markdown(
        self,
        release_note: ReleaseNoteInput,
        agent_result: ToolExecutionResult,
    ) -> str:
        return "\n".join(
            [
                f"# Release Notes: {release_note.release_name or 'Draft'}",
                "",
                "## Summary",
                f"Draft generated for `{release_note.github_url}`.",
                "",
                "## Source Collection",
                f"- release-note-agent status: `{agent_result.status}`",
                "",
                "## Features",
                "- Pending detailed evidence from release-note-agent.",
                "",
                "## Fixes",
                "- Pending detailed evidence from release-note-agent.",
                "",
                "## Operational Changes",
                "- Review deployment and configuration changes before publishing.",
                "",
                "## Known Issues",
                "- None identified in the hello-world draft path.",
                "",
                "## Source Evidence",
                f"- {release_note.github_url}",
            ]
        )

    @staticmethod
    def _repo_name(github_url: str) -> str:
        path = urlparse(github_url).path.strip("/")
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return path
