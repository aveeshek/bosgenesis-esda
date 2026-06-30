from __future__ import annotations

from io import BytesIO
import hashlib
import json
import re
from urllib.error import URLError
from urllib.request import Request, urlopen
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

from backend.app.config import Settings
from backend.app.db.database import RunRepository


TERMINAL_STATUSES = {"completed", "failed", "stopped"}

STAGE_DEFINITIONS = [
    {"id": "intake", "label": "Intake"},
    {"id": "classify", "label": "Classify"},
    {"id": "plan", "label": "Plan"},
    {"id": "evidence", "label": "Evidence"},
    {"id": "clone", "label": "Clone"},
    {"id": "security", "label": "Security"},
    {"id": "quality", "label": "Quality"},
    {"id": "cleanup", "label": "Cleanup"},
    {"id": "draft", "label": "Draft"},
    {"id": "validate", "label": "Validate"},
    {"id": "recover", "label": "Recover"},
    {"id": "artifacts", "label": "Bundle"},
    {"id": "publish", "label": "Export"},
    {"id": "complete", "label": "Complete"},
]

MOP_STAGE_DEFINITIONS = [
    {"id": "intake", "label": "Intake"},
    {"id": "classify", "label": "Classify"},
    {"id": "plan", "label": "Plan"},
    {"id": "scope", "label": "Scope"},
    {"id": "k8s", "label": "K8s"},
    {"id": "helm", "label": "Helm"},
    {"id": "mop_agent", "label": "MoP Agent"},
    {"id": "draft", "label": "Draft"},
    {"id": "validate", "label": "Validate"},
    {"id": "recover", "label": "Recover"},
    {"id": "artifacts", "label": "Bundle"},
    {"id": "publish", "label": "Export"},
    {"id": "complete", "label": "Complete"},
]


class ActivityService:
    def __init__(
        self,
        repository: RunRepository,
        *,
        settings: Settings | None = None,
        artifact_storage_root: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.artifact_storage_root = Path(artifact_storage_root) if artifact_storage_root else None

    def list_activity_nodes(
        self,
        *,
        user_id: str,
        workflow_type: str | None = None,
        include_hidden: bool = False,
        status: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        published: bool | None = None,
        time_range: str = "30d",
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        workflow_types = self._workflow_filter(workflow_type)
        snapshots = self.repository.list_activity_snapshots(
            user_id=user_id,
            workflow_types=workflow_types,
            include_hidden=include_hidden,
            limit=max(min(limit * 3, 500), 100),
        )
        nodes: list[dict] = []
        for snapshot in snapshots:
            transaction = snapshot["transaction"]
            events = snapshot["events"]
            artifacts = snapshot["artifacts"]
            node = self._build_node(transaction, events, artifacts)
            if not self._matches_filters(
                node,
                status=status,
                repo=repo,
                model=model,
                published=published,
                time_range=time_range,
                date_from=date_from,
                date_to=date_to,
            ):
                continue
            nodes.append(node)
        nodes.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return nodes[:limit]

    def list_release_note_nodes(
        self,
        *,
        user_id: str,
        include_hidden: bool = False,
        status: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        published: bool | None = None,
        time_range: str = "30d",
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return self.list_activity_nodes(
            user_id=user_id,
            workflow_type="release_note_creation",
            include_hidden=include_hidden,
            status=status,
            repo=repo,
            model=model,
            published=published,
            time_range=time_range,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )

    def get_activity_detail(self, run_id: str) -> dict | None:
        run = self.repository.get_run(run_id)
        if not run or run.workflow_type not in self._activity_workflow_types():
            return None
        events = self.repository.list_events(run_id)
        artifacts = self.repository.list_artifacts(run_id)
        transaction = {
            "run_id": run.run_id,
            "workflow_type": run.workflow_type,
            "title": self.repository._generate_session_name(run),
            "session_name": self.repository._generate_session_name(run),
            "goal": run.goal,
            "status": run.status,
            "target_url": run.target_url,
            "namespace": run.namespace,
            "artifact_count": len(artifacts),
            "last_event_sequence": len(events),
            "hidden_at": None,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }
        node = self._build_node(transaction, events, artifacts)
        return {
            "node": node,
            "stages": self._build_stages(events, run.workflow_type),
            "events": [self._safe_event(event) for event in events],
            "artifacts": [self._artifact_summary(artifact) for artifact in artifacts],
            "artifact_actions": self.artifact_actions(run_id),
        }

    def get_release_note_detail(self, run_id: str) -> dict | None:
        detail = self.get_activity_detail(run_id)
        if not detail or detail.get("node", {}).get("workflow_type") != "release_note_creation":
            return None
        return detail

    def artifact_actions(self, run_id: str) -> dict:
        run = self.repository.get_run(run_id)
        if not run or run.workflow_type not in self._activity_workflow_types():
            return self._empty_artifact_actions(run_id)
        events = self.repository.list_events(run_id)
        artifacts = self.repository.list_artifacts(run_id)
        publish_state = self._publish_state(events)
        repo_folder_url = self._published_folder_url(publish_state)
        repo_path = self._published_repo_path(publish_state)
        actions = self._base_repo_actions(repo_folder_url=repo_folder_url, repo_path=repo_path)
        if run.workflow_type == "mop_generation":
            bundle = self._preferred_artifact(artifacts, "bundle")
            actions = {
                "bundle": self._artifact_action(
                    run_id=run_id,
                    workflow_type=run.workflow_type,
                    kind="bundle",
                    label="Download MoP Bundle",
                    filename=self.artifact_filename(run.workflow_type, "bundle"),
                    artifact=bundle,
                    publish_state=publish_state,
                ),
                **actions,
            }
        else:
            markdown = self._preferred_artifact(artifacts, "markdown")
            pdf = self._preferred_artifact(artifacts, "pdf")
            actions = {
                "markdown": self._artifact_action(
                    run_id=run_id,
                    workflow_type=run.workflow_type,
                    kind="markdown",
                    label="Download Markdown",
                    filename=self.artifact_filename(run.workflow_type, "markdown"),
                    artifact=markdown,
                    publish_state=publish_state,
                ),
                "pdf": self._artifact_action(
                    run_id=run_id,
                    workflow_type=run.workflow_type,
                    kind="pdf",
                    label="Download PDF",
                    filename=self.artifact_filename(run.workflow_type, "pdf"),
                    artifact=pdf,
                    publish_state=publish_state,
                ),
                **actions,
            }
        return {
            "run_id": run_id,
            "workflow_type": run.workflow_type,
            "publish_state": publish_state,
            "repo_folder_url": repo_folder_url,
            "repo_path": repo_path,
            "actions": actions,
            "local_artifacts": [self._artifact_summary(artifact) for artifact in artifacts],
        }

    def release_note_artifact_actions(self, run_id: str) -> dict:
        actions = self.artifact_actions(run_id)
        if actions.get("workflow_type") != "release_note_creation":
            return self._empty_artifact_actions(run_id)
        return actions

    def resolve_artifact_download(self, run_id: str, kind: str) -> dict | None:
        normalized_kind = kind.lower()
        run = self.repository.get_run(run_id)
        if not run or run.workflow_type not in self._activity_workflow_types():
            return None
        if run.workflow_type == "mop_generation":
            if normalized_kind != "bundle":
                return None
        elif normalized_kind not in {"markdown", "pdf"}:
            return None
        events = self.repository.list_events(run_id)
        artifacts = self.repository.list_artifacts(run_id)
        publish_state = self._publish_state(events)
        filename = self.artifact_filename(run.workflow_type, normalized_kind)
        published_url = self._published_raw_url(publish_state, filename)
        if published_url:
            return {"mode": "redirect", "url": published_url, "filename": filename, "source": "published"}
        artifact = self._preferred_artifact(artifacts, normalized_kind)
        if artifact:
            return {"mode": "local", "artifact": artifact, "filename": filename, "source": "local"}
        return None

    def build_chat_context(self, run_ids: list[str]) -> list[dict]:
        contexts = []
        seen: set[str] = set()
        for run_id in run_ids[:8]:
            if run_id in seen:
                continue
            seen.add(run_id)
            detail = self.get_activity_detail(run_id)
            if not detail:
                continue
            node = detail["node"]
            artifacts = self.repository.list_artifacts(run_id)
            contexts.append(
                {
                    "run_id": run_id,
                    "workflow_type": node.get("workflow_type"),
                    "workflow_label": node.get("workflow_label"),
                    "title": node.get("title"),
                    "repository": node.get("repository"),
                    "resource_label": node.get("resource_label"),
                    "github_url": node.get("github_url"),
                    "release_name": node.get("release_name"),
                    "namespace": node.get("namespace"),
                    "target_environment": node.get("target_environment"),
                    "status": node.get("status"),
                    "visual_status": node.get("visual_status"),
                    "created_at": node.get("created_at"),
                    "updated_at": node.get("updated_at"),
                    "duration_label": node.get("duration_label"),
                    "model_profile": node.get("model_profile"),
                    "publish_state": node.get("publish_state"),
                    "artifact_summary": node.get("artifact_summary"),
                    "artifact_actions": detail.get("artifact_actions", {}),
                    "stages": [
                        {
                            "id": stage.get("id"),
                            "label": stage.get("label"),
                            "status": stage.get("status"),
                            "summary": stage.get("summary"),
                            "event_type": stage.get("event_type"),
                            "completed_at": stage.get("completed_at"),
                        }
                        for stage in detail.get("stages", [])
                    ],
                    "events": [
                        {
                            "sequence": event.get("sequence"),
                            "event_type": event.get("event_type"),
                            "message": event.get("message"),
                            "created_at": event.get("created_at"),
                        }
                        for event in detail.get("events", [])[-32:]
                    ],
                    "artifact_texts": self._artifact_texts_for_chat(artifacts),
                    "mop_bundle_context": self._mop_bundle_context_for_chat(detail, artifacts),
                }
            )
        return contexts

    def fallback_chat_answer(self, *, question: str, context: list[dict]) -> dict:
        citations: list[dict] = []
        if not context:
            return {
                "answer": "Select at least one activity node before asking about artifacts.",
                "citations": [],
                "safe_summary": "No selected activity context was available.",
            }
        if self.is_direct_activity_answer(question=question, context=context):
            return self._configmap_bundle_answer(context=context)

        lowered_question = question.lower()
        answer_lines = ["Here is what the selected activity shows:"]
        for run_context in context:
            run_id = run_context["run_id"]
            repo = run_context.get("repository") or "unknown repository"
            status = run_context.get("visual_status") or run_context.get("status") or "unknown"
            publish = run_context.get("publish_state") or {}
            artifact_summary = run_context.get("artifact_summary") or {}
            failed_stages = [
                stage for stage in run_context.get("stages", []) if stage.get("status") in {"failed", "recovered"}
            ]
            citations.append({"type": "run", "run_id": run_id, "label": repo})
            answer_lines.append(
                f"- `{repo}` used `{run_context.get('model_profile', {}).get('label', 'unknown model')}` "
                f"and is currently `{status}` for `{run_context.get('release_name') or run_context.get('resource_label') or 'current'}` "
                f"(run_id `{run_id}`)."
            )
            if publish.get("published"):
                answer_lines.append(f"  Artifacts were published to `{publish.get('folder_name')}`.")
                if publish.get("folder_name"):
                    citations.append({"type": "published_folder", "run_id": run_id, "folder": publish["folder_name"]})
            else:
                answer_lines.append("  Published artifact metadata is not available for this run.")
            answer_lines.append(
                "  Artifact availability: "
                f"Markdown={'yes' if artifact_summary.get('has_markdown') else 'no'}, "
                f"PDF={'yes' if artifact_summary.get('has_pdf') else 'no'}, "
                f"MoP bundle={'yes' if artifact_summary.get('has_bundle') else 'no'}."
            )
            if failed_stages:
                answer_lines.append(
                    "  Stages needing attention: "
                    + "; ".join(f"{stage['label']} - {stage.get('summary')}" for stage in failed_stages[:4])
                )
            if any(word in lowered_question for word in {"security", "vulnerability", "quality", "pylint", "scan"}):
                for stage_id in ("security", "quality"):
                    stage = next((item for item in run_context.get("stages", []) if item.get("id") == stage_id), None)
                    if stage:
                        answer_lines.append(f"  {stage['label']}: {stage.get('summary') or stage.get('status')}.")
                        citations.append({"type": "stage", "run_id": run_id, "stage": stage["label"]})
            for artifact_text in run_context.get("artifact_texts", [])[:2]:
                citations.append(
                    {
                        "type": "artifact",
                        "run_id": run_id,
                        "artifact_id": artifact_text.get("artifact_id"),
                        "label": artifact_text.get("title") or artifact_text.get("kind"),
                    }
                )
                if any(word in lowered_question for word in {"markdown", "summary", "document", "md", "pdf"}):
                    section_names = ", ".join(section["heading"] for section in artifact_text.get("sections", [])[:6])
                    if section_names:
                        answer_lines.append(f"  Markdown sections available for review: {section_names}.")
        return {
            "answer": "\n".join(answer_lines),
            "citations": citations[:12],
            "safe_summary": f"Answered from {len(context)} selected activity node(s).",
        }

    def is_direct_activity_answer(self, *, question: str, context: list[dict]) -> bool:
        return self._is_configmap_question(question) and any(item.get("mop_bundle_context") for item in context)

    @staticmethod
    def _is_configmap_question(question: str) -> bool:
        lowered = question.lower()
        return any(token in lowered for token in ("configmap", "config map", "configmaps", "config maps"))

    def _configmap_bundle_answer(self, *, context: list[dict]) -> dict:
        lines = ["### ConfigMaps in Selected MoP Bundle", ""]
        citations: list[dict] = []
        total_entries = 0
        for run_context in context:
            run_id = run_context.get("run_id")
            repo = run_context.get("repository") or run_context.get("resource_label") or "selected run"
            bundle_context = run_context.get("mop_bundle_context") or {}
            filename = bundle_context.get("filename") or "mop-bundle.zip"
            citations.append({"type": "run", "run_id": run_id, "label": repo})
            if bundle_context.get("status") != "available":
                lines.extend(
                    [
                        f"**Run:** `{repo}` (`{run_id}`)",
                        f"**Bundle:** `{filename}`",
                        "",
                        f"I could not inspect the bundle contents: {bundle_context.get('reason') or 'bundle context unavailable'}.",
                        "",
                    ]
                )
                continue
            configmaps = bundle_context.get("configmaps") or []
            total_entries += len(configmaps)
            counts = self._configmap_type_counts(configmaps)
            citations.append({"type": "mop_bundle", "run_id": run_id, "label": filename})
            lines.extend(
                [
                    f"**Run:** `{repo}` (`{run_id}`)",
                    f"**Bundle:** `{filename}` from `{bundle_context.get('source') or 'artifact storage'}`",
                    f"**Bundle scan:** {bundle_context.get('file_count', 0)} files, {bundle_context.get('text_file_count', 0)} readable text/YAML files inspected.",
                    "",
                ]
            )
            if not configmaps:
                lines.extend(
                    [
                        "No Kubernetes `ConfigMap` definition was found in the readable YAML/JSON files inside the bundle.",
                        "",
                    ]
                )
                continue
            lines.extend(
                [
                    f"Found **{len(configmaps)} ConfigMap definition/reference item(s)**.",
                    "",
                    "| Category | Count |",
                    "|---|---:|",
                ]
            )
            for label, count in counts.items():
                if count:
                    lines.append(f"| {label} | {count} |")
            lines.extend(
                [
                    "",
                    "| # | Name | Namespace | Category | Source file |",
                    "|---:|---|---|---|---|",
                ]
            )
            for index, item in enumerate(self._sorted_configmaps(configmaps)[:25], start=1):
                lines.append(
                    f"| {index} | `{self._table_value(item.get('name'))}` | `{self._table_value(item.get('namespace'))}` | "
                    f"{self._table_value(item.get('source_label'))} | `{self._table_value(item.get('file'))}` |"
                )
            if len(configmaps) > 25:
                lines.append(f"\nShowing 25 of {len(configmaps)} items. Download the bundle for the complete inventory.")
            lines.append("")
            if counts.get("Helm template"):
                lines.append("Note: Helm template entries are source templates, not necessarily final rendered Kubernetes objects.")
                lines.append("")
        return {
            "answer": "\n".join(lines).strip(),
            "citations": citations[:12],
            "safe_summary": f"Answered directly from MoP bundle inventory for {len(context)} selected activity node(s); {total_entries} ConfigMap item(s) found.",
            "used_direct_answer": True,
        }

    @staticmethod
    def _configmap_type_counts(configmaps: list[dict]) -> dict:
        counts = {"Raw/rendered manifest": 0, "Helm template": 0, "Bundle metadata": 0, "Documentation/reference": 0, "Other": 0}
        for item in configmaps:
            label = item.get("source_label") or "Other"
            counts[label if label in counts else "Other"] += 1
        return counts

    @staticmethod
    def _sorted_configmaps(configmaps: list[dict]) -> list[dict]:
        priority = {"raw_manifest": 0, "rendered_manifest": 0, "helm_template": 1, "bundle_metadata": 2, "documentation": 3, "reference": 4}
        return sorted(configmaps, key=lambda item: (priority.get(str(item.get("source_type") or ""), 9), str(item.get("file") or ""), str(item.get("name") or "")))

    @staticmethod
    def _table_value(value) -> str:
        text = str(value or "not specified").replace("|", "\\|").replace("\n", " ")
        return text[:180]

    def chat_prompt_payload(self, *, question: str, context: list[dict]) -> dict:
        return {
            "question": question,
            "selected_activity_runs": context,
            "response_contract": {
                "answer": "Concise user-facing answer grounded only in selected context.",
                "citations": "List of run IDs, artifact IDs, published folders, or section names used.",
                "safe_summary": "One safe audit summary. No hidden chain-of-thought.",
            },
        }

    def chat_fallback_hash(self, *, question: str, context: list[dict]) -> str:
        seed = json.dumps({"question": question, "context": context}, default=str, sort_keys=True)
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def _activity_workflow_types(self) -> tuple[str, ...]:
        return ("release_note_creation", "mop_generation")

    def _workflow_filter(self, workflow_type: str | None) -> tuple[str, ...]:
        allowed = self._activity_workflow_types()
        if not workflow_type or workflow_type == "all":
            return allowed
        requested = tuple(item.strip() for item in workflow_type.split(",") if item.strip())
        return tuple(item for item in requested if item in allowed) or allowed

    def workflow_label(self, workflow_type: str | None) -> str:
        return {
            "release_note_creation": "Release Notes",
            "mop_generation": "MoP Generation",
        }.get(str(workflow_type or ""), "Activity")

    def artifact_filename(self, workflow_type: str | None, kind: str) -> str:
        if workflow_type == "mop_generation":
            if kind == "bundle":
                return "mop-bundle.zip"
            return "mop.pdf" if kind == "pdf" else "mop.md"
        return "release-notes.pdf" if kind == "pdf" else "release-notes.md"

    def _build_node(self, transaction: dict, events: list[dict], artifacts: list[dict]) -> dict:
        workflow_type = transaction.get("workflow_type")
        target_url = transaction.get("target_url") or self._first_payload_value(events, "github_url")
        resource_label = self._resource_label(workflow_type, target_url, transaction.get("namespace"), events)
        release_name = self._release_name(events, artifacts, workflow_type, transaction.get("namespace"))
        model_profile = self._model_profile(events)
        publish = self._publish_state(events)
        artifact_summary = self._artifact_availability(artifacts)
        created_at = transaction.get("created_at")
        updated_at = transaction.get("updated_at")
        duration_ms = self._duration_ms(events, created_at, updated_at)
        status = str(transaction.get("status") or "unknown")
        visual_status = "published" if publish["published"] else status
        stage_counts = self._stage_counts(events, workflow_type)
        last_event = events[-1] if events else None
        return {
            "run_id": transaction["run_id"],
            "title": transaction.get("session_name") or transaction.get("title") or transaction["run_id"],
            "workflow_type": workflow_type,
            "workflow_label": self.workflow_label(workflow_type),
            "workflow_badge": "MOP" if workflow_type == "mop_generation" else "RN",
            "repository": resource_label,
            "resource_label": resource_label,
            "github_url": target_url,
            "target_url": target_url,
            "target_environment": self._target_environment(target_url),
            "namespace": transaction.get("namespace"),
            "release_name": release_name,
            "status": status,
            "visual_status": visual_status,
            "publish_state": publish,
            "created_at": created_at,
            "updated_at": updated_at,
            "duration_ms": duration_ms,
            "duration_label": self._duration_label(duration_ms),
            "model_profile": model_profile,
            "artifact_summary": artifact_summary,
            "artifact_count": len(artifacts),
            "stage_counts": stage_counts,
            "last_event_sequence": transaction.get("last_event_sequence") or len(events),
            "last_event_message": last_event.get("message") if last_event else "No events yet",
            "hidden_at": transaction.get("hidden_at"),
        }

    def _build_stages(self, events: list[dict], workflow_type: str | None = None) -> list[dict]:
        definitions = MOP_STAGE_DEFINITIONS if workflow_type == "mop_generation" else STAGE_DEFINITIONS
        stages = {
            definition["id"]: {
                "id": definition["id"],
                "label": definition["label"],
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "summary": definition["label"],
                "event_type": None,
                "sequence": None,
                "payload": {},
            }
            for definition in definitions
        }
        for event in events:
            for stage_id, status in self._stage_updates(event, workflow_type):
                if stage_id not in stages:
                    continue
                stage = stages[stage_id]
                if stage["started_at"] is None:
                    stage["started_at"] = event.get("created_at")
                if status in {"success", "failed", "recovered"}:
                    stage["completed_at"] = event.get("created_at")
                stage["status"] = status
                stage["summary"] = self._event_summary(event)
                stage["event_type"] = event.get("event_type")
                stage["sequence"] = event.get("sequence")
                stage["payload"] = self._safe_payload(event.get("payload") or {})
        return [stages[definition["id"]] for definition in definitions]

    def _stage_updates(self, event: dict, workflow_type: str | None = None) -> list[tuple[str, str]]:
        if workflow_type == "mop_generation":
            return self._mop_stage_updates(event)
        return self._release_note_stage_updates(event)

    def _mop_stage_updates(self, event: dict) -> list[tuple[str, str]]:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == "run_started":
            return [("intake", "success")]
        if event_type == "workflow_classified":
            return [("classify", "success")]
        if event_type == "planning_started":
            return [("plan", "running")]
        if event_type == "plan_created":
            return [("plan", "success")]
        if event_type == "namespace_validated":
            return [("scope", "success" if payload.get("valid") else "failed")]
        if event_type == "tool_call_started":
            tool_name = payload.get("tool_name")
            if tool_name == "mop.k8s_inspector":
                return [("k8s", "running")]
            if tool_name == "mop.helm_manager":
                return [("helm", "running")]
            if tool_name == "mop.creation_agent":
                return [("mop_agent", "running")]
        if event_type == "k8s_evidence_completed":
            return [("k8s", self._tool_result_status(payload.get("result") or {}))]
        if event_type == "helm_evidence_completed":
            return [("helm", self._tool_result_status(payload.get("result") or {}))]
        if event_type == "mop_agent_completed":
            return [("mop_agent", self._tool_result_status(payload.get("result") or {}))]
        if event_type == "draft_started":
            return [("draft", "running")]
        if event_type == "draft_completed":
            return [("draft", "success")]
        if event_type == "validation_completed":
            return [("validate", "success" if payload.get("valid") is not False else "failed")]
        if event_type == "recovery_recommendation":
            return [("recover", "recovered" if payload.get("action") == "escalate" else "success")]
        if event_type == "artifact_created":
            return [("artifacts", "success")]
        if event_type == "artifact_publish_started":
            return [("publish", "running")]
        if event_type == "artifact_publish_completed":
            return [("publish", "success")]
        if event_type == "artifact_publish_failed":
            return [("publish", "failed")]
        if event_type == "run_completed":
            updates = [("complete", "success")]
            if (payload.get("artifact_publish") or {}).get("status") == "success":
                updates.append(("publish", "success"))
            return updates
        if event_type == "run_failed":
            return [("complete", "failed")]
        return []

    def _release_note_stage_updates(self, event: dict) -> list[tuple[str, str]]:
        event_type = event.get("event_type")
        payload = event.get("payload") or {}
        if event_type == "run_started":
            return [("intake", "success")]
        if event_type == "workflow_classified":
            return [("classify", "success")]
        if event_type == "planning_started":
            return [("plan", "running")]
        if event_type == "plan_created":
            return [("plan", "success")]
        if event_type == "tool_call_started":
            return [("evidence", "running")]
        if event_type == "tool_call_completed":
            result = payload.get("result") or payload.get("response") or {}
            return [("evidence", "success" if result.get("status") == "success" else "recovered")]
        if event_type == "repo_clone_started":
            return [("clone", "running")]
        if event_type == "repo_clone_completed":
            clone = payload.get("clone") or {}
            return [("clone", "success" if clone.get("status") == "success" else "recovered")]
        if event_type == "vulnerability_scan_completed":
            return [("security", "success" if payload.get("status") != "failed" else "recovered")]
        if event_type == "quality_scan_completed":
            quality = payload.get("quality") or {}
            return [("quality", "success" if quality.get("status") == "completed" else "recovered")]
        if event_type == "repo_cleanup_completed":
            cleanup = payload.get("cleanup") or {}
            return [("cleanup", "recovered" if cleanup.get("removed") is False else "success")]
        if event_type == "draft_started":
            return [("draft", "running")]
        if event_type == "validation_completed":
            return [
                ("draft", "success"),
                ("validate", "success" if payload.get("valid") is not False else "failed"),
            ]
        if event_type == "recovery_recommendation":
            return [("recover", "recovered" if payload.get("action") == "escalate" else "success")]
        if event_type == "artifact_created":
            return [("artifacts", "success")]
        if event_type == "artifact_publish_started":
            return [("publish", "running")]
        if event_type == "artifact_publish_completed":
            return [("publish", "success")]
        if event_type == "artifact_publish_failed":
            return [("publish", "failed")]
        if event_type == "run_completed":
            updates = [("complete", "success")]
            if (payload.get("artifact_publish") or {}).get("status") == "success":
                updates.append(("publish", "success"))
            return updates
        if event_type == "run_failed":
            return [("complete", "failed")]
        return []

    def _tool_result_status(self, result: dict) -> str:
        status = str(result.get("status") or "success")
        return "success" if status == "success" else "recovered"

    def _matches_filters(
        self,
        node: dict,
        *,
        status: str | None,
        repo: str | None,
        model: str | None,
        published: bool | None,
        time_range: str,
        date_from: str | None,
        date_to: str | None,
    ) -> bool:
        if status and status != "all" and node.get("visual_status") != status and node.get("status") != status:
            return False
        if repo and repo.lower() not in str(node.get("repository") or "").lower():
            return False
        if model and not self._model_matches(node.get("model_profile") or {}, model):
            return False
        if published is not None and bool(node.get("publish_state", {}).get("published")) is not published:
            return False
        created_at = self._parse_datetime(node.get("created_at"))
        if created_at:
            start, end = self._time_window(time_range, date_from, date_to)
            if start and created_at < start:
                return False
            if end and created_at > end:
                return False
        return True

    def _model_matches(self, profile: dict, value: str) -> bool:
        needle = value.lower()
        candidates = [
            profile.get("profile_id"),
            profile.get("label"),
            profile.get("short_label"),
            profile.get("provider"),
            profile.get("deployment"),
        ]
        return any(needle in str(candidate or "").lower() for candidate in candidates)

    def _stage_counts(self, events: list[dict], workflow_type: str | None = None) -> dict:
        counts = {"success": 0, "running": 0, "failed": 0, "recovered": 0, "pending": 0}
        for stage in self._build_stages(events, workflow_type):
            counts[stage["status"]] = counts.get(stage["status"], 0) + 1
        return counts

    def _publish_state(self, events: list[dict]) -> dict:
        publish_state = {
            "status": "not_started",
            "published": False,
            "folder_name": None,
            "repo_url": None,
            "tree_url": None,
            "branch": None,
            "files": [],
        }
        for event in events:
            payload = event.get("payload") or {}
            artifact_publish = payload.get("artifact_publish") or {}
            if event.get("event_type") == "artifact_publish_started":
                publish_state.update({"status": "running", "published": False})
            if event.get("event_type") == "artifact_publish_failed":
                publish_state.update({"status": "failed", "published": False})
            if event.get("event_type") == "artifact_publish_completed" or artifact_publish.get("status") == "success":
                publish_state.update(
                    {
                        "status": artifact_publish.get("status") or "success",
                        "published": True,
                        "folder_name": artifact_publish.get("folder_name"),
                        "repo_url": artifact_publish.get("repo_url"),
                        "tree_url": artifact_publish.get("tree_url") or artifact_publish.get("folder_url"),
                        "branch": artifact_publish.get("branch"),
                        "files": artifact_publish.get("files") or [],
                        "commit_hash": artifact_publish.get("commit_hash"),
                    }
                )
        return publish_state

    def _artifact_availability(self, artifacts: list[dict]) -> dict:
        markdown = [item for item in artifacts if self._is_markdown(item)]
        pdf = [item for item in artifacts if self._is_pdf(item)]
        bundle = [item for item in artifacts if self._is_bundle(item)]
        return {
            "has_markdown": bool(markdown),
            "has_pdf": bool(pdf),
            "has_bundle": bool(bundle),
            "markdown_count": len(markdown),
            "pdf_count": len(pdf),
            "bundle_count": len(bundle),
            "types": sorted({str(item.get("mime_type") or item.get("artifact_type") or "unknown") for item in artifacts}),
        }

    def _artifact_summary(self, artifact: dict) -> dict:
        return {
            "artifact_id": artifact.get("artifact_id"),
            "artifact_type": artifact.get("artifact_type"),
            "title": artifact.get("title"),
            "mime_type": artifact.get("mime_type"),
            "created_at": artifact.get("created_at"),
            "kind": "bundle" if self._is_bundle(artifact) else "pdf" if self._is_pdf(artifact) else "markdown" if self._is_markdown(artifact) else "other",
            "download_url": f"/api/artifacts/{artifact.get('artifact_id')}",
            "filename": (artifact.get("metadata") or {}).get("filename"),
        }

    def _empty_artifact_actions(self, run_id: str) -> dict:
        return {
            "run_id": run_id,
            "publish_state": {"status": "not_started", "published": False, "folder_name": None, "repo_url": None},
            "repo_folder_url": None,
            "repo_path": None,
            "actions": {},
            "local_artifacts": [],
        }

    @staticmethod
    def _base_repo_actions(*, repo_folder_url: str | None, repo_path: str | None) -> dict:
        return {
            "open_repo": {
                "label": "Open Repo Folder",
                "enabled": bool(repo_folder_url),
                "url": repo_folder_url,
                "source": "published" if repo_folder_url else "missing",
                "reason": None if repo_folder_url else "Publish metadata is not available for this run.",
            },
            "copy_repo_path": {
                "label": "Copy Repo Path",
                "enabled": bool(repo_path),
                "value": repo_path,
                "source": "published" if repo_path else "missing",
                "reason": None if repo_path else "Publish metadata is not available for this run.",
            },
        }

    def _artifact_action(
        self,
        *,
        run_id: str,
        workflow_type: str | None,
        kind: str,
        label: str,
        filename: str,
        artifact: dict | None,
        publish_state: dict,
    ) -> dict:
        published_url = self._published_raw_url(publish_state, filename)
        local_url = f"/api/artifacts/{artifact['artifact_id']}" if artifact and artifact.get("artifact_id") else None
        enabled = bool(published_url or local_url)
        source = "published" if published_url else "local" if local_url else "missing"
        return {
            "kind": kind,
            "label": label,
            "enabled": enabled,
            "url": f"/api/activity/runs/{run_id}/artifact/{kind}/download" if enabled else None,
            "direct_url": published_url or local_url,
            "source": source,
            "artifact_id": artifact.get("artifact_id") if artifact else None,
            "filename": filename,
            "reason": None if enabled else f"{label.replace('Download ', '')} artifact is not available for this run.",
        }

    def _preferred_artifact(self, artifacts: list[dict], kind: str) -> dict | None:
        if kind == "bundle":
            predicate = self._is_bundle
        else:
            predicate = self._is_pdf if kind == "pdf" else self._is_markdown
        candidates = [artifact for artifact in artifacts if predicate(artifact)]
        if not candidates:
            return None
        primary_types = {
            "pdf": {"release_note_pdf", "mop_pdf"},
            "markdown": {"release_note", "mop"},
            "bundle": {"mop_bundle_zip"},
        }
        primary = [
            artifact
            for artifact in candidates
            if str(artifact.get("artifact_type") or "") in primary_types.get(kind, set())
        ]
        return primary[-1] if primary else candidates[-1]

    def _is_bundle(self, artifact: dict) -> bool:
        filename = str((artifact.get("metadata") or {}).get("filename") or "").lower()
        return (
            str(artifact.get("artifact_type") or "") == "mop_bundle_zip"
            or filename == "mop-bundle.zip"
            or str(artifact.get("mime_type") or "").lower() in {"application/zip", "application/x-zip-compressed"}
        )

    def _artifact_repo_web_base(self) -> str | None:
        if not self.settings:
            return None
        repo_url = (self.settings.artifact_git_repo_url or "").strip()
        if not repo_url:
            return None
        parsed = urlparse(repo_url)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            path = parsed.path[:-4] if parsed.path.endswith(".git") else parsed.path
            return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")
        if repo_url.startswith("git@github.com:"):
            path = repo_url.removeprefix("git@github.com:")
            path = path[:-4] if path.endswith(".git") else path
            return f"https://github.com/{path}".rstrip("/")
        return repo_url[:-4] if repo_url.endswith(".git") else repo_url

    def _artifact_repo_raw_base(self) -> str | None:
        web_base = self._artifact_repo_web_base()
        if not web_base:
            return None
        parsed = urlparse(web_base)
        if parsed.netloc.lower() != "github.com":
            return None
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return None
        return f"https://raw.githubusercontent.com/{parts[0]}/{parts[1]}"

    def _published_folder_url(self, publish_state: dict) -> str | None:
        tree_url = publish_state.get("tree_url")
        if tree_url:
            return str(tree_url).rstrip("/")
        folder_name = publish_state.get("folder_name")
        web_base = self._artifact_repo_web_base()
        if not folder_name or not web_base:
            return None
        branch = publish_state.get("branch") or (self.settings.artifact_git_branch if self.settings else "main")
        return f"{web_base}/tree/{quote(branch)}/{quote(str(folder_name).strip('/'))}"

    def _published_repo_path(self, publish_state: dict) -> str | None:
        folder_name = publish_state.get("folder_name")
        if not folder_name:
            return None
        web_base = self._artifact_repo_web_base() or "https://github.com/aveeshek/bosgenesis-artifacts"
        parsed = urlparse(web_base)
        parts = [part for part in parsed.path.split("/") if part]
        repo = "/".join(parts[:2]) if len(parts) >= 2 else web_base
        branch = publish_state.get("branch") or (self.settings.artifact_git_branch if self.settings else "main")
        return f"{repo}/{branch}/{folder_name}"

    def _published_raw_url(self, publish_state: dict, filename: str) -> str | None:
        if not publish_state.get("published") or not publish_state.get("folder_name"):
            return None
        raw_base = self._artifact_repo_raw_base()
        if not raw_base:
            return None
        branch = publish_state.get("branch") or (self.settings.artifact_git_branch if self.settings else "main")
        folder_name = quote(str(publish_state["folder_name"]).strip("/"))
        return f"{raw_base}/{quote(branch)}/{folder_name}/{quote(filename)}"

    def _mop_bundle_context_for_chat(self, detail: dict, artifacts: list[dict]) -> dict | None:
        node = detail.get("node") or {}
        if node.get("workflow_type") != "mop_generation":
            return None
        bundle_artifact = self._preferred_artifact(artifacts, "bundle")
        source = "missing"
        bundle_bytes: bytes | None = None
        filename = self.artifact_filename("mop_generation", "bundle")
        if bundle_artifact:
            filename = (bundle_artifact.get("metadata") or {}).get("filename") or filename
            bundle_bytes = self._read_binary_artifact(bundle_artifact, max_bytes=20_000_000)
            source = "local"
        if bundle_bytes is None:
            publish_state = node.get("publish_state") or self._publish_state(detail.get("events") or [])
            raw_url = self._published_raw_url(publish_state, filename)
            if raw_url:
                bundle_bytes = self._fetch_published_bundle(raw_url, max_bytes=20_000_000)
                source = "published"
        if bundle_bytes is None:
            return {"status": "unavailable", "filename": filename, "reason": "mop-bundle.zip could not be read from local storage or published GitHub raw URL"}
        return self._inspect_mop_bundle_zip(bundle_bytes, filename=filename, source=source)

    def _read_binary_artifact(self, artifact: dict, *, max_bytes: int) -> bytes | None:
        if not self.artifact_storage_root:
            return None
        try:
            root = self.artifact_storage_root.resolve()
            path = (root / str(artifact.get("storage_path") or "")).resolve()
            if root not in path.parents and path != root:
                return None
            if not path.exists() or path.stat().st_size > max_bytes:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def _fetch_published_bundle(self, raw_url: str, *, max_bytes: int) -> bytes | None:
        parsed = urlparse(raw_url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "raw.githubusercontent.com":
            return None
        raw_base = self._artifact_repo_raw_base()
        if not raw_base or not raw_url.startswith(raw_base.rstrip("/") + "/"):
            return None
        try:
            request = Request(raw_url, headers={"User-Agent": "bosgenesis-esda-activity-chat"})
            with urlopen(request, timeout=8) as response:
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > max_bytes:
                    return None
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    return None
                return data
        except (OSError, URLError, ValueError):
            return None

    def _inspect_mop_bundle_zip(self, content: bytes, *, filename: str, source: str) -> dict:
        if not zipfile.is_zipfile(BytesIO(content)):
            return {"status": "unavailable", "filename": filename, "source": source, "reason": "bundle is not a valid zip archive"}
        configmaps: list[dict] = []
        file_names: list[str] = []
        text_files: list[str] = []
        evidence: list[dict] = []
        skipped_large = 0
        with zipfile.ZipFile(BytesIO(content)) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            for info in infos[:600]:
                name = info.filename.replace("\\", "/")
                file_names.append(name)
                if info.file_size > 700_000:
                    skipped_large += 1
                    continue
                if not self._is_bundle_text_file(name):
                    continue
                text_files.append(name)
                try:
                    raw = archive.read(info, pwd=None)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    continue
                text = raw.decode("utf-8", errors="replace")
                discovered = self._configmaps_from_text(name, text)
                if discovered:
                    configmaps.extend(discovered)
                    evidence.extend(
                        {
                            "file": item["file"],
                            "name": item.get("name"),
                            "namespace": item.get("namespace"),
                            "source_label": item.get("source_label"),
                            "excerpt": self._excerpt_around_configmap(text),
                        }
                        for item in discovered[:3]
                    )
                elif "configmap" in name.lower():
                    source_type, source_label = self._bundle_source_category(name)
                    configmaps.append({"file": name, "name": None, "namespace": None, "confidence": "filename_match", "source_type": source_type, "source_label": source_label})
        return {
            "status": "available",
            "filename": filename,
            "source": source,
            "file_count": len(file_names),
            "text_file_count": len(text_files),
            "skipped_large_files": skipped_large,
            "files_sample": file_names[:80],
            "configmap_count": len(configmaps),
            "configmaps": configmaps[:30],
            "evidence": evidence[:10],
        }

    @staticmethod
    def _is_bundle_text_file(name: str) -> bool:
        lowered = name.lower()
        return lowered.endswith((".yaml", ".yml", ".json", ".md", ".txt", ".properties", ".conf", ".ini", ".env"))

    def _configmaps_from_text(self, filename: str, text: str) -> list[dict]:
        lowered = text.lower()
        if "configmap" not in lowered:
            return []
        source_type, source_label = self._bundle_source_category(filename)
        matches: list[dict] = []
        yaml_docs = re.split(r"(?m)^---\s*$", text)
        for doc in yaml_docs:
            if re.search(r"(?im)^\s*kind\s*:\s*ConfigMap\s*$", doc) or re.search(r'"kind"\s*:\s*"ConfigMap"', doc):
                matches.append(
                    {
                        "file": filename,
                        "name": self._clean_manifest_name(self._metadata_value(doc, "name")),
                        "namespace": self._clean_manifest_name(self._metadata_value(doc, "namespace")),
                        "confidence": "kind_configmap",
                        "source_type": source_type,
                        "source_label": source_label,
                    }
                )
        return matches

    @staticmethod
    def _bundle_source_category(filename: str) -> tuple[str, str]:
        lowered = filename.lower().replace("\\", "/")
        if "/kubernetes-manifests/raw/" in lowered or "/kubernetes-manifests/rendered/" in lowered:
            return "raw_manifest", "Raw/rendered manifest"
        if "/templates/" in lowered:
            return "helm_template", "Helm template"
        if lowered.endswith("artifact.json") or "/artifact.json" in lowered:
            return "bundle_metadata", "Bundle metadata"
        if lowered.endswith((".md", ".markdown", ".txt")):
            return "documentation", "Documentation/reference"
        return "reference", "Other"

    @staticmethod
    def _clean_manifest_name(value: str | None) -> str | None:
        if not value:
            return None
        text = value.strip().strip('"\'')
        if text.startswith("{{") or "{{" in text or "}}" in text:
            return "templated"
        return text[:160]

    @staticmethod
    def _metadata_value(text: str, key: str) -> str | None:
        json_match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"\\]+)"', text)
        if json_match:
            return json_match.group(1)[:160]
        metadata_match = re.search(r"(?ims)^\s*metadata\s*:\s*(.*?)(?:^\S|\Z)", text)
        search_area = metadata_match.group(1) if metadata_match else text
        yaml_match = re.search(rf"(?im)^\s*{re.escape(key)}\s*:\s*['\"]?([^'\"\n#]+)", search_area)
        if yaml_match:
            return yaml_match.group(1).strip()[:160]
        return None

    @staticmethod
    def _excerpt_around_configmap(text: str) -> str:
        index = text.lower().find("configmap")
        if index < 0:
            return text[:700]
        start = max(index - 260, 0)
        end = min(index + 700, len(text))
        return text[start:end].strip()
    def _artifact_texts_for_chat(self, artifacts: list[dict]) -> list[dict]:
        texts = []
        for artifact in [item for item in artifacts if self._is_markdown(item)][-3:]:
            text = self._read_text_artifact(artifact)
            if not text:
                continue
            texts.append(
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "title": artifact.get("title"),
                    "kind": "markdown",
                    "text_excerpt": text[:12000],
                    "checksum": hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest(),
                    "sections": self._markdown_sections(text),
                }
            )
        return texts

    def _read_text_artifact(self, artifact: dict) -> str | None:
        if not self.artifact_storage_root:
            return None
        try:
            root = self.artifact_storage_root.resolve()
            path = (root / str(artifact.get("storage_path") or "")).resolve()
            if root not in path.parents and path != root:
                return None
            if not path.exists() or path.stat().st_size > 1_500_000:
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _markdown_sections(self, text: str) -> list[dict]:
        sections = []
        current: dict | None = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("#"):
                heading = line.lstrip("#").strip()
                if current:
                    current["excerpt"] = current["excerpt"].strip()[:900]
                    sections.append(current)
                current = {"heading": heading[:120], "excerpt": ""}
                continue
            if current and line:
                current["excerpt"] += line + " "
        if current:
            current["excerpt"] = current["excerpt"].strip()[:900]
            sections.append(current)
        return sections[:16]

    def _model_profile(self, events: list[dict]) -> dict:
        for event in reversed(events):
            profile = self._find_model_profile(event.get("payload") or {})
            if profile:
                return profile
        return {"profile_id": "unknown", "label": "Unknown model", "short_label": "Model"}

    def _find_model_profile(self, value) -> dict | None:
        if isinstance(value, dict):
            profile = value.get("model_profile")
            if isinstance(profile, dict):
                return {
                    "profile_id": profile.get("profile_id") or profile.get("id") or "unknown",
                    "label": profile.get("label") or profile.get("model_display") or profile.get("model_name") or "Unknown model",
                    "short_label": profile.get("short_label") or profile.get("label") or "Model",
                    "provider": profile.get("provider"),
                    "deployment": profile.get("deployment") or profile.get("deployment_name"),
                }
            if isinstance(profile, str):
                return {"profile_id": profile, "label": profile, "short_label": profile}
            for child in value.values():
                found = self._find_model_profile(child)
                if found:
                    return found
        if isinstance(value, list):
            for child in value:
                found = self._find_model_profile(child)
                if found:
                    return found
        return None

    def _release_name(
        self,
        events: list[dict],
        artifacts: list[dict],
        workflow_type: str | None = None,
        namespace: str | None = None,
    ) -> str:
        if workflow_type == "mop_generation":
            return namespace or self._first_payload_value(events, "namespace") or "MoP"
        for artifact in artifacts:
            title = str(artifact.get("title") or "").strip()
            if title:
                return title[:96]
        for event in events:
            value = self._find_key(event.get("payload") or {}, "release_name")
            if value:
                return str(value)[:96]
        return "current"

    def _first_payload_value(self, events: list[dict], key: str) -> str | None:
        for event in events:
            value = self._find_key(event.get("payload") or {}, key)
            if value:
                return str(value)
        return None

    def _find_key(self, value, key: str):
        if isinstance(value, dict):
            if key in value and value[key]:
                return value[key]
            for child in value.values():
                found = self._find_key(child, key)
                if found:
                    return found
        if isinstance(value, list):
            for child in value:
                found = self._find_key(child, key)
                if found:
                    return found
        return None

    def _resource_label(
        self,
        workflow_type: str | None,
        target_url: str | None,
        namespace: str | None,
        events: list[dict],
    ) -> str:
        if workflow_type == "mop_generation":
            ns = namespace or self._first_payload_value(events, "namespace")
            environment = self._target_environment(target_url)
            if ns and environment:
                return f"{ns} ({environment})"
            return ns or target_url or "Unknown namespace"
        return self._repo_label(target_url)

    def _target_environment(self, target_url: str | None) -> str | None:
        if not target_url:
            return None
        parsed = urlparse(target_url)
        if parsed.scheme == "k8s":
            return parsed.netloc or None
        return None

    def _repo_label(self, github_url: str | None) -> str:
        if not github_url:
            return "Unknown repository"
        parsed = urlparse(github_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        if parts:
            return parts[0]
        return parsed.netloc or github_url

    def _duration_ms(self, events: list[dict], created_at: str | None, updated_at: str | None) -> int:
        start = self._parse_datetime(events[0].get("created_at") if events else created_at)
        terminal_event = next(
            (event for event in reversed(events) if event.get("event_type") in {"run_completed", "run_failed"}),
            None,
        )
        end = self._parse_datetime((terminal_event or {}).get("created_at") or updated_at)
        if not start or not end:
            return 0
        return max(0, int((end - start).total_seconds() * 1000))

    def _duration_label(self, duration_ms: int) -> str:
        if duration_ms <= 0:
            return "-"
        seconds = duration_ms // 1000
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

    def _time_window(self, time_range: str, date_from: str | None, date_to: str | None) -> tuple[datetime | None, datetime | None]:
        explicit_start = self._parse_datetime(date_from)
        explicit_end = self._parse_datetime(date_to)
        if explicit_start or explicit_end:
            return explicit_start, explicit_end
        now = datetime.now(UTC)
        if time_range == "today":
            return now.replace(hour=0, minute=0, second=0, microsecond=0), now
        if time_range == "7d":
            return now - timedelta(days=7), now
        if time_range == "all":
            return None, None
        return now - timedelta(days=30), now

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _safe_event(self, event: dict) -> dict:
        return {
            "event_id": event.get("event_id"),
            "sequence": event.get("sequence"),
            "event_type": event.get("event_type"),
            "message": event.get("message"),
            "created_at": event.get("created_at"),
            "payload": self._safe_payload(event.get("payload") or {}),
        }

    def _safe_payload(self, value, *, depth: int = 0):
        if depth > 3:
            return "..."
        if isinstance(value, dict):
            scrubbed = {}
            for key, child in value.items():
                if key in {"final_report", "preview", "markdown", "content", "pdf_bytes"}:
                    scrubbed[key] = "[omitted]"
                else:
                    scrubbed[key] = self._safe_payload(child, depth=depth + 1)
            return scrubbed
        if isinstance(value, list):
            return [self._safe_payload(item, depth=depth + 1) for item in value[:12]]
        if isinstance(value, str) and len(value) > 360:
            return value[:357] + "..."
        return value

    def _event_summary(self, event: dict) -> str:
        payload = event.get("payload") or {}
        if payload.get("reasoning_summary"):
            return str(payload["reasoning_summary"])
        artifact_publish = payload.get("artifact_publish") or {}
        if artifact_publish.get("folder_name"):
            return f"Published to {artifact_publish['folder_name']}"
        quality = payload.get("quality") or {}
        if quality.get("summary"):
            return str(quality["summary"])
        if payload.get("finding_count") is not None:
            return f"{payload.get('finding_count')} vulnerability signal(s) found for review."
        return str(event.get("message") or event.get("event_type") or "Activity event")

    def _is_markdown(self, artifact: dict) -> bool:
        mime = str(artifact.get("mime_type") or "").lower()
        path = str(artifact.get("storage_path") or "").lower()
        return "markdown" in mime or path.endswith(".md")

    def _is_pdf(self, artifact: dict) -> bool:
        mime = str(artifact.get("mime_type") or "").lower()
        path = str(artifact.get("storage_path") or "").lower()
        return "pdf" in mime or path.endswith(".pdf")








