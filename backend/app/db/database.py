from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.auth.security import hash_password
from backend.app.config import Settings
from backend.app.db.models import (
    AgentRun,
    ApprovalRequest,
    Artifact,
    Base,
    ChatMessage,
    ChatSession,
    PlanStep,
    ProcedureVersion,
    ProcedureStep,
    ProcedurePolicy,
    Procedure,
    L4AuditRecord,
    RunEvent,
    ToolCall,
    User,
    UserRunView,
)


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        from sqlalchemy import create_engine

        self.engine = create_engine(settings.database_url, pool_pre_ping=True)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def init(self) -> None:
        Base.metadata.create_all(self.engine)
        self._seed_admin()

    def _seed_admin(self) -> None:
        with self.session() as db:
            existing = db.scalar(select(User).where(User.username == self.settings.admin_username))
            if existing:
                return
            db.add(
                User(
                    user_id=f"usr_{uuid4().hex}",
                    username=self.settings.admin_username,
                    password_hash=hash_password(self.settings.admin_password),
                    roles=["admin", "operator", "approver"],
                )
            )

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class RunRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_chat_session(self, *, session_id: str, user_id: str, title: str) -> None:
        with self.database.session() as db:
            db.add(ChatSession(session_id=session_id, user_id=user_id, title=title))

    def add_chat_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        run_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        with self.database.session() as db:
            db.add(
                ChatMessage(
                    message_id=f"msg_{uuid4().hex}",
                    session_id=session_id,
                    run_id=run_id,
                    role=role,
                    content=content,
                    payload=payload or {},
                )
            )

    def get_chat_session(self, *, session_id: str, user_id: str) -> dict | None:
        with self.database.session() as db:
            session = db.get(ChatSession, session_id)
            if not session or session.user_id != user_id:
                return None
            return {
                "session_id": session.session_id,
                "user_id": session.user_id,
                "title": session.title,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            }

    def list_chat_messages(self, *, session_id: str, user_id: str) -> list[dict] | None:
        with self.database.session() as db:
            session = db.get(ChatSession, session_id)
            if not session or session.user_id != user_id:
                return None
            messages = db.scalars(
                select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
            ).all()
            return [
                {
                    "message_id": message.message_id,
                    "session_id": message.session_id,
                    "run_id": message.run_id,
                    "role": message.role,
                    "content": message.content,
                    "payload": message.payload,
                    "created_at": message.created_at.isoformat(),
                }
                for message in messages
            ]
    def create_run(
        self,
        *,
        run_id: str,
        user_id: str,
        goal: str,
        target_url: str,
        namespace: str | None,
        workflow_type: str = "health_check_diagnostic",
    ) -> None:
        with self.database.session() as db:
            db.add(
                AgentRun(
                    run_id=run_id,
                    user_id=user_id,
                    workflow_type=workflow_type,
                    goal=goal,
                    target_url=target_url,
                    namespace=namespace,
                    status="created",
                )
            )
            db.add(UserRunView(user_id=user_id, run_id=run_id))

    def update_status(self, run_id: str, status: str, final_report: str | None = None) -> None:
        with self.database.session() as db:
            run = db.get(AgentRun, run_id)
            if not run:
                return
            run.status = status
            if final_report is not None:
                run.final_report = final_report

    def add_event(self, run_id: str, event_type: str, message: str, payload: dict) -> dict:
        event = RunEvent(
            event_id=f"evt_{uuid4().hex}",
            run_id=run_id,
            event_type=event_type,
            message=message,
            payload=payload,
        )
        with self.database.session() as db:
            sequence = int(
                db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run_id)) or 0
            ) + 1
            db.add(event)
        return {
            "event_id": event.event_id,
            "run_id": run_id,
            "sequence": sequence,
            "event_type": event_type,
            "message": message,
            "payload": payload,
            "created_at": event.created_at.isoformat(),
        }

    def add_plan_steps(self, *, run_id: str, steps: list[dict]) -> None:
        with self.database.session() as db:
            for index, step in enumerate(steps, start=1):
                db.add(
                    PlanStep(
                        step_id=f"step_{uuid4().hex}",
                        run_id=run_id,
                        position=index,
                        title=str(step.get("title") or f"Step {index}"),
                        tool_name=str(step.get("tool") or ""),
                        risk_level=str(step.get("risk") or "low"),
                        status=str(step.get("status") or "planned"),
                        payload=step,
                    )
                )

    def add_tool_call(
        self,
        *,
        run_id: str,
        tool_name: str,
        status: str,
        request_json: dict,
        response_json: dict,
    ) -> None:
        with self.database.session() as db:
            db.add(
                ToolCall(
                    tool_call_id=f"tc_{uuid4().hex}",
                    run_id=run_id,
                    tool_name=tool_name,
                    status=status,
                    request_json=request_json,
                    response_json=response_json,
                )
            )

    def get_run(self, run_id: str) -> AgentRun | None:
        with self.database.session() as db:
            return db.get(AgentRun, run_id)

    def list_events(
        self,
        run_id: str,
        *,
        after_event_id: str | None = None,
        after_sequence: int | None = None,
    ) -> list[dict]:
        with self.database.session() as db:
            events = db.scalars(
                select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.created_at, RunEvent.event_id)
            ).all()
            serialized = [self._event_to_dict(event, sequence=index) for index, event in enumerate(events, start=1)]
        if after_sequence is not None:
            return [event for event in serialized if int(event["sequence"]) > after_sequence]
        if after_event_id:
            for index, event in enumerate(serialized):
                if event["event_id"] == after_event_id:
                    return serialized[index + 1 :]
        return serialized

    def get_run_snapshot(self, run_id: str) -> dict | None:
        run = self.get_run(run_id)
        if not run:
            return None
        events = self.list_events(run_id)
        artifacts = self.list_artifacts(run_id)
        return {
            "run": self._run_to_dict(run),
            "events": events,
            "artifacts": artifacts,
            "last_event_id": events[-1]["event_id"] if events else None,
            "last_event_sequence": events[-1]["sequence"] if events else 0,
        }

    def list_transactions(self, *, user_id: str, include_hidden: bool = False, limit: int = 50) -> list[dict]:
        with self.database.session() as db:
            runs = db.scalars(
                select(AgentRun)
                .where(AgentRun.user_id == user_id)
                .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
                .limit(limit)
            ).all()
            result = []
            for run in runs:
                view = db.get(UserRunView, {"user_id": user_id, "run_id": run.run_id})
                hidden_at = view.hidden_at if view else None
                if hidden_at and not include_hidden:
                    continue
                artifact_count = int(
                    db.scalar(select(func.count()).select_from(Artifact).where(Artifact.run_id == run.run_id)) or 0
                )
                event_count = int(
                    db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run.run_id)) or 0
                )
                result.append(
                    {
                        "run_id": run.run_id,
                        "workflow_type": run.workflow_type,
                        "title": self._generate_session_name(run),
                        "session_name": self._generate_session_name(run),
                        "goal": run.goal,
                        "status": run.status,
                        "target_url": run.target_url,
                        "namespace": run.namespace,
                        "artifact_count": artifact_count,
                        "last_event_sequence": event_count,
                        "hidden_at": hidden_at.isoformat() if hidden_at else None,
                        "created_at": run.created_at.isoformat(),
                        "updated_at": run.updated_at.isoformat(),
                    }
                )
            return result

    def list_activity_snapshots(
        self,
        *,
        user_id: str,
        workflow_types: list[str] | tuple[str, ...] | None = None,
        include_hidden: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        timeline_event_types = {
            "run_started",
            "workflow_classified",
            "planning_started",
            "plan_created",
            "repo_clone_started",
            "repo_clone_completed",
            "vulnerability_scan_completed",
            "quality_scan_completed",
            "repo_cleanup_completed",
            "draft_started",
            "validation_completed",
            "recovery_recommendation",
            "artifact_created",
            "artifact_publish_started",
            "artifact_publish_completed",
            "artifact_publish_failed",
            "run_completed",
            "run_failed",
            "preflight_completed",
            "agent_health_checked",
            "bundle_validated",
            "dry_run_job_created",
            "mutation_job_created",
            "job_started",
            "job_state_polled",
            "observations_received",
            "decision_required",
            "policy_decision_recorded",
            "instruction_submitted",
            "approval_submitted",
            "reports_updated",
            "rollback_cleanup_updated",
            "safe_reasoning_summary",
        }
        with self.database.session() as db:
            query = select(AgentRun).where(AgentRun.user_id == user_id)
            if workflow_types:
                query = query.where(AgentRun.workflow_type.in_(list(workflow_types)))
            runs = db.scalars(
                query.order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc()).limit(limit)
            ).all()
            if not runs:
                return []

            run_ids = [run.run_id for run in runs]
            views = db.scalars(
                select(UserRunView).where(
                    UserRunView.user_id == user_id,
                    UserRunView.run_id.in_(run_ids),
                )
            ).all()
            views_by_run = {view.run_id: view for view in views}
            visible_runs = []
            for run in runs:
                view = views_by_run.get(run.run_id)
                if view and view.hidden_at and not include_hidden:
                    continue
                visible_runs.append(run)
            if not visible_runs:
                return []

            visible_run_ids = [run.run_id for run in visible_runs]
            event_counts = {
                run_id: int(count)
                for run_id, count in db.execute(
                    select(RunEvent.run_id, func.count())
                    .where(RunEvent.run_id.in_(visible_run_ids))
                    .group_by(RunEvent.run_id)
                ).all()
            }
            events_by_run = {run_id: [] for run_id in visible_run_ids}
            events = db.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id.in_(visible_run_ids),
                    RunEvent.event_type.in_(timeline_event_types),
                )
                .order_by(RunEvent.run_id, RunEvent.created_at, RunEvent.event_id)
            ).all()
            for event in events:
                grouped = events_by_run.setdefault(event.run_id, [])
                grouped.append(self._event_to_dict(event, sequence=len(grouped) + 1))

            artifacts_by_run = {run_id: [] for run_id in visible_run_ids}
            artifacts = db.scalars(
                select(Artifact)
                .where(Artifact.run_id.in_(visible_run_ids))
                .order_by(Artifact.run_id, Artifact.created_at)
            ).all()
            for artifact in artifacts:
                artifacts_by_run.setdefault(artifact.run_id, []).append(self._artifact_to_dict(artifact))

            snapshots = []
            for run in visible_runs:
                view = views_by_run.get(run.run_id)
                run_artifacts = artifacts_by_run.get(run.run_id, [])
                snapshots.append(
                    {
                        "transaction": {
                            "run_id": run.run_id,
                            "workflow_type": run.workflow_type,
                            "title": self._generate_session_name(run),
                            "session_name": self._generate_session_name(run),
                            "goal": run.goal,
                            "status": run.status,
                            "target_url": run.target_url,
                            "namespace": run.namespace,
                            "artifact_count": len(run_artifacts),
                            "last_event_sequence": event_counts.get(run.run_id, 0),
                            "hidden_at": view.hidden_at.isoformat() if view and view.hidden_at else None,
                            "created_at": run.created_at.isoformat(),
                            "updated_at": run.updated_at.isoformat(),
                        },
                        "events": events_by_run.get(run.run_id, []),
                        "artifacts": run_artifacts,
                    }
                )
            return snapshots

    def list_release_note_activity_snapshots(
        self,
        *,
        user_id: str,
        include_hidden: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        return self.list_activity_snapshots(
            user_id=user_id,
            workflow_types=("release_note_creation",),
            include_hidden=include_hidden,
            limit=limit,
        )

    def mark_transaction_opened(self, *, user_id: str, run_id: str) -> None:
        with self.database.session() as db:
            view = db.get(UserRunView, {"user_id": user_id, "run_id": run_id})
            if not view:
                db.add(UserRunView(user_id=user_id, run_id=run_id, last_opened_at=datetime.now(UTC)))
                return
            view.last_opened_at = datetime.now(UTC)

    def clear_transaction(self, *, user_id: str, run_id: str) -> bool:
        with self.database.session() as db:
            run = db.get(AgentRun, run_id)
            if not run or run.user_id != user_id:
                return False
            view = db.get(UserRunView, {"user_id": user_id, "run_id": run_id})
            if not view:
                db.add(UserRunView(user_id=user_id, run_id=run_id, hidden_at=datetime.now(UTC)))
                return True
            view.hidden_at = datetime.now(UTC)
            return True

    def clear_transactions(self, *, user_id: str, workflow_type: str | None = None) -> int:
        with self.database.session() as db:
            query = select(AgentRun).where(AgentRun.user_id == user_id)
            if workflow_type:
                query = query.where(AgentRun.workflow_type == workflow_type)
            runs = db.scalars(query).all()
            hidden_at = datetime.now(UTC)
            cleared = 0
            for run in runs:
                view = db.get(UserRunView, {"user_id": user_id, "run_id": run.run_id})
                if not view:
                    db.add(UserRunView(user_id=user_id, run_id=run.run_id, hidden_at=hidden_at))
                    cleared += 1
                    continue
                if view.hidden_at is None:
                    cleared += 1
                view.hidden_at = hidden_at
            return cleared

    def create_artifact(
        self,
        *,
        artifact_id: str,
        run_id: str,
        user_id: str,
        artifact_type: str,
        title: str,
        mime_type: str,
        storage_path: str,
        metadata: dict,
    ) -> dict:
        artifact = Artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            user_id=user_id,
            artifact_type=artifact_type,
            title=title,
            mime_type=mime_type,
            storage_path=storage_path,
            metadata_json=metadata,
        )
        with self.database.session() as db:
            db.add(artifact)
        return self._artifact_to_dict(artifact)

    def list_artifacts(self, run_id: str) -> list[dict]:
        with self.database.session() as db:
            artifacts = db.scalars(
                select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at)
            ).all()
            return [self._artifact_to_dict(artifact) for artifact in artifacts]

    def get_artifact(self, artifact_id: str) -> dict | None:
        with self.database.session() as db:
            artifact = db.get(Artifact, artifact_id)
            if not artifact:
                return None
            return self._artifact_to_dict(artifact)

    def create_approval_request(
        self,
        *,
        approval_id: str,
        run_id: str | None,
        requested_by_user_id: str,
        workflow_type: str,
        tool_name: str,
        environment: str,
        namespace: str | None,
        risk_level: str,
        request_json: dict,
        policy_decision: dict,
        expected_impact: str,
        rollback_note: str,
        expires_at: datetime,
    ) -> dict:
        approval = ApprovalRequest(
            approval_id=approval_id,
            run_id=run_id,
            requested_by_user_id=requested_by_user_id,
            workflow_type=workflow_type,
            tool_name=tool_name,
            environment=environment,
            namespace=namespace,
            risk_level=risk_level,
            request_json=request_json,
            policy_decision_json=policy_decision,
            expected_impact=expected_impact,
            rollback_note=rollback_note,
            expires_at=expires_at,
        )
        with self.database.session() as db:
            db.add(approval)
        return self._approval_to_dict(approval)

    def list_approvals(self, *, status: str | None = None) -> list[dict]:
        with self.database.session() as db:
            query = select(ApprovalRequest)
            if status:
                query = query.where(ApprovalRequest.status == status)
            approvals = db.scalars(query.order_by(ApprovalRequest.created_at.desc())).all()
            return [self._approval_to_dict(approval) for approval in approvals]

    def get_approval(self, approval_id: str) -> dict | None:
        with self.database.session() as db:
            approval = db.get(ApprovalRequest, approval_id)
            if not approval:
                return None
            return self._approval_to_dict(approval)

    def update_approval(
        self,
        approval_id: str,
        *,
        status: str | None = None,
        reviewed_by_user_id: str | None = None,
        review_notes: str | None = None,
        request_json: dict | None = None,
        policy_decision: dict | None = None,
        risk_level: str | None = None,
        expected_impact: str | None = None,
        rollback_note: str | None = None,
    ) -> dict | None:
        with self.database.session() as db:
            approval = db.get(ApprovalRequest, approval_id)
            if not approval:
                return None
            if status is not None:
                approval.status = status
            if reviewed_by_user_id is not None:
                approval.reviewed_by_user_id = reviewed_by_user_id
            if review_notes is not None:
                approval.review_notes = review_notes
            if request_json is not None:
                approval.request_json = request_json
            if policy_decision is not None:
                approval.policy_decision_json = policy_decision
            if risk_level is not None:
                approval.risk_level = risk_level
            if expected_impact is not None:
                approval.expected_impact = expected_impact
            if rollback_note is not None:
                approval.rollback_note = rollback_note
            approval.updated_at = datetime.now(UTC)
            if status in {"approved", "rejected", "expired"}:
                approval.decided_at = datetime.now(UTC)
            return self._approval_to_dict(approval)


    def create_procedure(
        self,
        *,
        procedure_id: str,
        name: str,
        workflow_type: str,
        owner_user_id: str,
        version: str,
        status: str,
        approved_by_user_id: str | None,
        steps: list[dict],
        policies: list[dict],
        metadata: dict,
    ) -> dict:
        version_id = f"pver_{uuid4().hex}"
        approved_at = datetime.now(UTC) if status == "approved" else None
        with self.database.session() as db:
            procedure = Procedure(
                procedure_id=procedure_id,
                name=name,
                workflow_type=workflow_type,
                owner_user_id=owner_user_id,
                status=status,
            )
            db.add(procedure)
            db.add(
                ProcedureVersion(
                    version_id=version_id,
                    procedure_id=procedure_id,
                    version=version,
                    status=status,
                    metadata_json=metadata,
                    approved_by_user_id=approved_by_user_id,
                    approved_at=approved_at,
                )
            )
            for index, step in enumerate(steps, start=1):
                db.add(
                    ProcedureStep(
                        step_id=f"pstep_{uuid4().hex}",
                        version_id=version_id,
                        position=index,
                        title=str(step.get("title") or f"Step {index}"),
                        tool_name=str(step.get("tool_name") or ""),
                        risk_level=str(step.get("risk_level") or "low"),
                        arguments_json=step.get("arguments") or {},
                        validation_json=step.get("validation") or {},
                        rollback_json=step.get("rollback") or {},
                    )
                )
            for policy in policies:
                db.add(
                    ProcedurePolicy(
                        policy_id=f"ppol_{uuid4().hex}",
                        procedure_id=procedure_id,
                        environment=str(policy.get("environment") or "local"),
                        namespace=policy.get("namespace"),
                        policy_json=policy,
                    )
                )
        procedure = self.get_approved_procedure(procedure_id=procedure_id, version=version)
        if procedure:
            return procedure
        return {
            "procedure_id": procedure_id,
            "name": name,
            "workflow_type": workflow_type,
            "status": status,
            "version": version,
            "steps": steps,
            "policies": policies,
            "metadata": metadata,
        }

    def get_approved_procedure(self, *, procedure_id: str, version: str | None = None) -> dict | None:
        with self.database.session() as db:
            procedure = db.get(Procedure, procedure_id)
            if not procedure or procedure.status != "approved":
                return None
            query = select(ProcedureVersion).where(
                ProcedureVersion.procedure_id == procedure_id,
                ProcedureVersion.status == "approved",
            )
            if version:
                query = query.where(ProcedureVersion.version == version)
            procedure_version = db.scalars(query.order_by(ProcedureVersion.created_at.desc())).first()
            if not procedure_version:
                return None
            steps = db.scalars(
                select(ProcedureStep)
                .where(ProcedureStep.version_id == procedure_version.version_id)
                .order_by(ProcedureStep.position)
            ).all()
            policies = db.scalars(select(ProcedurePolicy).where(ProcedurePolicy.procedure_id == procedure_id)).all()
            return self._procedure_to_dict(procedure, procedure_version, steps, policies)

    def list_procedures(self) -> list[dict]:
        with self.database.session() as db:
            procedures = db.scalars(select(Procedure).order_by(Procedure.created_at.desc())).all()
            result = []
            for procedure in procedures:
                version = db.scalars(
                    select(ProcedureVersion)
                    .where(ProcedureVersion.procedure_id == procedure.procedure_id)
                    .order_by(ProcedureVersion.created_at.desc())
                ).first()
                steps = []
                policies = db.scalars(
                    select(ProcedurePolicy).where(ProcedurePolicy.procedure_id == procedure.procedure_id)
                ).all()
                if version:
                    steps = db.scalars(
                        select(ProcedureStep)
                        .where(ProcedureStep.version_id == version.version_id)
                        .order_by(ProcedureStep.position)
                    ).all()
                result.append(self._procedure_to_dict(procedure, version, steps, policies))
            return result

    def create_l4_audit_record(
        self,
        *,
        run_id: str | None,
        user_id: str,
        workflow_type: str,
        environment: str,
        namespace: str | None,
        eligible: bool,
        decision: str,
        reasons: list[str],
        odd_config: dict,
        tool_sequence: list[dict],
        procedure_id: str | None,
        procedure_version: str | None,
        stop_conditions: dict,
    ) -> dict:
        audit = L4AuditRecord(
            audit_id=f"l4aud_{uuid4().hex}",
            run_id=run_id if run_id and run_id.startswith("run_") else None,
            user_id=user_id,
            workflow_type=workflow_type,
            environment=environment,
            namespace=namespace,
            eligible=eligible,
            decision=decision,
            reasons_json=reasons,
            odd_config_json=odd_config,
            tool_sequence_json=tool_sequence,
            procedure_id=procedure_id,
            procedure_version=procedure_version,
            stop_conditions_json=stop_conditions,
        )
        with self.database.session() as db:
            db.add(audit)
        return self._l4_audit_to_dict(audit)

    def list_l4_audit_records(self, *, limit: int = 50) -> list[dict]:
        with self.database.session() as db:
            audits = db.scalars(
                select(L4AuditRecord).order_by(L4AuditRecord.created_at.desc()).limit(limit)
            ).all()
            return [self._l4_audit_to_dict(audit) for audit in audits]

    def get_l4_audit_record(self, audit_id: str) -> dict | None:
        with self.database.session() as db:
            audit = db.get(L4AuditRecord, audit_id)
            if not audit:
                return None
            return self._l4_audit_to_dict(audit)

    @staticmethod
    def _generate_session_name(run: AgentRun) -> str:
        adjectives = [
            "Coral",
            "Aurora",
            "Nova",
            "Pulse",
            "Orbit",
            "Signal",
            "Vertex",
            "Prism",
            "Beacon",
            "Comet",
            "Nimbus",
            "Vector",
        ]
        nouns = [
            "Sprint",
            "Forge",
            "Scout",
            "Relay",
            "Atlas",
            "Pilot",
            "Quest",
            "Spark",
            "Beacon",
            "Trail",
            "Launch",
            "Echo",
        ]
        seed = sum(ord(character) for character in run.run_id)
        adjective = adjectives[seed % len(adjectives)]
        noun = nouns[(seed // len(adjectives)) % len(nouns)]
        suffix = run.run_id.rsplit("_", 1)[-1][-5:].upper()
        target = RunRepository._target_slug(run.target_url)
        if target:
            return f"{adjective} {noun} - {target} - {suffix}"
        return f"{adjective} {noun} - {suffix}"

    @staticmethod
    def _target_slug(target_url: str | None) -> str:
        if not target_url:
            return ""
        parsed = urlparse(target_url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            slug = path_parts[-1]
        else:
            slug = parsed.netloc or target_url
        return slug[:42]

    @staticmethod
    def _run_to_dict(run: AgentRun) -> dict:
        return {
            "run_id": run.run_id,
            "user_id": run.user_id,
            "workflow_type": run.workflow_type,
            "status": run.status,
            "goal": run.goal,
            "target_url": run.target_url,
            "namespace": run.namespace,
            "final_report": run.final_report,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }

    @staticmethod
    def _event_to_dict(event: RunEvent, *, sequence: int) -> dict:
        return {
            "event_id": event.event_id,
            "run_id": event.run_id,
            "sequence": sequence,
            "event_type": event.event_type,
            "message": event.message,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        }
    @staticmethod
    def _procedure_to_dict(
        procedure: Procedure,
        version: ProcedureVersion | None,
        steps: list[ProcedureStep],
        policies: list[ProcedurePolicy],
    ) -> dict:
        return {
            "procedure_id": procedure.procedure_id,
            "name": procedure.name,
            "workflow_type": procedure.workflow_type,
            "owner_user_id": procedure.owner_user_id,
            "status": procedure.status,
            "version_id": version.version_id if version else None,
            "version": version.version if version else None,
            "version_status": version.status if version else None,
            "metadata": version.metadata_json if version else {},
            "approved_by_user_id": version.approved_by_user_id if version else None,
            "approved_at": version.approved_at.isoformat() if version and version.approved_at else None,
            "steps": [
                {
                    "step_id": step.step_id,
                    "position": step.position,
                    "title": step.title,
                    "tool_name": step.tool_name,
                    "risk_level": step.risk_level,
                    "arguments": step.arguments_json,
                    "validation": step.validation_json,
                    "rollback": step.rollback_json,
                }
                for step in steps
            ],
            "policies": [
                {
                    "policy_id": policy.policy_id,
                    "environment": policy.environment,
                    "namespace": policy.namespace,
                    "policy": policy.policy_json,
                }
                for policy in policies
            ],
            "created_at": procedure.created_at.isoformat(),
            "updated_at": procedure.updated_at.isoformat(),
        }

    @staticmethod
    def _l4_audit_to_dict(audit: L4AuditRecord) -> dict:
        return {
            "audit_id": audit.audit_id,
            "run_id": audit.run_id,
            "user_id": audit.user_id,
            "workflow_type": audit.workflow_type,
            "environment": audit.environment,
            "namespace": audit.namespace,
            "eligible": audit.eligible,
            "decision": audit.decision,
            "reasons": audit.reasons_json,
            "odd_config": audit.odd_config_json,
            "tool_sequence": audit.tool_sequence_json,
            "procedure_id": audit.procedure_id,
            "procedure_version": audit.procedure_version,
            "stop_conditions": audit.stop_conditions_json,
            "human_review_status": audit.human_review_status,
            "created_at": audit.created_at.isoformat(),
        }
    @staticmethod
    def _approval_to_dict(approval: ApprovalRequest) -> dict:
        return {
            "approval_id": approval.approval_id,
            "run_id": approval.run_id,
            "requested_by_user_id": approval.requested_by_user_id,
            "reviewed_by_user_id": approval.reviewed_by_user_id,
            "workflow_type": approval.workflow_type,
            "tool_name": approval.tool_name,
            "environment": approval.environment,
            "namespace": approval.namespace,
            "risk_level": approval.risk_level,
            "status": approval.status,
            "request": approval.request_json,
            "policy_decision": approval.policy_decision_json,
            "expected_impact": approval.expected_impact,
            "rollback_note": approval.rollback_note,
            "review_notes": approval.review_notes,
            "expires_at": approval.expires_at.isoformat(),
            "decided_at": approval.decided_at.isoformat() if approval.decided_at else None,
            "created_at": approval.created_at.isoformat(),
            "updated_at": approval.updated_at.isoformat(),
        }
    @staticmethod
    def _artifact_to_dict(artifact: Artifact) -> dict:
        return {
            "artifact_id": artifact.artifact_id,
            "run_id": artifact.run_id,
            "user_id": artifact.user_id,
            "artifact_type": artifact.artifact_type,
            "title": artifact.title,
            "mime_type": artifact.mime_type,
            "storage_path": artifact.storage_path,
            "metadata": artifact.metadata_json,
            "created_at": artifact.created_at.isoformat(),
        }
