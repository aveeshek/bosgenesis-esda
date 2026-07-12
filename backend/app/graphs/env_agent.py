from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from backend.app.chains.env_agent import (
    EnvAgentDiagnosisChain,
    EnvAgentIntentClassifierChain,
    EnvAgentPlannerChain,
    EnvAgentRecoveryChain,
    EnvAgentRemediationPlannerChain,
    EnvAgentVerifierChain,
    pod_inventory_from_evidence,
)
from backend.app.config import Settings
from backend.app.llm.azure_gpt5 import AzureGpt5Service
from backend.app.tools.contracts import ToolExecutionRequest

WORKFLOW_TYPE = "env_agent"

ENV_AGENT_GRAPH_NODES = [
    "intake",
    "scope",
    "classify",
    "plan",
    "inspect",
    "correlate",
    "diagnose",
    "propose",
    "approve",
    "execute",
    "verify",
    "report",
    "complete",
]


class EnvAgentGraphState(TypedDict, total=False):
    run_id: str
    user_id: str
    user_text: str
    namespace: str | None
    mode: str
    model_profile: str | None
    user_roles: list[str]
    execute_tools: bool
    persist_safe_summaries: bool
    allowed_namespaces: list[str]
    nodes_completed: list[str]
    scope: dict[str, Any]
    classification: dict[str, Any]
    plan: dict[str, Any]
    evidence: list[dict[str, Any]]
    correlation: dict[str, Any]
    diagnosis: dict[str, Any]
    remediation: dict[str, Any]
    approval: dict[str, Any]
    execution: dict[str, Any]
    verification: dict[str, Any]
    recovery: dict[str, Any]
    safe_summaries: list[dict[str, Any]]
    final_report: str
    status: str
    runtime: str


@dataclass(frozen=True)
class EnvAgentRuntimeInput:
    run_id: str
    user_id: str
    user_text: str
    namespace: str | None
    mode: str = "diagnostic_only"
    model_profile: str | None = None
    user_roles: list[str] | None = None
    execute_tools: bool = False


class EnvAgentWorkflowGraph:
    def __init__(
        self,
        *,
        settings: Settings,
        llm: AzureGpt5Service,
        k8s_inspector=None,
        helm_manager=None,
        data_ingestion=None,
        observability=None,
        repository=None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.repository = repository
        self.intent_classifier = EnvAgentIntentClassifierChain(llm)
        self.planner = EnvAgentPlannerChain(llm)
        self.diagnoser = EnvAgentDiagnosisChain(llm)
        self.remediation_planner = EnvAgentRemediationPlannerChain(llm)
        self.verifier = EnvAgentVerifierChain(llm)
        self.recovery = EnvAgentRecoveryChain(llm)
        self.tool_adapters = {
            "env.k8s_inspector": k8s_inspector,
            "env.helm_manager": helm_manager,
            "env.data_ingestion": data_ingestion,
            "env.observability": observability,
        }
        self._compiled = self._compile_graph()

    async def run(self, env_input: EnvAgentRuntimeInput) -> EnvAgentGraphState:
        return await self.ainvoke(
            {
                "run_id": env_input.run_id,
                "user_id": env_input.user_id,
                "user_text": env_input.user_text,
                "namespace": env_input.namespace,
                "mode": env_input.mode,
                "model_profile": env_input.model_profile,
                "user_roles": env_input.user_roles or [],
                "execute_tools": env_input.execute_tools,
                "persist_safe_summaries": True,
            }
        )

    async def ainvoke(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        if self._compiled is None:
            return await self._fallback_ainvoke(state)
        config = {"configurable": {"thread_id": state.get("run_id") or "env_agent"}}
        result = await self._compiled.ainvoke(state, config=config)
        result["runtime"] = "langgraph"
        return result

    def _compile_graph(self):
        if self.settings.langgraph_checkpointer == "disabled":
            return None
        try:
            from langgraph.checkpoint.memory import MemorySaver
            from langgraph.graph import END, START, StateGraph
        except Exception:
            return None

        graph = StateGraph(EnvAgentGraphState)
        for node in ENV_AGENT_GRAPH_NODES:
            graph.add_node(node, getattr(self, f"_{node}"))
        graph.add_edge(START, "intake")
        for left, right in zip(ENV_AGENT_GRAPH_NODES, ENV_AGENT_GRAPH_NODES[1:], strict=False):
            graph.add_edge(left, right)
        graph.add_edge("complete", END)
        checkpointer = MemorySaver() if self.settings.langgraph_checkpointer == "memory" else None
        return graph.compile(checkpointer=checkpointer)

    async def _fallback_ainvoke(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        for node in ENV_AGENT_GRAPH_NODES:
            state = await getattr(self, f"_{node}")(state)
        state["runtime"] = "sequential_fallback"
        return state

    async def _intake(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        text = str(state.get("user_text") or "").strip()
        return self._mark(
            state | {"user_text": text, "mode": _normalize_mode(state.get("mode")), "safe_summaries": list(state.get("safe_summaries") or []), "evidence": list(state.get("evidence") or [])},
            "intake",
            "Request accepted for Environment Chat classification.",
        )

    async def _scope(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        allowed = state.get("allowed_namespaces") or self._allowed_namespaces()
        namespace = str(state.get("namespace") or "").strip()
        scope = {"workflow_type": WORKFLOW_TYPE, "namespace": namespace or None, "allowed_namespaces": allowed, "namespace_allowed": True, "configured_namespace_match": (not namespace or namespace in allowed), "autonomy_mode": state.get("mode") or "diagnostic_only", "mutation_enabled": False}
        return self._mark(state | {"scope": scope, "namespace": namespace or None}, "scope", "Prompt scope loaded; MCP and policy responses will decide allowed boundaries.")

    async def _classify(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        classification = await self.intent_classifier.run(user_text=state.get("user_text") or "", namespace=state.get("namespace"), mode=state.get("mode") or "diagnostic_only", model_profile=state.get("model_profile"))
        return self._mark(state | {"classification": classification.model_dump()}, "classify", classification.reasoning_summary)

    async def _plan(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        plan = await self.planner.run(user_text=state.get("user_text") or "", namespace=state.get("namespace"), classification=state.get("classification") or {}, model_profile=state.get("model_profile"))
        plan_payload = plan.model_dump()
        return self._mark(state | {"plan": plan_payload}, "plan", plan.reasoning_summary)

    async def _inspect(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        if not state.get("execute_tools"):
            return self._mark(state | {"evidence": list(state.get("evidence") or [])}, "inspect", "Inspection tool execution is deferred until the diagnostic runtime phase.")
        evidence = list(state.get("evidence") or [])
        for index, step in enumerate((state.get("plan") or {}).get("steps") or [], start=1):
            if step.get("status") == "blocked" or step.get("approval_required"):
                continue
            adapter_name = step.get("adapter")
            adapter = self.tool_adapters.get(adapter_name)
            if adapter is None or adapter_name == "workflow":
                continue
            request = ToolExecutionRequest(run_id=str(state.get("run_id") or "env_agent"), step_id=f"inspect_{index}", tool_name=str(adapter_name), workflow_type=WORKFLOW_TYPE, environment="kubernetes", namespace=state.get("namespace"), user_id=str(state.get("user_id") or "unknown"), arguments={"tool_name": step.get("tool_name"), "arguments": step.get("arguments") or {}}, autonomy_mode="observe_only")
            result, _ = await adapter.execute(request)
            if self.repository is not None and state.get("run_id"):
                self.repository.add_tool_call(
                    run_id=str(state["run_id"]),
                    tool_name=f"{adapter_name}.{step.get('tool_name') or 'unknown'}",
                    status=result.status,
                    request_json=request.model_dump(),
                    response_json=result.model_dump(),
                )
            output = result.output or {}
            record = (output.get("evidence") if output else None) or {"tool_name": adapter_name, "action": step.get("tool_name"), "status": result.status, "summary": (result.error or {}).get("message", "Tool call completed.")}
            if isinstance(record, dict) and "raw" not in record and "raw" in output:
                record = record | {"raw": output["raw"]}
            evidence.append(record)
        return self._mark(state | {"evidence": evidence}, "inspect", f"Inspection phase collected {len(evidence)} evidence record(s).")

    async def _correlate(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        evidence = list(state.get("evidence") or [])
        pod_inventory = pod_inventory_from_evidence([item for item in evidence if isinstance(item, dict)])
        correlation = {"evidence_count": len(evidence), "tools_used": sorted({str(item.get("tool_name") or item.get("source_agent") or "") for item in evidence if isinstance(item, dict)}), "failed_evidence_count": len([item for item in evidence if isinstance(item, dict) and item.get("status") in {"failed", "blocked"}])}
        if pod_inventory["pod_count"]:
            correlation["pod_inventory"] = pod_inventory
        return self._mark(state | {"correlation": correlation}, "correlate", "Evidence correlation summary prepared.")

    async def _diagnose(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        diagnosis = await self.diagnoser.run(user_text=state.get("user_text") or "", plan=state.get("plan") or {}, evidence=list(state.get("evidence") or []), model_profile=state.get("model_profile"))
        return self._mark(state | {"diagnosis": diagnosis.model_dump()}, "diagnose", diagnosis.reasoning_summary)

    async def _propose(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        remediation = await self.remediation_planner.run(user_text=state.get("user_text") or "", namespace=state.get("namespace"), classification=state.get("classification") or {}, diagnosis=state.get("diagnosis") or {}, model_profile=state.get("model_profile"))
        return self._mark(state | {"remediation": remediation.model_dump()}, "propose", remediation.reasoning_summary)

    async def _approve(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        remediation = state.get("remediation") or {}
        approval = {"required": bool(remediation.get("approval_required")), "state": "required" if remediation.get("approval_required") else "not_required", "phase": "phase_c_planning_only"}
        return self._mark(state | {"approval": approval}, "approve", "Approval state calculated; no approval is submitted in Phase C.")

    async def _execute(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        execution = {"executed": False, "state": "skipped", "reason": "Environment Chat diagnostic flow is read-only; remediation execution is handled in later approval-gated phases."}
        return self._mark(state | {"execution": execution}, "execute", execution["reason"])

    async def _verify(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        verification = await self.verifier.run(diagnosis=state.get("diagnosis") or {}, remediation=state.get("remediation") or {}, evidence=list(state.get("evidence") or []), model_profile=state.get("model_profile"))
        return self._mark(state | {"verification": verification.model_dump()}, "verify", verification.reasoning_summary)

    async def _report(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        recovery = await self.recovery.run(classification=state.get("classification") or {}, plan=state.get("plan") or {}, diagnosis=state.get("diagnosis") or {}, verification=state.get("verification") or {}, model_profile=state.get("model_profile"))
        diagnosis = state.get("diagnosis") or {}
        remediation = state.get("remediation") or {}
        verification = state.get("verification") or {}
        evidence = [item for item in list(state.get("evidence") or []) if isinstance(item, dict)]
        log_section = _pod_log_section(evidence)
        evidence_lines = [
            f"- `{item.get('action') or item.get('tool_name')}`: {item.get('summary') or 'Evidence collected.'} "
            f"Status `{item.get('status', 'unknown')}`, confidence `{item.get('confidence', 0)}`."
            for item in evidence[:10]
        ] or ["- No read-only MCP evidence was returned for this run."]
        pod_inventory = (state.get("correlation") or {}).get("pod_inventory") or pod_inventory_from_evidence(evidence)
        pod_section: list[str] = []
        if pod_inventory.get("pod_count"):
            pod_section = [
                "",
                "## Pod Inventory",
                f"- Total pods: `{pod_inventory['pod_count']}`",
                f"- Ready pods: `{pod_inventory['ready_count']}`",
                f"- Pods needing attention: `{pod_inventory['attention_count']}`",
                f"- Total observed restarts: `{pod_inventory['restart_total']}`",
            ]
            if pod_inventory.get("problem_pods"):
                pod_section.append("- Problem pods:")
                for pod in pod_inventory["problem_pods"][:8]:
                    pod_section.append(
                        f"  - `{pod.get('name')}`: ready `{pod.get('ready_detail')}`, "
                        f"status `{pod.get('status')}`, restarts `{pod.get('restarts')}`, "
                        f"issues `{', '.join(pod.get('issues') or [])}`"
                    )
        symptom_lines = [f"- {item}" for item in diagnosis.get("symptoms", [])] or ["- No symptoms were confirmed."]
        cause_lines = [f"- {item}" for item in diagnosis.get("likely_causes", [])] or ["- No likely cause was confirmed."]
        missing_lines = [f"- {item}" for item in diagnosis.get("missing_evidence", [])] or ["- None reported."]
        final_report = "\n".join(
            [
                f"# Environment Chat Report: {state.get('namespace') or 'unknown namespace'}",
                "",
                f"- Intent: `{(state.get('classification') or {}).get('intent_type', 'unknown')}`",
                f"- Diagnosis confidence: `{diagnosis.get('confidence', 0)}`",
                f"- Health status: `{verification.get('health_status', 'unknown')}`",
                f"- Remediation decision: `{remediation.get('decision', 'no_action')}`",
                f"- Recovery action: `{recovery.action}`",
                f"- Evidence records: `{len(evidence)}`",
                *log_section,
                *pod_section,
                "",
                "## Symptoms",
                *symptom_lines,
                "",
                "## Likely Causes",
                *cause_lines,
                "",
                "## Evidence",
                *evidence_lines,
                "",
                "## Missing Evidence",
                *missing_lines,
                "",
                "## Operator Guidance",
                str(diagnosis.get("summary") or verification.get("message") or "Environment Chat diagnostic completed."),
            ]
        )
        return self._mark(state | {"recovery": recovery.model_dump(), "final_report": final_report}, "report", recovery.reasoning_summary)

    async def _complete(self, state: EnvAgentGraphState) -> EnvAgentGraphState:
        classification = state.get("classification") or {}
        remediation = state.get("remediation") or {}
        verification = state.get("verification") or {}
        if classification.get("needs_clarification"):
            status = "needs_clarification"
        elif remediation.get("decision") == "blocked":
            status = "blocked"
        elif remediation.get("approval_required"):
            status = "proposal_ready"
        elif verification.get("valid"):
            status = "completed"
        else:
            status = "needs_review"
        return self._mark(state | {"status": status}, "complete", f"Environment Chat graph completed with status {status}.")

    def _mark(self, state: EnvAgentGraphState, node: str, summary: str) -> EnvAgentGraphState:
        completed = list(state.get("nodes_completed") or [])
        if not completed or completed[-1] != node:
            completed.append(node)
        summaries = list(state.get("safe_summaries") or [])
        summaries.append({"stage": node, "stage_label": node.replace("_", " ").title(), "reasoning_summary": summary, "persisted": True, "safe_for_audit": True})
        payload = {"stage": node, "stage_label": node.replace("_", " ").title(), "reasoning_summary": summary, "persisted": True, "safe_for_audit": True}
        if state.get("persist_safe_summaries") and self.repository is not None and state.get("run_id"):
            self.repository.add_event(str(state["run_id"]), "safe_reasoning_summary", f"Safe Environment Chat summary: {node}", payload)
        return state | {"nodes_completed": completed, "safe_summaries": summaries}

    def _allowed_namespaces(self) -> list[str]:
        return [item.strip() for item in self.settings.env_agent_allowed_namespaces.split(",") if item.strip()]


def _normalize_mode(value: Any) -> str:
    clean = str(value or "diagnostic_only").strip()
    return clean if clean in {"diagnostic_only", "propose_only", "approval_gated_remediation"} else "diagnostic_only"





def _pod_log_section(evidence: list[dict]) -> list[str]:
    sections: list[str] = []
    for item in evidence:
        if not isinstance(item, dict) or item.get("action") != "logs":
            continue
        pod_name = str(item.get("resource_name") or (item.get("request_redacted") or {}).get("pod_name") or "selected pod")
        status = str(item.get("status") or "unknown")
        log_text = _compact_log_text(item.get("observation_redacted") if "observation_redacted" in item else item.get("raw"))
        sections.extend(
            [
                "",
                "## Pod Logs",
                f"- Pod: `{pod_name}`",
                f"- Status: `{status}`",
                "",
                "```text",
                log_text or "No log lines were returned by the Kubernetes inspector MCP route.",
                "```",
            ]
        )
    return sections


def _compact_log_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        lines = [_compact_log_text(item) for item in value[:80]]
        return "\n".join(line for line in lines if line).strip()
    if isinstance(value, dict):
        for key in ("logs", "log", "text", "output", "message", "stdout", "stderr"):
            candidate = value.get(key)
            if candidate:
                return _compact_log_text(candidate)
        lines = []
        for key, candidate in value.items():
            if isinstance(candidate, (str, int, float, bool)):
                lines.append(f"{key}: {candidate}")
            elif isinstance(candidate, (dict, list)):
                nested = _compact_log_text(candidate)
                if nested:
                    lines.append(f"{key}: {nested}")
        return "\n".join(lines[:80]).strip()
    return str(value).strip()
