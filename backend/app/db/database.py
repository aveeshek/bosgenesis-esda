from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.auth.security import hash_password
from backend.app.config import Settings
from backend.app.db.models import (
    AgentMemory,
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

        connect_args = self._connect_args(settings.database_url)
        self.engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def init(self) -> None:
        try:
            Base.metadata.create_all(self.engine)
            self._seed_admin()
        except SQLAlchemyError as exc:
            raise RuntimeError(self._startup_error_message(exc)) from exc

    def _connect_args(self, database_url: str) -> dict:
        if not database_url.startswith("postgresql"):
            return {}
        parsed = urlparse(database_url)
        if "connect_timeout=" in parsed.query:
            return {}
        return {"connect_timeout": max(1, int(self.settings.database_connect_timeout_seconds))}

    def _startup_error_message(self, exc: Exception) -> str:
        parsed = urlparse(self.settings.database_url)
        if self.settings.database_url.startswith("postgresql"):
            host = parsed.hostname or "unknown-host"
            port = parsed.port or 5432
            database = parsed.path.lstrip("/") or "unknown-database"
            return (
                "ESDA could not connect to PostgreSQL during startup. "
                f"Target={host}:{port}/{database}. "
                f"Configured timeout={self.settings.database_connect_timeout_seconds}s. "
                "Check VPN/network route, PostgreSQL service/listener, firewall, credentials, "
                "and DATABASE_URL in .env before restarting ESDA. "
                f"Original error: {exc}"
            )
        return f"ESDA database initialization failed: {exc}"

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
            session = db.get(ChatSession, session_id)
            if session:
                session.updated_at = datetime.now(UTC)

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
    def list_chat_sessions(
        self,
        *,
        user_id: str,
        workflow_type: str | None = None,
        include_hidden: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        with self.database.session() as db:
            sessions = db.scalars(
                select(ChatSession)
                .where(ChatSession.user_id == user_id)
                .order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc())
                .limit(limit * 4)
            ).all()
            result: list[dict] = []
            for session in sessions:
                messages = db.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session.session_id)
                    .order_by(ChatMessage.created_at, ChatMessage.message_id)
                ).all()
                run_ids = list(dict.fromkeys(message.run_id for message in messages if message.run_id))
                if not run_ids:
                    continue
                query = select(AgentRun).where(AgentRun.run_id.in_(run_ids), AgentRun.user_id == user_id)
                if workflow_type:
                    query = query.where(AgentRun.workflow_type == workflow_type)
                runs = db.scalars(query).all()
                runs_by_id = {run.run_id: run for run in runs}
                ordered_runs = [runs_by_id[run_id] for run_id in run_ids if run_id in runs_by_id]
                visible_runs: list[AgentRun] = []
                for run in ordered_runs:
                    view = db.get(UserRunView, {"user_id": user_id, "run_id": run.run_id})
                    hidden_at = view.hidden_at if view else None
                    if hidden_at and not include_hidden:
                        continue
                    visible_runs.append(run)
                if not visible_runs:
                    continue
                latest_run = sorted(
                    visible_runs,
                    key=lambda item: (item.updated_at, item.created_at, item.run_id),
                    reverse=True,
                )[0]
                event_count = int(
                    db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == latest_run.run_id))
                    or 0
                )
                result.append(
                    {
                        "session_id": session.session_id,
                        "title": session.title,
                        "workflow_type": latest_run.workflow_type,
                        "latest_run_id": latest_run.run_id,
                        "status": latest_run.status,
                        "goal": latest_run.goal,
                        "namespace": latest_run.namespace,
                        "run_count": len(visible_runs),
                        "message_count": len(messages),
                        "last_event_sequence": event_count,
                        "created_at": session.created_at.isoformat(),
                        "updated_at": session.updated_at.isoformat(),
                    }
                )
                if len(result) >= limit:
                    break
            return result

    def get_chat_session_snapshot(
        self,
        *,
        session_id: str,
        user_id: str,
        workflow_type: str | None = None,
    ) -> dict | None:
        session = self.get_chat_session(session_id=session_id, user_id=user_id)
        if not session:
            return None
        messages = self.list_chat_messages(session_id=session_id, user_id=user_id) or []
        run_ids = list(dict.fromkeys(message.get("run_id") for message in messages if message.get("run_id")))
        snapshots = []
        for run_id in run_ids:
            snapshot = self.get_run_snapshot(str(run_id))
            if not snapshot or snapshot.get("run", {}).get("user_id") != user_id:
                continue
            if workflow_type and snapshot.get("run", {}).get("workflow_type") != workflow_type:
                continue
            snapshots.append(snapshot)
        latest_snapshot = None
        if snapshots:
            latest_snapshot = sorted(
                snapshots,
                key=lambda item: (item.get("run", {}).get("updated_at") or "", item.get("run", {}).get("created_at") or ""),
                reverse=True,
            )[0]
        return {
            "session": session,
            "messages": messages,
            "runs": snapshots,
            "latest_snapshot": latest_snapshot,
        }

    def list_memories(
        self,
        *,
        user_id: str,
        workflow_type: str,
        memory_type: str | None = None,
        memory_scope: str | None = None,
        scope_id: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        with self.database.session() as db:
            query = select(AgentMemory).where(
                AgentMemory.user_id == user_id,
                AgentMemory.workflow_type == workflow_type,
            )
            if memory_type:
                query = query.where(AgentMemory.memory_type == memory_type)
            if memory_scope:
                query = query.where(AgentMemory.memory_scope == memory_scope)
            if scope_id:
                query = query.where(AgentMemory.scope_id == scope_id)
            rows = db.scalars(query.order_by(AgentMemory.updated_at.desc()).limit(limit)).all()
            return [self._memory_to_dict(row) for row in rows]

    def upsert_memory(
        self,
        *,
        user_id: str,
        workflow_type: str,
        memory_type: str,
        memory_scope: str,
        scope_id: str,
        key: str,
        content: str,
        value_json: dict | None = None,
        importance: int = 1,
    ) -> dict:
        with self.database.session() as db:
            existing = db.scalar(
                select(AgentMemory).where(
                    AgentMemory.user_id == user_id,
                    AgentMemory.workflow_type == workflow_type,
                    AgentMemory.memory_type == memory_type,
                    AgentMemory.memory_scope == memory_scope,
                    AgentMemory.scope_id == scope_id,
                    AgentMemory.key == key,
                )
            )
            now = datetime.now(UTC)
            if existing:
                existing.content = content
                existing.value_json = value_json or {}
                existing.importance = importance
                existing.updated_at = now
                return self._memory_to_dict(existing)
            memory = AgentMemory(
                memory_id=f"mem_{uuid4().hex}",
                user_id=user_id,
                workflow_type=workflow_type,
                memory_type=memory_type,
                memory_scope=memory_scope,
                scope_id=scope_id,
                key=key,
                content=content,
                value_json=value_json or {},
                importance=importance,
                created_at=now,
                updated_at=now,
            )
            db.add(memory)
            return self._memory_to_dict(memory)

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

    def list_tool_calls(self, run_id: str) -> list[dict]:
        with self.database.session() as db:
            calls = db.scalars(
                select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at, ToolCall.tool_call_id)
            ).all()
            return [self._tool_call_to_dict(call) for call in calls]

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

    def get_run_snapshot(self, run_id: str, *, compact: bool = False) -> dict | None:
        # Keep restoration on one checked-out connection. PostgreSQL may be remote
        # during local development, so opening four independent sessions here made
        # history selection unnecessarily slow and prone to partial timeouts.
        with self.database.session() as db:
            run = db.get(AgentRun, run_id)
            if not run:
                return None
            if compact:
                # Historical execution events can contain repeated multi-megabyte
                # observations. Select metadata only so opening the history drawer
                # does not download and decode hundreds of megabytes of JSON.
                event_rows = db.execute(
                    select(
                        RunEvent.event_id,
                        RunEvent.run_id,
                        RunEvent.event_type,
                        RunEvent.message,
                        RunEvent.created_at,
                    )
                    .where(RunEvent.run_id == run_id)
                    .order_by(RunEvent.created_at, RunEvent.event_id)
                ).all()
                events = [
                    {
                        "event_id": row.event_id,
                        "run_id": row.run_id,
                        "sequence": index,
                        "event_type": row.event_type,
                        "message": row.message,
                        "payload": {"payload_omitted": True},
                        "created_at": row.created_at.isoformat(),
                    }
                    for index, row in enumerate(event_rows, start=1)
                ]
                artifact_rows = db.execute(
                    select(
                        Artifact.artifact_id,
                        Artifact.run_id,
                        Artifact.user_id,
                        Artifact.artifact_type,
                        Artifact.title,
                        Artifact.mime_type,
                        Artifact.storage_path,
                        Artifact.created_at,
                    )
                    .where(Artifact.run_id == run_id)
                    .order_by(Artifact.created_at)
                ).all()
                artifacts = [
                    {
                        "artifact_id": row.artifact_id,
                        "run_id": row.run_id,
                        "user_id": row.user_id,
                        "artifact_type": row.artifact_type,
                        "title": row.title,
                        "mime_type": row.mime_type,
                        "storage_path": row.storage_path,
                        "metadata": {"payload_omitted": True},
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in artifact_rows
                ]
                tool_rows = db.execute(
                    select(
                        ToolCall.tool_call_id,
                        ToolCall.run_id,
                        ToolCall.tool_name,
                        ToolCall.status,
                        ToolCall.created_at,
                    )
                    .where(ToolCall.run_id == run_id)
                    .order_by(ToolCall.created_at, ToolCall.tool_call_id)
                ).all()
                tool_calls = [
                    {
                        "tool_call_id": row.tool_call_id,
                        "run_id": row.run_id,
                        "tool_name": row.tool_name,
                        "status": row.status,
                        "request": {"payload_omitted": True},
                        "response": {"payload_omitted": True},
                        "created_at": row.created_at.isoformat(),
                    }
                    for row in tool_rows
                ]
            else:
                event_models = db.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id)
                    .order_by(RunEvent.created_at, RunEvent.event_id)
                ).all()
                events = [
                    self._event_to_dict(event, sequence=index)
                    for index, event in enumerate(event_models, start=1)
                ]
                artifacts = [
                    self._artifact_to_dict(artifact)
                    for artifact in db.scalars(
                        select(Artifact)
                        .where(Artifact.run_id == run_id)
                        .order_by(Artifact.created_at)
                    ).all()
                ]
                tool_calls = [
                    self._tool_call_to_dict(call)
                    for call in db.scalars(
                        select(ToolCall)
                        .where(ToolCall.run_id == run_id)
                        .order_by(ToolCall.created_at, ToolCall.tool_call_id)
                    ).all()
                ]
            return {
                "run": self._run_to_dict(run),
                "events": events,
                "artifacts": artifacts,
                "tool_calls": tool_calls,
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
            artifact_counts = {
                run_id: int(count)
                for run_id, count in db.execute(
                    select(Artifact.run_id, func.count())
                    .where(Artifact.run_id.in_(run_ids))
                    .group_by(Artifact.run_id)
                ).all()
            }
            event_counts = {
                run_id: int(count)
                for run_id, count in db.execute(
                    select(RunEvent.run_id, func.count())
                    .where(RunEvent.run_id.in_(run_ids))
                    .group_by(RunEvent.run_id)
                ).all()
            }
            result = []
            for run in runs:
                view = views_by_run.get(run.run_id)
                hidden_at = view.hidden_at if view else None
                if hidden_at and not include_hidden:
                    continue
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
                        "artifact_count": artifact_counts.get(run.run_id, 0),
                        "last_event_sequence": event_counts.get(run.run_id, 0),
                        "hidden_at": hidden_at.isoformat() if hidden_at else None,
                        "created_at": run.created_at.isoformat(),
                        "updated_at": run.updated_at.isoformat(),
                    }
                )
            return result

    def list_completed_run_sources(
        self,
        *,
        user_id: str,
        workflow_type: str,
        status: str = "completed",
        include_hidden: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Return runs, artifacts, and publish events with three bounded queries."""
        with self.database.session() as db:
            runs = db.scalars(
                select(AgentRun)
                .where(
                    AgentRun.user_id == user_id,
                    AgentRun.workflow_type == workflow_type,
                    AgentRun.status == status,
                )
                .order_by(AgentRun.updated_at.desc(), AgentRun.created_at.desc())
                .limit(limit)
            ).all()
            if not runs:
                return []
            run_ids = [run.run_id for run in runs]
            hidden_ids: set[str] = set()
            if not include_hidden:
                views = db.scalars(
                    select(UserRunView).where(
                        UserRunView.user_id == user_id,
                        UserRunView.run_id.in_(run_ids),
                    )
                ).all()
                hidden_ids = {view.run_id for view in views if view.hidden_at is not None}
            visible_runs = [run for run in runs if run.run_id not in hidden_ids]
            if not visible_runs:
                return []
            visible_ids = [run.run_id for run in visible_runs]
            artifacts = db.scalars(
                select(Artifact)
                .where(Artifact.run_id.in_(visible_ids))
                .order_by(Artifact.created_at, Artifact.artifact_id)
            ).all()
            events = db.scalars(
                select(RunEvent)
                .where(
                    RunEvent.run_id.in_(visible_ids),
                    RunEvent.event_type == "artifact_publish_completed",
                )
                .order_by(RunEvent.created_at, RunEvent.event_id)
            ).all()
            artifacts_by_run: dict[str, list[dict]] = {run_id: [] for run_id in visible_ids}
            for artifact in artifacts:
                artifacts_by_run.setdefault(artifact.run_id, []).append(
                    self._artifact_to_dict(artifact)
                )
            events_by_run: dict[str, list[dict]] = {run_id: [] for run_id in visible_ids}
            event_sequences: dict[str, int] = {}
            for event in events:
                event_sequences[event.run_id] = event_sequences.get(event.run_id, 0) + 1
                events_by_run.setdefault(event.run_id, []).append(
                    self._event_to_dict(event, sequence=event_sequences[event.run_id])
                )
            return [
                {
                    "run": {
                        **self._run_to_dict(run),
                        "title": self._generate_session_name(run),
                    },
                    "artifacts": artifacts_by_run.get(run.run_id, []),
                    "events": events_by_run.get(run.run_id, []),
                }
                for run in visible_runs
            ]
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
            "remediation_approval_requested",
            "remediation_approval_blocked",
            "remediation_approval_confirmed",
            "remediation_execution_started",
            "remediation_execution_blocked",
            "remediation_action_executed",
            "remediation_verified",
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
    def _memory_to_dict(memory: AgentMemory) -> dict:
        return {
            "memory_id": memory.memory_id,
            "user_id": memory.user_id,
            "workflow_type": memory.workflow_type,
            "memory_type": memory.memory_type,
            "memory_scope": memory.memory_scope,
            "scope_id": memory.scope_id,
            "key": memory.key,
            "content": memory.content,
            "value_json": memory.value_json,
            "importance": memory.importance,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
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
    def _tool_call_to_dict(tool_call: ToolCall) -> dict:
        return {
            "tool_call_id": tool_call.tool_call_id,
            "run_id": tool_call.run_id,
            "tool_name": tool_call.tool_name,
            "status": tool_call.status,
            "request": tool_call.request_json,
            "response": tool_call.response_json,
            "created_at": tool_call.created_at.isoformat(),
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
