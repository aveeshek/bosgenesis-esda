import asyncio
from uuid import uuid4

from backend.app.config import Settings
from backend.app.db.database import Database
from backend.app.db.models import AgentEventLog, LlmReviewLog, ToolExecutionLog
from backend.app.logging.redaction import redact


class PostgresLogger:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._available = True

    def init(self) -> None:
        self._available = True

    async def event(
        self,
        *,
        run_id: str,
        user_id: str,
        graph_node: str,
        event_type: str,
        message: str,
        payload: dict | None = None,
        severity: str = "INFO",
        duration_ms: int = 0,
        workflow_type: str = "health_check_diagnostic",
    ) -> None:
        await asyncio.to_thread(
            self._write_event,
            run_id,
            user_id,
            graph_node,
            event_type,
            message,
            payload or {},
            severity,
            duration_ms,
            workflow_type,
        )

    async def llm_review(
        self,
        *,
        run_id: str,
        user_id: str,
        graph_node: str,
        user_intent: str,
        plan: dict,
        reasoning_summary: str,
        final_answer: str = "",
        workflow_type: str = "health_check_diagnostic",
        model_deployment: str | None = None,
    ) -> None:
        if not self.settings.llm_review_logging_enabled:
            return
        await asyncio.to_thread(
            self._write_llm_review,
            run_id,
            user_id,
            graph_node,
            user_intent,
            plan,
            reasoning_summary,
            final_answer,
            workflow_type,
            model_deployment,
        )

    async def tool(
        self,
        *,
        run_id: str,
        tool_name: str,
        tool_category: str,
        status: str,
        request: dict,
        response_summary: dict,
        error_message: str = "",
        duration_ms: int = 0,
        risk_level: str = "low",
        policy_decision: str = "allow",
    ) -> None:
        await asyncio.to_thread(
            self._write_tool,
            run_id,
            tool_name,
            tool_category,
            status,
            request,
            response_summary,
            error_message,
            duration_ms,
            risk_level,
            policy_decision,
        )

    def _write_event(
        self,
        run_id: str,
        user_id: str,
        graph_node: str,
        event_type: str,
        message: str,
        payload: dict,
        severity: str,
        duration_ms: int,
        workflow_type: str,
    ) -> None:
        with self.database.session() as db:
            db.add(
                AgentEventLog(
                    event_log_id=f"log_{uuid4().hex}",
                    run_id=run_id,
                    user_id=user_id,
                    workflow_type=workflow_type,
                    graph_node=graph_node,
                    event_type=event_type,
                    severity=severity,
                    message=message,
                    payload=redact(payload),
                    duration_ms=duration_ms,
                )
            )

    def _write_llm_review(
        self,
        run_id: str,
        user_id: str,
        graph_node: str,
        user_intent: str,
        plan: dict,
        reasoning_summary: str,
        final_answer: str,
        workflow_type: str,
        model_deployment: str | None = None,
    ) -> None:
        with self.database.session() as db:
            db.add(
                LlmReviewLog(
                    review_id=f"rev_{uuid4().hex}",
                    run_id=run_id,
                    user_id=user_id,
                    workflow_type=workflow_type,
                    graph_node=graph_node,
                    model_deployment=model_deployment or self.settings.azure_deployment_name or "not_configured",
                    prompt_version=str(plan.get("prompt_version", "planner_v1")),
                    prompt_hash=str(plan.get("prompt_hash", "not_implemented")),
                    user_intent=user_intent,
                    plan_json=redact(plan),
                    reasoning_summary=redact(reasoning_summary),
                    tool_choice_json={},
                    final_answer=redact(final_answer),
                    redaction_count=0,
                    human_review_status="pending",
                )
            )

    def _write_tool(
        self,
        run_id: str,
        tool_name: str,
        tool_category: str,
        status: str,
        request: dict,
        response_summary: dict,
        error_message: str,
        duration_ms: int,
        risk_level: str = "low",
        policy_decision: str = "allow",
    ) -> None:
        with self.database.session() as db:
            db.add(
                ToolExecutionLog(
                    tool_log_id=f"tool_{uuid4().hex}",
                    run_id=run_id,
                    tool_name=tool_name,
                    tool_category=tool_category,
                    risk_level=risk_level,
                    policy_decision=policy_decision,
                    status=status,
                    request_json=redact(request),
                    response_summary=redact(response_summary),
                    error_message=redact(error_message),
                    duration_ms=duration_ms,
                )
            )