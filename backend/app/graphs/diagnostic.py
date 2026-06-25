from dataclasses import dataclass

from backend.app.approvals import ApprovalService
from backend.app.db.database import RunRepository
from backend.app.graphs.event_bus import RunEventBus
from backend.app.graphs.foundation import ReadOnlyDemoGraph
from backend.app.llm.azure_gpt5 import AzureGpt5Service
from backend.app.logging.postgres_logger import PostgresLogger
from backend.app.policy.evaluator import PolicyGuard
from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult
from backend.app.tools.mcp_client import K8sInspectorMcpTool
from backend.app.tools.powershell_get import PowerShellGetTemplateTool
from backend.app.tools.registry import ToolRegistry
from backend.app.tools.rest_get import RestGetTool


@dataclass
class DiagnosticInput:
    run_id: str
    user_id: str
    goal: str
    target_url: str
    namespace: str | None
    chat_session_id: str | None = None
    user_roles: list[str] | None = None


class DiagnosticGraph:
    def __init__(
        self,
        *,
        repository: RunRepository,
        event_bus: RunEventBus,
        logger: PostgresLogger,
        llm: AzureGpt5Service,
        rest_tool: RestGetTool,
        powershell_tool: PowerShellGetTemplateTool,
        mcp_tool: K8sInspectorMcpTool,
        tool_registry: ToolRegistry,
        policy_guard: PolicyGuard | None = None,
        approval_service: ApprovalService | None = None,
    ) -> None:
        self.repository = repository
        self.event_bus = event_bus
        self.logger = logger
        self.llm = llm
        self.foundation_graph = ReadOnlyDemoGraph(settings=llm.settings, llm=llm)
        self.rest_tool = rest_tool
        self.powershell_tool = powershell_tool
        self.mcp_tool = mcp_tool
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

    async def _node_started(self, diagnostic: DiagnosticInput, graph_node: str) -> None:
        await self._emit(
            diagnostic.run_id,
            "graph_node_started",
            f"Started graph node {graph_node}",
            {"graph_node": graph_node},
        )
        await self.logger.event(
            run_id=diagnostic.run_id,
            user_id=diagnostic.user_id,
            graph_node=graph_node,
            event_type="graph_node_started",
            message=f"Started graph node {graph_node}",
        )

    async def _node_completed(self, diagnostic: DiagnosticInput, graph_node: str, payload: dict) -> None:
        await self._emit(
            diagnostic.run_id,
            "graph_node_completed",
            f"Completed graph node {graph_node}",
            {"graph_node": graph_node, **payload},
        )
        await self.logger.event(
            run_id=diagnostic.run_id,
            user_id=diagnostic.user_id,
            graph_node=graph_node,
            event_type="graph_node_completed",
            message=f"Completed graph node {graph_node}",
            payload=payload,
        )

    async def _tool(
        self,
        diagnostic: DiagnosticInput,
        step_id: str,
        tool_name: str,
        tool,
        arguments: dict,
    ) -> ToolExecutionResult:
        request = ToolExecutionRequest(
            run_id=diagnostic.run_id,
            step_id=step_id,
            tool_name=tool_name,
            namespace=diagnostic.namespace,
            user_id=diagnostic.user_id,
            arguments=arguments,
        )
        await self._emit(
            diagnostic.run_id,
            "tool_call_started",
            f"Starting {tool_name}",
            {"tool_name": tool_name, "arguments": arguments},
        )
        policy_decision = self.policy_guard.evaluate_tool(
            request,
            user_roles=diagnostic.user_roles or [],
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
                requested_by_user_id=diagnostic.user_id,
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
            run_id=diagnostic.run_id,
            tool_name=tool_name,
            status=result.status,
            request_json=request.model_dump(),
            response_json=result.model_dump(),
        )
        await self.logger.tool(
            run_id=diagnostic.run_id,
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
            diagnostic.run_id,
            "tool_call_completed",
            f"Completed {tool_name} with status {result.status}",
            {"tool_name": tool_name, "result": result.model_dump()},
        )
        return result

    async def run(self, diagnostic: DiagnosticInput) -> None:
        self.repository.update_status(diagnostic.run_id, "running")
        await self._emit(diagnostic.run_id, "run_started", "Diagnostic run started", {})
        await self.logger.event(
            run_id=diagnostic.run_id,
            user_id=diagnostic.user_id,
            graph_node="start",
            event_type="run_started",
            message="Diagnostic run started",
        )

        await self._node_started(diagnostic, "langgraph_foundation")
        graph_state = await self.foundation_graph.ainvoke(
            {
                "run_id": diagnostic.run_id,
                "user_id": diagnostic.user_id,
                "goal": diagnostic.goal,
                "target_url": diagnostic.target_url,
                "namespace": diagnostic.namespace,
            }
        )
        plan = graph_state.get("plan") or {}
        self.repository.add_plan_steps(run_id=diagnostic.run_id, steps=list(plan.get("steps") or []))
        await self._node_completed(
            diagnostic,
            "langgraph_foundation",
            {
                "runtime": graph_state.get("runtime", "unknown"),
                "scope": graph_state.get("scope", {}),
                "step_count": len(plan.get("steps") or []),
            },
        )

        await self.logger.llm_review(
            run_id=diagnostic.run_id,
            user_id=diagnostic.user_id,
            graph_node="plan",
            user_intent=diagnostic.goal,
            plan=plan,
            reasoning_summary=str(plan.get("reasoning_summary", "")),
        )
        await self._emit(diagnostic.run_id, "plan_created", "Diagnostic plan created", plan)

        await self._node_started(diagnostic, "execute_tools")
        rest_result = await self._tool(
            diagnostic,
            "step_rest_get",
            "rest.get",
            self.rest_tool,
            {"url": diagnostic.target_url},
        )
        ps_result = await self._tool(
            diagnostic,
            "step_ps_get",
            "powershell.ps_http_get",
            self.powershell_tool,
            {"url": diagnostic.target_url},
        )
        mcp_result = await self._tool(
            diagnostic,
            "step_mcp_k8s",
            "mcp.k8s_inspector",
            {
                "tool_name": "list_pods",
                "arguments": {"namespace": diagnostic.namespace or "bosgenesis"},
            },
        )
        await self._node_completed(
            diagnostic,
            "execute_tools",
            {
                "rest_status": rest_result.status,
                "powershell_status": ps_result.status,
                "mcp_status": mcp_result.status,
            },
        )

        validations = [
            rest_result.validation_result or {"valid": False, "message": "REST validation missing"},
            ps_result.validation_result or {"valid": False, "message": "PowerShell validation missing"},
        ]
        valid = all(item.get("valid") for item in validations)
        final_status = "completed" if valid else "failed"
        final_report = self._report(diagnostic, plan, rest_result, ps_result, mcp_result, valid)
        self.repository.update_status(diagnostic.run_id, final_status, final_report)
        if diagnostic.chat_session_id:
            self.repository.add_chat_message(
                session_id=diagnostic.chat_session_id,
                run_id=diagnostic.run_id,
                role="assistant",
                content=final_report,
                payload={"status": final_status},
            )
        await self._emit(
            diagnostic.run_id,
            "run_completed" if valid else "run_failed",
            f"Diagnostic run {final_status}",
            {"final_report": final_report, "valid": valid},
        )
        await self.logger.event(
            run_id=diagnostic.run_id,
            user_id=diagnostic.user_id,
            graph_node="final_report",
            event_type="run_completed" if valid else "run_failed",
            message=f"Diagnostic run {final_status}",
            payload={"valid": valid},
        )

    def _report(
        self,
        diagnostic: DiagnosticInput,
        plan: dict,
        rest_result: ToolExecutionResult,
        ps_result: ToolExecutionResult,
        mcp_result: ToolExecutionResult,
        valid: bool,
    ) -> str:
        return "\n".join(
            [
                "# Health Check Diagnostic Report",
                "",
                f"Goal: {diagnostic.goal}",
                f"Target URL: {diagnostic.target_url}",
                f"Namespace: {diagnostic.namespace or 'not provided'}",
                f"Final Status: {'Healthy/readable' if valid else 'Needs review'}",
                "",
                "## Plan",
                str(plan.get("reasoning_summary", "")),
                "",
                "## Evidence",
                f"- REST GET: {rest_result.status}",
                f"- PowerShell GET template: {ps_result.status}",
                f"- Kubernetes MCP inspection: {mcp_result.status}",
                "",
                "## Validation",
                f"- REST: {(rest_result.validation_result or {}).get('message', 'missing')}",
                f"- PowerShell: {(ps_result.validation_result or {}).get('message', 'missing')}",
            ]
        )