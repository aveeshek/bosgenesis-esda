from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
import hashlib
import json
import re
from typing import Any
from uuid import uuid4
import zipfile

from backend.app.artifacts import ArtifactService
from backend.app.config import Settings
from backend.app.db.database import RunRepository
from backend.app.logging.redaction import redact
from backend.app.tools.mop_agents import redact_sensitive


WORKFLOW_TYPE = "mop_execution"
TERMINAL_STATES = {"completed", "succeeded", "failed", "failed_safe", "cancelled", "stopped"}


def _find_nested(payload: Any, key: str) -> Any:
    if isinstance(payload, dict):
        if key in payload:
            return payload[key]
        for value in payload.values():
            found = _find_nested(value, key)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_nested(item, key)
            if found is not None:
                return found
    return None


@dataclass(frozen=True)
class MopExecutionRunRequest:
    user_id: str
    bundle_source: dict[str, Any]
    target_namespace: str | None = None
    execution_mode: str = "dry_run_then_approval"
    model_profile: dict[str, Any] | str | None = None
    operator: str | None = None
    correlation_id: str | None = None


class MopExecutionRunStore:
    """Persists MoP Execution orchestration state in the existing run/event model.

    Ephemeral live working stream text is intentionally not accepted here. The
    UI can render it while connected, while safe summaries and execution-agent
    observations are stored as redacted events for audit and Activity views.
    """

    def __init__(self, *, repository: RunRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    def allowed_target_namespaces(self) -> list[str]:
        configured = self.settings.mop_execution_allowed_target_namespace_list
        return configured or [self.settings.mop_execution_default_target_namespace]

    def normalize_target_namespace(self, namespace: str | None) -> str:
        target = (namespace or self.settings.mop_execution_default_target_namespace or "").strip()
        allowed = self.allowed_target_namespaces()
        if not target or target not in allowed:
            raise ValueError(f"Target namespace '{target or 'not specified'}' is outside the MoP Execution ODD.")
        return target

    def create_execution_run(self, request: MopExecutionRunRequest) -> dict[str, Any]:
        target_namespace = self.normalize_target_namespace(request.target_namespace)
        correlation_id = request.correlation_id or self._correlation_id(target_namespace)
        run_id = f"mopx_{uuid4().hex}"
        bundle_label = self._bundle_label(request.bundle_source)
        goal = f"Execute MoP bundle {bundle_label} against target namespace {target_namespace}"
        self.repository.create_run(
            run_id=run_id,
            user_id=request.user_id,
            goal=goal,
            target_url=f"mop-bundle://{bundle_label}",
            namespace=target_namespace,
            workflow_type=WORKFLOW_TYPE,
        )
        event = self.repository.add_event(
            run_id,
            "run_started",
            "MoP Execution started",
            self._payload(
                {
                    "workflow_type": WORKFLOW_TYPE,
                    "bundle_source": request.bundle_source,
                    "target_namespace": target_namespace,
                    "execution_mode": request.execution_mode,
                    "correlation_id": correlation_id,
                    "operator": request.operator,
                    "model_profile": request.model_profile,
                }
            ),
        )
        return {
            "run_id": run_id,
            "workflow_type": WORKFLOW_TYPE,
            "target_namespace": target_namespace,
            "correlation_id": correlation_id,
            "status": "created",
            "event": event,
        }

    def record_preflight(self, *, run_id: str, passed: bool, checks: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "preflight_completed",
            "MoP Execution preflight completed",
            {"passed": passed, "checks": checks},
            status="running" if passed else "failed",
        )

    def record_agent_health(self, *, run_id: str, healthy: bool, response: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "agent_health_checked",
            "MoP Execution Agent health checked",
            {"healthy": healthy, "agent_health": response},
            status="running" if healthy else "failed",
        )

    def record_agent_readiness(self, *, run_id: str, ready: bool, response: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "agent_readiness_checked",
            "MoP Execution Agent readiness checked",
            {"ready": ready, "agent_readiness": response},
            status="running" if ready else "failed",
        )

    def record_agent_capabilities(
        self,
        *,
        run_id: str,
        capabilities_ok: bool,
        response: dict[str, Any],
        missing: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._record(
            run_id,
            "agent_capabilities_checked",
            "MoP Execution Agent capabilities checked",
            {"capabilities_ok": capabilities_ok, "missing_capabilities": missing or [], "agent_capabilities": response},
            status="running" if capabilities_ok else "failed",
        )

    def record_bundle_validation(self, *, run_id: str, validation: dict[str, Any]) -> dict[str, Any]:
        payload = {"bundle_validation": validation}
        bundle_id = self._find_key(validation, "bundle_id")
        if bundle_id:
            payload["bundle_id"] = bundle_id
        valid = validation.get("valid") is not False and str(validation.get("status") or "").lower() not in {"failed", "invalid"}
        return self._record(
            run_id,
            "bundle_validated",
            "MoP bundle validation completed",
            payload,
            status="running" if valid else "failed",
        )

    def record_job_created(
        self,
        *,
        run_id: str,
        job: dict[str, Any],
        job_kind: str = "dry_run",
    ) -> dict[str, Any]:
        job_id = str(self._find_key(job, "job_id") or self._find_key(job, "id") or "")
        payload: dict[str, Any] = {"job_kind": job_kind, "job": job}
        if job_id:
            payload["job_id"] = job_id
            if job_kind == "mutation":
                payload["mutation_job_id"] = job_id
            else:
                payload["dry_run_job_id"] = job_id
        event_type = "mutation_job_created" if job_kind == "mutation" else "dry_run_job_created"
        message = "Mutation execution job created" if job_kind == "mutation" else "Dry-run execution job created"
        return self._record(run_id, event_type, message, payload, status="running")

    def record_job_started(self, *, run_id: str, job_id: str, phase: str, response: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "job_started",
            f"MoP Execution job started for {phase}",
            {"job_id": job_id, "phase": phase, "response": response},
            status="running",
        )

    def record_job_state(self, *, run_id: str, job: dict[str, Any]) -> dict[str, Any]:
        state = str(self._find_key(job, "state") or job.get("status") or "unknown")
        current_phase = str(self._find_key(job, "current_phase") or self._find_key(job, "phase") or "unknown")
        status = "completed" if state in {"succeeded", "completed"} else "failed" if state in {"failed", "failed_safe"} else "running"
        return self._record(
            run_id,
            "job_state_polled",
            f"MoP Execution job state: {state}",
            {"job": job, "current_state": state, "current_phase": current_phase},
            status=status if state in TERMINAL_STATES else "running",
        )

    def record_observations(self, *, run_id: str, observations: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "observations_received",
            "MoP Execution observations received",
            {"observations": observations},
            status="running",
        )

    def record_policy_decision(self, *, run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "policy_decision_recorded",
            "MoP Execution policy decision recorded",
            {"policy_decision": decision},
            status="running",
        )

    def record_decision_required(self, *, run_id: str, context: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "decision_required",
            "MoP Execution requires external instruction",
            {"decision_required": context},
            status="running",
        )

    def record_instruction(self, *, run_id: str, instruction: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "instruction_submitted",
            "MoP Execution external instruction submitted",
            {"instruction": instruction, "response": response},
            status="running",
        )

    def record_approval(self, *, run_id: str, approval: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        agent_response = response.get("agent_response") if isinstance(response.get("agent_response"), dict) else {}
        agent_body = agent_response.get("response") if isinstance(agent_response.get("response"), dict) else agent_response
        approval_status = str(_find_nested(agent_body, "approval_status") or "").lower()
        agent_ok = _find_nested(agent_body, "ok") is True
        agent_message = str(_find_nested(agent_body, "message") or "").lower()
        accepted = (
            response.get("accepted") is True
            or approval_status in {"active", "accepted", "approved"}
            or (agent_ok and "approval submitted" in agent_message)
            or str(response.get("status") or response.get("decision") or response.get("current_state") or "").lower()
            in {"accepted", "approved", "success", "approval_accepted", "active"}
        )
        return self._record(
            run_id,
            "approval_submitted",
            "MoP Execution human approval submitted",
            {"approval": approval, "response": response, "accepted": accepted},
            status="running",
        )

    def record_reports(self, *, run_id: str, reports: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "reports_updated",
            "MoP Execution report metadata updated",
            {"reports": reports},
            status="running",
        )

    def record_validation(self, *, run_id: str, validation: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "validation_completed",
            "MoP Execution post-mutation validation completed",
            {"validation": validation},
            status="running",
        )

    def record_artifact_publish(self, *, run_id: str, publish: dict[str, Any]) -> dict[str, Any]:
        status = str(publish.get("status") or "unknown").lower()
        event_type = "artifact_publish_completed" if status in {"success", "disabled", "unchanged"} else "artifact_publish_failed"
        message = "MoP Execution report bundle publishing completed" if event_type == "artifact_publish_completed" else "MoP Execution report bundle publishing failed"
        return self._record(run_id, event_type, message, {"artifact_publish": publish}, status="running")

    def record_rollback_cleanup(self, *, run_id: str, kind: str, state: dict[str, Any]) -> dict[str, Any]:
        return self._record(
            run_id,
            "rollback_cleanup_updated",
            f"MoP Execution {kind} state updated",
            {"kind": kind, "state": state},
            status="running",
        )

    def record_safe_summary(self, *, run_id: str, stage: str, summary: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._record(
            run_id,
            "safe_reasoning_summary",
            f"Safe MoP Execution summary: {stage}",
            {"stage": stage, "summary": summary, "payload": payload or {}},
            status="running",
        )

    def mark_completed(self, *, run_id: str, final_report: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = self._record(run_id, "run_completed", "MoP Execution completed", payload or {}, status="completed")
        self.repository.update_status(run_id, "completed", final_report=final_report)
        return event

    def mark_failed(self, *, run_id: str, reason: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        event = self._record(
            run_id,
            "run_failed",
            "MoP Execution failed safe",
            {"reason": reason, **(payload or {})},
            status="failed",
        )
        self.repository.update_status(run_id, "failed", final_report=reason)
        return event

    def execution_metadata(self, run_id: str) -> dict[str, Any]:
        events = self.repository.list_events(run_id)
        metadata: dict[str, Any] = {
            "workflow_type": WORKFLOW_TYPE,
            "bundle_id": None,
            "dry_run_job_id": None,
            "mutation_job_id": None,
            "target_namespace": None,
            "correlation_id": None,
            "current_state": None,
            "current_phase": None,
            "reports": [],
            "approvals": [],
            "instructions": [],
            "policy_decisions": [],
            "rollback_cleanup": [],
            "safe_summaries": [],
        }
        for event in events:
            payload = event.get("payload") or {}
            for key in ("bundle_id", "dry_run_job_id", "mutation_job_id", "target_namespace", "correlation_id", "current_state", "current_phase"):
                value = self._find_key(payload, key)
                if value:
                    metadata[key] = value
            if event.get("event_type") == "reports_updated":
                metadata["reports"].append(payload.get("reports") or payload)
            elif event.get("event_type") == "approval_submitted":
                metadata["approvals"].append(payload)
            elif event.get("event_type") == "instruction_submitted":
                metadata["instructions"].append(payload)
            elif event.get("event_type") == "policy_decision_recorded":
                metadata["policy_decisions"].append(payload.get("policy_decision") or payload)
            elif event.get("event_type") == "rollback_cleanup_updated":
                metadata["rollback_cleanup"].append(payload)
            elif event.get("event_type") == "safe_reasoning_summary":
                metadata["safe_summaries"].append(payload)
        return metadata

    def _record(
        self,
        run_id: str,
        event_type: str,
        message: str,
        payload: dict[str, Any],
        *,
        status: str | None = None,
    ) -> dict[str, Any]:
        event = self.repository.add_event(run_id, event_type, message, self._payload(payload))
        if status:
            self.repository.update_status(run_id, status)
        return event

    def _payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return redact_sensitive(redact(payload))

    def _correlation_id(self, target_namespace: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        prefix = self.settings.mop_execution_generated_name_prefix or "agent-ai"
        return f"{prefix}-{target_namespace}-execution-{timestamp}"

    @staticmethod
    def _bundle_label(bundle_source: dict[str, Any]) -> str:
        for key in ("bundle_id", "run_id", "folder_name", "path", "reference"):
            value = bundle_source.get(key)
            if value:
                return str(value).replace("/", "_").replace("\\", "_")[:120]
        return "uploaded-bundle"

    def _find_key(self, value: Any, key: str) -> Any:
        if isinstance(value, dict):
            if key in value and value[key] is not None and value[key] != "":
                return value[key]
            for child in value.values():
                found = self._find_key(child, key)
                if found is not None and found != "":
                    return found
        if isinstance(value, list):
            for child in value:
                found = self._find_key(child, key)
                if found is not None and found != "":
                    return found
        return None


class MopExecutionPreflightService:
    """Discovers and validates MoP bundles before any execution-agent call."""

    REQUIRED_FILE_CHECKS = {
        "artifact_json": "artifact.json",
        "machine_execution_plan": "machine_execution_plan.yaml",
        "human_mop_markdown": "*.human-mop.md",
        "mop_pdf": "*.pdf",
        "deployment_artifacts_zip": "deployment-artifacts.zip",
        "artifact_index": "deployment-artifacts/artifact-index.json",
    }
    GENERIC_TARGETS = {"generic", "generic-namespace", "namespace-placeholder", "target-namespace", "<target_namespace>"}
    CLUSTER_SCOPED_KINDS = {
        "ClusterRole",
        "ClusterRoleBinding",
        "CustomResourceDefinition",
        "MutatingWebhookConfiguration",
        "Namespace",
        "PersistentVolume",
        "StorageClass",
        "ValidatingWebhookConfiguration",
    }

    def __init__(
        self,
        *,
        repository: RunRepository,
        artifact_service: ArtifactService,
        settings: Settings,
    ) -> None:
        self.repository = repository
        self.artifact_service = artifact_service
        self.settings = settings

    def bundle_candidates(self, *, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        transactions = self.repository.list_transactions(user_id=user_id, include_hidden=False, limit=limit)
        for transaction in transactions:
            if transaction.get("workflow_type") != "mop_generation" or transaction.get("status") != "completed":
                continue
            artifacts = self.repository.list_artifacts(transaction["run_id"])
            bundle = self._preferred_bundle_artifact(artifacts)
            if not bundle:
                continue
            events = self.repository.list_events(transaction["run_id"])
            metadata = bundle.get("metadata") or {}
            publish_state = self._publish_state(events)
            bytes_result = self._read_bundle_artifact(bundle)
            candidates.append(
                {
                    "source_type": "activity_run",
                    "run_id": transaction["run_id"],
                    "title": transaction.get("title") or transaction.get("goal") or transaction["run_id"],
                    "status": transaction.get("status"),
                    "source_namespace": metadata.get("namespace") or transaction.get("namespace"),
                    "target_namespace_placeholder": metadata.get("target_namespace_placeholder"),
                    "target_environment": metadata.get("target_environment"),
                    "generated_at": metadata.get("bundle_timestamp") or transaction.get("updated_at"),
                    "artifact_id": bundle.get("artifact_id"),
                    "artifact_type": bundle.get("artifact_type"),
                    "filename": self._artifact_filename(bundle),
                    "size_bytes": len(bytes_result[0]) if bytes_result[0] is not None else None,
                    "sha256": self._sha256(bytes_result[0]) if bytes_result[0] is not None else None,
                    "local_available": bytes_result[0] is not None,
                    "local_error": bytes_result[1],
                    "publish_folder": publish_state.get("folder_name"),
                    "publish_branch": publish_state.get("branch"),
                    "bundle_id": metadata.get("bundle_id"),
                }
            )
        return candidates

    def bundle_content_for_activity_run(
        self,
        *,
        user_id: str,
        run_id: str,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        if not run or run.user_id != user_id or run.workflow_type != "mop_generation":
            return {
                "ok": False,
                "error": "Selected Activity run is not available for MoP Execution.",
                "bundle": {"run_id": run_id},
            }
        artifacts = self.repository.list_artifacts(run_id)
        bundle = self._artifact_by_id(artifacts, artifact_id) if artifact_id else self._preferred_bundle_artifact(artifacts)
        if not bundle:
            return {
                "ok": False,
                "error": "Selected MoP Generation run does not have a local mop-bundle.zip artifact.",
                "bundle": {"run_id": run_id},
            }
        content, error = self._read_bundle_artifact(bundle)
        publish_state = self._publish_state(self.repository.list_events(run_id))
        source_metadata = (bundle.get("metadata") or {}) | {"run_id": run_id, "artifact_id": bundle.get("artifact_id")}
        source_metadata["storage_path"] = bundle.get("storage_path")
        source_metadata["filename"] = self._artifact_filename(bundle)
        if publish_state.get("folder_name"):
            source_metadata["folder_name"] = publish_state.get("folder_name")
        if publish_state.get("branch"):
            source_metadata["branch"] = publish_state.get("branch")
        if content is None:
            return {
                "ok": False,
                "error": error or "Local mop-bundle.zip artifact could not be read.",
                "bundle": {"run_id": run_id, "artifact_id": bundle.get("artifact_id"), "filename": self._artifact_filename(bundle)},
            }
        return {
            "ok": True,
            "content": content,
            "filename": self._artifact_filename(bundle),
            "source_metadata": source_metadata,
            "bundle": {
                "run_id": run_id,
                "artifact_id": bundle.get("artifact_id"),
                "filename": self._artifact_filename(bundle),
                "storage_path": bundle.get("storage_path"),
                "folder_name": source_metadata.get("folder_name"),
                "branch": source_metadata.get("branch"),
            },
        }

    def bundle_content_for_artifact_repo_folder(
        self,
        *,
        user_id: str,
        folder_name: str,
    ) -> dict[str, Any]:
        folder = folder_name.strip().strip("/")
        if not folder:
            return {"ok": False, "error": "Artifact repo folder is required for this bundle source.", "bundle": {}}
        for candidate in self.bundle_candidates(user_id=user_id, limit=200):
            if candidate.get("publish_folder") == folder:
                resolved = self.bundle_content_for_activity_run(
                    user_id=user_id,
                    run_id=str(candidate["run_id"]),
                    artifact_id=str(candidate.get("artifact_id") or "") or None,
                )
                if resolved.get("ok"):
                    resolved["source_metadata"] = (resolved.get("source_metadata") or {}) | {"folder_name": folder}
                    resolved["bundle"] = (resolved.get("bundle") or {}) | {"folder_name": folder}
                return resolved
        return {
            "ok": False,
            "error": "Artifact repo folder is not linked to a local MoP bundle. Select an Activity run or upload mop-bundle.zip.",
            "bundle": {"folder_name": folder},
        }
    def preflight_activity_run(
        self,
        *,
        user_id: str,
        run_id: str,
        target_namespace: str,
        artifact_id: str | None = None,
    ) -> dict[str, Any]:
        resolved = self.bundle_content_for_activity_run(user_id=user_id, run_id=run_id, artifact_id=artifact_id)
        if not resolved.get("ok"):
            return self._failure_result(
                source_type="activity_run",
                target_namespace=target_namespace,
                message=str(resolved.get("error") or "Local mop-bundle.zip artifact could not be read."),
                bundle=resolved.get("bundle") or None,
            )
        return self.preflight_bytes(
            content=resolved["content"],
            filename=str(resolved.get("filename") or "mop-bundle.zip"),
            source_type="activity_run",
            target_namespace=target_namespace,
            source_metadata=resolved.get("source_metadata") or {},
        )

    def preflight_artifact_repo_folder(
        self,
        *,
        user_id: str,
        folder_name: str,
        target_namespace: str,
    ) -> dict[str, Any]:
        resolved = self.bundle_content_for_artifact_repo_folder(user_id=user_id, folder_name=folder_name)
        if not resolved.get("ok"):
            return self._failure_result(
                source_type="artifact_repo_folder",
                target_namespace=target_namespace,
                message=str(resolved.get("error") or "Local mop-bundle.zip artifact could not be read."),
                bundle=resolved.get("bundle") or None,
            )
        return self.preflight_bytes(
            content=resolved["content"],
            filename=str(resolved.get("filename") or "mop-bundle.zip"),
            source_type="artifact_repo_folder",
            target_namespace=target_namespace,
            source_metadata=resolved.get("source_metadata") or {},
        )

    def preflight_bytes(
        self,
        *,
        content: bytes,
        filename: str,
        source_type: str,
        target_namespace: str,
        source_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_metadata = source_metadata or {}
        checks: list[dict[str, Any]] = []
        warnings: list[str] = []
        failures: list[str] = []
        bundle = {
            "filename": filename or "mop-bundle.zip",
            "size_bytes": len(content),
            "sha256": self._sha256(content),
            "source_type": source_type,
            **{k: v for k, v in source_metadata.items() if k in {"run_id", "artifact_id", "folder_name"}},
        }
        self._add_check(checks, "local_bundle_present", "Local bundle present", True, f"{bundle['size_bytes']} bytes available.")
        if not zipfile.is_zipfile(BytesIO(content)):
            self._add_check(checks, "zip_valid", "Bundle is a valid zip", False, "Selected bundle is not a valid zip archive.")
            return self._result(source_type, target_namespace, bundle, {}, checks, ["Selected bundle is not a valid zip archive."], warnings)
        self._add_check(checks, "zip_valid", "Bundle is a valid zip", True, "Zip archive opened successfully.")

        with zipfile.ZipFile(BytesIO(content)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            names = [self._norm(info.filename) for info in infos]
            name_set = set(names)
            required = self._required_file_status(name_set)
            for check_id, label in self.REQUIRED_FILE_CHECKS.items():
                ok = bool(required[check_id])
                self._add_check(checks, check_id, f"Required file: {label}", ok, required[check_id] or "missing")
                if not ok:
                    failures.append(f"Missing required file: {label}")

            artifact_json = self._read_json_member(archive, "artifact.json")
            artifact_index = self._read_json_member(archive, "deployment-artifacts/artifact-index.json")
            machine_plan = self._read_text_member(archive, "machine_execution_plan.yaml") or ""
            metadata = self._metadata_summary(artifact_json, artifact_index, source_metadata)
            source_namespace = str(metadata.get("source_namespace") or "").strip()
            target_placeholder = str(metadata.get("target_namespace_placeholder") or "").strip()
            self._namespace_checks(
                checks=checks,
                failures=failures,
                warnings=warnings,
                archive=archive,
                names=names,
                source_namespace=source_namespace,
                target_placeholder=target_placeholder,
                target_namespace=target_namespace,
            )
            self._secret_checks(checks=checks, failures=failures, warnings=warnings, archive=archive, names=names)
            self._cluster_scope_checks(checks=checks, failures=failures, warnings=warnings, archive=archive, names=names, machine_plan=machine_plan)
            metadata["artifact_index_summary"] = self._artifact_index_summary(artifact_index)
            metadata["file_count"] = len(names)
            metadata["files_sample"] = names[:80]
        return self._result(source_type, target_namespace, bundle, metadata, checks, failures, warnings)

    def _required_file_status(self, names: set[str]) -> dict[str, str | None]:
        root_names = {name for name in names if "/" not in name}
        return {
            "artifact_json": "artifact.json" if "artifact.json" in root_names else None,
            "machine_execution_plan": "machine_execution_plan.yaml" if "machine_execution_plan.yaml" in root_names else None,
            "human_mop_markdown": next((name for name in root_names if name.endswith(".human-mop.md") or name == "mop.md"), None),
            "mop_pdf": next((name for name in root_names if name.endswith(".pdf") or name == "mop.pdf"), None),
            "deployment_artifacts_zip": "deployment-artifacts.zip" if "deployment-artifacts.zip" in root_names else None,
            "artifact_index": "deployment-artifacts/artifact-index.json" if "deployment-artifacts/artifact-index.json" in names else ("artifact-index.json" if "artifact-index.json" in root_names else None),
        }

    def _namespace_checks(
        self,
        *,
        checks: list[dict[str, Any]],
        failures: list[str],
        warnings: list[str],
        archive: zipfile.ZipFile,
        names: list[str],
        source_namespace: str,
        target_placeholder: str,
        target_namespace: str,
    ) -> None:
        target = target_namespace.strip()
        if source_namespace and target and source_namespace == target:
            message = "Target namespace matches the source namespace; execution would risk mutating the source namespace."
            failures.append(message)
            self._add_check(checks, "source_target_distinct", "Source and target namespaces are distinct", False, message)
        else:
            self._add_check(checks, "source_target_distinct", "Source and target namespaces are distinct", True, f"source={source_namespace or 'unknown'}, target={target}")

        placeholder = target_placeholder.lower()
        if placeholder and placeholder not in self.GENERIC_TARGETS and target_placeholder != target:
            message = f"Bundle target placeholder '{target_placeholder}' differs from selected target namespace '{target}'."
            warnings.append(message)
            self._add_check(checks, "target_placeholder_match", "Bundle target placeholder matches selection", "warning", message)
        else:
            self._add_check(checks, "target_placeholder_match", "Bundle target placeholder matches selection", True, target_placeholder or "generic target placeholder")

        hardcoded_source_refs = []
        hardcoded_other_refs = []
        for name in names:
            if not self._is_manifest_path(name):
                continue
            text = self._read_text_member(archive, name, max_bytes=750_000) or ""
            for namespace in self._manifest_namespaces(text):
                if source_namespace and namespace == source_namespace and namespace != target:
                    hardcoded_source_refs.append(f"{name}: {namespace}")
                elif namespace not in {target, target_placeholder, ""} and namespace.lower() not in self.GENERIC_TARGETS:
                    hardcoded_other_refs.append(f"{name}: {namespace}")
        if hardcoded_source_refs:
            message = "Deployable manifests still reference the source namespace: " + "; ".join(hardcoded_source_refs[:5])
            failures.append(message)
            self._add_check(checks, "manifest_namespace_rewrite", "Deployable manifests do not target source namespace", False, message)
        elif hardcoded_other_refs:
            message = "Deployable manifests contain namespaces different from the selected target: " + "; ".join(hardcoded_other_refs[:5])
            warnings.append(message)
            self._add_check(checks, "manifest_namespace_rewrite", "Deployable manifests do not target source namespace", "warning", message)
        else:
            self._add_check(checks, "manifest_namespace_rewrite", "Deployable manifests do not target source namespace", True, "No source namespace references found in deployable manifests.")

    def _secret_checks(
        self,
        *,
        checks: list[dict[str, Any]],
        failures: list[str],
        warnings: list[str],
        archive: zipfile.ZipFile,
        names: list[str],
    ) -> None:
        blocking_refs = []
        warning_refs = []
        for name in names:
            lowered = name.lower()
            if not self._is_text_file(name) or ("secret" not in lowered and not self._is_manifest_path(name)):
                continue
            text = self._read_text_member(archive, name, max_bytes=750_000) or ""
            contains_secret = self._contains_secret_manifest(text) or ("secret" in lowered and re.search(r"(?im)^\s*(data|stringData)\s*:", text))
            if not contains_secret:
                continue
            has_material = bool(re.search(r"(?im)^\s*(data|stringData)\s*:", text))
            is_source_manifest = lowered.startswith("deployment-artifacts/kubernetes-manifests/")
            is_generated_or_template = (
                lowered.startswith("deployment-artifacts/rendered-manifests/")
                or "/templates/" in lowered
                or lowered.startswith("deployment-artifacts/helm-chart/")
            )
            if has_material and is_source_manifest and not is_generated_or_template:
                blocking_refs.append(name)
            else:
                warning_refs.append(name)
        if blocking_refs:
            message = "Source Secret material was found in deployable source manifests: " + "; ".join(blocking_refs[:8])
            failures.append(message)
            self._add_check(checks, "no_secret_material", "No source Secret material", False, message)
        elif warning_refs:
            message = "Generated or templated Secret manifests require execution-agent validation: " + "; ".join(warning_refs[:8])
            warnings.append(message)
            self._add_check(checks, "no_secret_material", "No source Secret material", "warning", message)
        else:
            self._add_check(checks, "no_secret_material", "No source Secret material", True, "No Secret manifest/data pattern detected.")

    def _cluster_scope_checks(
        self,
        *,
        checks: list[dict[str, Any]],
        failures: list[str],
        warnings: list[str],
        archive: zipfile.ZipFile,
        names: list[str],
        machine_plan: str,
    ) -> None:
        scoped_refs = []
        for name in names:
            if not self._is_manifest_path(name):
                continue
            text = self._read_text_member(archive, name, max_bytes=750_000) or ""
            for kind in sorted(self.CLUSTER_SCOPED_KINDS):
                if re.search(rf"(?im)^\s*kind\s*:\s*{re.escape(kind)}\s*$", text):
                    scoped_refs.append(f"{name}: {kind}")
        destructive = re.findall(
            r"(?im)\b(?:kubectl\s+delete|helm\s+uninstall|helm\s+delete|kubectl\s+replace\s+--force)\b[^\n]*",
            machine_plan,
        )
        cluster_destructive = [line.strip() for line in destructive if re.search(r"clusterrole|namespace|crd|customresourcedefinition|storageclass|persistentvolume|webhook", line, re.IGNORECASE)]
        unconditional_destructive = [line for line in cluster_destructive if not self._is_approval_gated_cleanup_command(line)]
        approval_gated_destructive = [line for line in cluster_destructive if self._is_approval_gated_cleanup_command(line)]
        if unconditional_destructive:
            message = "Cluster-scoped destructive command detected: " + "; ".join(unconditional_destructive[:5])
            failures.append(message)
            self._add_check(checks, "no_cluster_scoped_destructive_actions", "No cluster-scoped destructive actions", False, message)
        elif approval_gated_destructive:
            message = "Approval-gated cluster-scoped cleanup command requires operator review: " + "; ".join(approval_gated_destructive[:5])
            warnings.append(message)
            self._add_check(checks, "no_cluster_scoped_destructive_actions", "No cluster-scoped destructive actions", "warning", message)
        elif scoped_refs:
            message = "Cluster-scoped resources require operator review: " + "; ".join(scoped_refs[:8])
            warnings.append(message)
            self._add_check(checks, "no_cluster_scoped_destructive_actions", "No cluster-scoped destructive actions", "warning", message)
        else:
            self._add_check(checks, "no_cluster_scoped_destructive_actions", "No cluster-scoped destructive actions", True, "No cluster-scoped destructive action detected.")

    def _result(
        self,
        source_type: str,
        target_namespace: str,
        bundle: dict[str, Any],
        metadata: dict[str, Any],
        checks: list[dict[str, Any]],
        failures: list[str],
        warnings: list[str],
    ) -> dict[str, Any]:
        valid = not failures and all(check["status"] in {"passed", "warning"} for check in checks)
        return {
            "valid": valid,
            "status": "passed" if valid else "failed",
            "source_type": source_type,
            "target_namespace": target_namespace,
            "correlation_id": self._correlation_id(target_namespace),
            "summary": "Preflight passed. Bundle is ready for execution-agent validation." if valid else "Preflight failed. Resolve blocking items before execution.",
            "bundle": bundle,
            "metadata": metadata,
            "checks": checks,
            "failures": failures,
            "warnings": warnings,
        }

    def _failure_result(
        self,
        *,
        source_type: str,
        target_namespace: str,
        message: str,
        bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        checks = []
        self._add_check(checks, "local_bundle_present", "Local bundle present", False, message)
        return self._result(source_type, target_namespace, bundle or {}, {}, checks, [message], [])

    def _metadata_summary(
        self,
        artifact_json: dict[str, Any] | None,
        artifact_index: dict[str, Any] | None,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        artifact_json = artifact_json or {}
        return {
            "bundle_id": artifact_json.get("bundle_id") or source_metadata.get("bundle_id"),
            "source_namespace": artifact_json.get("source_namespace") or artifact_json.get("namespace") or source_metadata.get("namespace"),
            "target_namespace_placeholder": artifact_json.get("target_namespace_placeholder") or artifact_json.get("target_namespace") or source_metadata.get("target_namespace_placeholder"),
            "generated_release_name": artifact_json.get("generated_release_name"),
            "generated_at": artifact_json.get("generated_at") or artifact_json.get("bundle_timestamp") or source_metadata.get("bundle_timestamp"),
            "target_environment": artifact_json.get("target_environment") or source_metadata.get("target_environment"),
            "warnings": artifact_json.get("warnings") or [],
            "artifact_index_available": bool(artifact_index),
        }

    def _artifact_index_summary(self, artifact_index: dict[str, Any] | None) -> dict[str, Any]:
        artifact_index = artifact_index or {}
        return {
            "values_count": len(artifact_index.get("values") or []),
            "kubernetes_manifest_count": len(artifact_index.get("kubernetes_manifests") or []),
            "rendered_manifest_count": len(artifact_index.get("rendered_manifests") or []),
            "crd_count": len(artifact_index.get("crds") or []),
            "warnings_count": len(artifact_index.get("warnings") or []),
        }

    def _preferred_bundle_artifact(self, artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [artifact for artifact in artifacts if self._is_bundle_artifact(artifact)]
        return candidates[-1] if candidates else None

    def _artifact_by_id(self, artifacts: list[dict[str, Any]], artifact_id: str | None) -> dict[str, Any] | None:
        if not artifact_id:
            return None
        return next((artifact for artifact in artifacts if artifact.get("artifact_id") == artifact_id and self._is_bundle_artifact(artifact)), None)

    @staticmethod
    def _is_bundle_artifact(artifact: dict[str, Any]) -> bool:
        metadata = artifact.get("metadata") or {}
        filename = str(metadata.get("filename") or artifact.get("title") or "").lower()
        return artifact.get("artifact_type") == "mop_bundle_zip" or filename == "mop-bundle.zip" or str(artifact.get("mime_type") or "").lower() in {"application/zip", "application/x-zip-compressed"}

    @staticmethod
    def _artifact_filename(artifact: dict[str, Any]) -> str:
        metadata = artifact.get("metadata") or {}
        return str(metadata.get("filename") or artifact.get("title") or "mop-bundle.zip")

    def _read_bundle_artifact(self, artifact: dict[str, Any]) -> tuple[bytes | None, str | None]:
        try:
            return self.artifact_service.read_artifact_bytes(str(artifact.get("storage_path") or "")), None
        except (OSError, ValueError) as exc:
            return None, f"Local artifact could not be read: {exc}"

    @staticmethod
    def _publish_state(events: list[dict[str, Any]]) -> dict[str, Any]:
        state: dict[str, Any] = {"published": False}
        for event in events:
            if event.get("event_type") != "artifact_publish_completed":
                continue
            payload = event.get("payload") or {}
            publish = MopExecutionPreflightService._find_key(payload, "artifact_publish")
            if isinstance(publish, dict):
                state.update(publish)
                state["published"] = True
            else:
                folder_name = MopExecutionPreflightService._find_key(payload, "folder_name")
                if folder_name:
                    state.update({"published": True, "folder_name": folder_name})
        return state

    @staticmethod
    def _find_key(value: Any, key: str) -> Any:
        if isinstance(value, dict):
            if key in value and value[key] is not None and value[key] != "":
                return value[key]
            for child in value.values():
                found = MopExecutionPreflightService._find_key(child, key)
                if found is not None and found != "":
                    return found
        if isinstance(value, list):
            for child in value:
                found = MopExecutionPreflightService._find_key(child, key)
                if found is not None and found != "":
                    return found
        return None

    @staticmethod
    def _sha256(content: bytes | None) -> str | None:
        return hashlib.sha256(content).hexdigest() if content is not None else None

    @staticmethod
    def _norm(name: str) -> str:
        return name.replace("\\", "/").lstrip("./")

    def _read_json_member(self, archive: zipfile.ZipFile, name: str) -> dict[str, Any] | None:
        text = self._read_text_member(archive, name, max_bytes=1_500_000)
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _read_text_member(self, archive: zipfile.ZipFile, name: str, *, max_bytes: int = 2_000_000) -> str | None:
        normalized = self._norm(name)
        match = next((info for info in archive.infolist() if self._norm(info.filename) == normalized and not info.is_dir()), None)
        if not match or match.file_size > max_bytes:
            return None
        try:
            return archive.read(match).decode("utf-8", errors="replace")
        except (OSError, RuntimeError, zipfile.BadZipFile):
            return None

    @staticmethod
    def _is_text_file(name: str) -> bool:
        return name.lower().endswith((".yaml", ".yml", ".json", ".md", ".txt", ".conf", ".ini", ".env", ".properties"))

    @staticmethod
    def _is_manifest_path(name: str) -> bool:
        lowered = name.lower()
        return lowered.endswith((".yaml", ".yml", ".json")) and (
            lowered.startswith("deployment-artifacts/kubernetes-manifests/")
            or lowered.startswith("deployment-artifacts/rendered-manifests/")
            or lowered.startswith("deployment-artifacts/crds/")
        )

    @staticmethod
    def _contains_secret_manifest(text: str) -> bool:
        return bool(re.search(r"(?im)^\s*kind\s*:\s*Secret\s*$", text) or re.search(r'"kind"\s*:\s*"Secret"', text))

    @staticmethod
    def _is_approval_gated_cleanup_command(line: str) -> bool:
        return bool(re.search(r"\b(approval|approved|only if|cleanup|rollback|revert|after review|human)\b", line, re.IGNORECASE))

    @staticmethod
    def _manifest_namespaces(text: str) -> list[str]:
        pattern = r"(?im)^[^\S\r\n]*namespace[^\S\r\n]*:[^\S\r\n]*['\"]?([^\s'\"#]+)"
        return [match.group(1).strip().strip('"\'') for match in re.finditer(pattern, text)]

    @staticmethod
    def _add_check(checks: list[dict[str, Any]], check_id: str, label: str, ok: bool | str, detail: str) -> None:
        status = "warning" if ok == "warning" else "passed" if ok else "failed"
        checks.append({"id": check_id, "label": label, "status": status, "detail": detail})

    def _correlation_id(self, target_namespace: str) -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        prefix = self.settings.mop_execution_generated_name_prefix or "agent-ai"
        return f"{prefix}-{target_namespace}-execution-{timestamp}"