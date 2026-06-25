from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="session")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), ForeignKey("chat_sessions.session_id"), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("agent_runs.run_id"))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.user_id"), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    target_url: Mapped[str | None] = mapped_column(Text)
    namespace: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    final_report: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    events: Mapped[list["RunEvent"]] = relationship(back_populates="run")
    plan_steps: Mapped[list["PlanStep"]] = relationship(back_populates="run")
    tool_calls: Mapped[list["ToolCall"]] = relationship(back_populates="run")
    event_logs: Mapped[list["AgentEventLog"]] = relationship(back_populates="run")
    llm_review_logs: Mapped[list["LlmReviewLog"]] = relationship(back_populates="run")
    tool_execution_logs: Mapped[list["ToolExecutionLog"]] = relationship(back_populates="run")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="run")
    approvals: Mapped[list["ApprovalRequest"]] = relationship(back_populates="run")


class Artifact(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_runs.run_id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="artifacts")

class RunEvent(Base):
    __tablename__ = "run_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_runs.run_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="events")


class PlanStep(Base):
    __tablename__ = "plan_steps"

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_runs.run_id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="planned")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="plan_steps")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    tool_call_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_runs.run_id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="tool_calls")


class AgentEventLog(Base):
    __tablename__ = "agent_event_logs"

    event_log_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_runs.run_id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_node: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="event_logs")


class LlmReviewLog(Base):
    __tablename__ = "llm_reasoning_review_logs"

    review_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_runs.run_id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_node: Mapped[str] = mapped_column(String(128), nullable=False)
    model_deployment: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_intent: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reasoning_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_choice_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tool_choice_explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    validation_explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    final_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    redaction_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    human_review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="llm_review_logs")


class ToolExecutionLog(Base):
    __tablename__ = "tool_execution_logs"

    tool_log_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_runs.run_id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    tool_category: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    policy_decision: Mapped[str] = mapped_column(String(32), nullable=False, default="allow")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    response_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    run: Mapped[AgentRun] = relationship(back_populates="tool_execution_logs")

class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("agent_runs.run_id"))
    requested_by_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(String(64))
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    namespace: Mapped[str | None] = mapped_column(String(128))
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy_decision_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expected_impact: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rollback_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    run: Mapped[AgentRun | None] = relationship(back_populates="approvals")

class Procedure(Base):
    __tablename__ = "procedures"

    procedure_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ProcedureVersion(Base):
    __tablename__ = "procedure_versions"

    version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    procedure_id: Mapped[str] = mapped_column(String(64), ForeignKey("procedures.procedure_id"), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    approved_by_user_id: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ProcedureStep(Base):
    __tablename__ = "procedure_steps"

    step_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(String(64), ForeignKey("procedure_versions.version_id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False, default="low")
    arguments_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rollback_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ProcedurePolicy(Base):
    __tablename__ = "procedure_policies"

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    procedure_id: Mapped[str] = mapped_column(String(64), ForeignKey("procedures.procedure_id"), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False, default="local")
    namespace: Mapped[str | None] = mapped_column(String(128))
    policy_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class L4AuditRecord(Base):
    __tablename__ = "l4_audit_records"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("agent_runs.run_id"))
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace: Mapped[str | None] = mapped_column(String(128))
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reasons_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    odd_config_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    tool_sequence_json: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    procedure_id: Mapped[str | None] = mapped_column(String(64))
    procedure_version: Mapped[str | None] = mapped_column(String(64))
    stop_conditions_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    human_review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
