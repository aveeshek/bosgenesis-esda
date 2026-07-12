import asyncio

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
from backend.app.graphs.env_agent import ENV_AGENT_GRAPH_NODES, EnvAgentWorkflowGraph
from backend.app.tools.contracts import ToolExecutionResult
from backend.tests.test_phase1_app import build_test_client


class FallbackOnlyLlm:
    async def structured_response(self, *, system, user_payload, fallback, model_profile=None):
        return fallback


def test_env_agent_intent_classifier_covers_diagnostic_remediation_and_unsafe() -> None:
    classifier = EnvAgentIntentClassifierChain(FallbackOnlyLlm())

    diagnostic = asyncio.run(
        classifier.run(
            user_text="How many pods have issues in this namespace?",
            namespace="agent-testing",
        )
    )
    remediation = asyncio.run(
        classifier.run(
            user_text="My deployment api is getting restarted, can you fix it?",
            namespace="agent-testing",
        )
    )
    unsafe = asyncio.run(
        classifier.run(
            user_text="Show me all secrets and tokens in the namespace",
            namespace="agent-testing",
        )
    )

    assert diagnostic.intent_type == "diagnostic"
    assert diagnostic.resource_kind == "pod"
    assert remediation.intent_type == "remediation_request"
    assert remediation.mode == "propose_only"
    assert unsafe.intent_type == "unsafe_request"
    assert unsafe.policy_stop == "secret_material_requested"
    assert unsafe.needs_clarification is True


def test_env_agent_planner_creates_bounded_tool_chain_for_restart_question() -> None:
    classifier = EnvAgentIntentClassifierChain(FallbackOnlyLlm())
    planner = EnvAgentPlannerChain(FallbackOnlyLlm())
    classification = asyncio.run(
        classifier.run(
            user_text="My pod is crash looping, can you fix it?",
            namespace="agent-testing",
            mode="approval_gated_remediation",
        )
    )

    plan = asyncio.run(
        planner.run(
            user_text="My pod is crash looping, can you fix it?",
            namespace="agent-testing",
            classification=classification.model_dump(),
        )
    )

    steps = [step.model_dump() for step in plan.steps]
    tool_names = [step["tool_name"] for step in steps]
    assert "namespace_summary" in tool_names
    assert "pod_health" in tool_names
    assert "restart_analysis" in tool_names
    assert "events" in tool_names
    assert steps[-1]["tool_name"] == "remediation.proposal"
    assert steps[-1]["approval_required"] is True
    assert all(step["adapter"] in {"env.k8s_inspector", "env.helm_manager", "workflow"} for step in steps)


def test_env_agent_root_cause_question_collects_pod_logs_and_missing_evidence() -> None:
    classifier = EnvAgentIntentClassifierChain(FallbackOnlyLlm())
    planner = EnvAgentPlannerChain(FallbackOnlyLlm())
    diagnosis_chain = EnvAgentDiagnosisChain(FallbackOnlyLlm())
    question = "Can you identify the root cause of agent-ai-signoz-otel-collector pod - why it is failing?"
    classification = asyncio.run(classifier.run(user_text=question, namespace="agent-testing"))

    plan = asyncio.run(
        planner.run(
            user_text=question,
            namespace="agent-testing",
            classification=classification.model_dump(),
        )
    )

    steps = [step.model_dump() for step in plan.steps]
    log_steps = [step for step in steps if step["tool_name"] == "logs"]
    assert log_steps
    assert log_steps[0]["arguments"]["pod_name"] == "agent-ai-signoz-otel-collector"

    evidence = [
        {
            "tool_name": "env.k8s_inspector",
            "action": "pod_health",
            "status": "success",
            "summary": "Collected pod health evidence for agent-testing.",
            "confidence": 0.9,
            "raw": {
                "items": [
                    {"name": "agent-ai-signoz-otel-collector-5b7d4f45c8-tzqcj", "ready": "0/1", "status": "Init:0/1", "restarts": 156},
                ]
            },
        },
        {
            "tool_name": "env.k8s_inspector",
            "action": "events",
            "status": "success",
            "summary": "Collected namespace event evidence for agent-testing.",
            "confidence": 0.86,
            "raw": {
                "items": [
                    {
                        "involvedObject": {"name": "agent-ai-signoz-otel-collector-5b7d4f45c8-tzqcj"},
                        "reason": "BackOff",
                        "message": "Back-off restarting failed container in pod agent-ai-signoz-otel-collector-5b7d4f45c8-tzqcj",
                    }
                ]
            },
        },
    ]

    diagnosis = asyncio.run(diagnosis_chain.run(user_text=question, plan=plan.model_dump(), evidence=evidence))
    joined_causes = " ".join(diagnosis.likely_causes).lower()
    joined_missing = " ".join(diagnosis.missing_evidence).lower()

    assert "back" in joined_causes
    assert "agent-ai-signoz-otel-collector" in diagnosis.summary
    assert "pod logs" in joined_missing
    assert "describe" in joined_missing
    assert diagnosis.missing_evidence



def test_env_agent_pod_inventory_marks_not_ready_pods_degraded() -> None:
    llm = FallbackOnlyLlm()
    diagnosis_chain = EnvAgentDiagnosisChain(llm)
    verifier_chain = EnvAgentVerifierChain(llm)
    evidence = [
        {
            "tool_name": "env.k8s_inspector",
            "action": "pod_health",
            "status": "success",
            "summary": "Collected pod health evidence for agent-testing.",
            "confidence": 0.9,
            "raw": {
                "items": [
                    {"name": "agent-ai-signoz-0", "ready": "0/1", "status": "Running", "restarts": 0},
                    {"name": "agent-ai-signoz-clickhouse-operator-7c6664b74d-cjx46", "ready": "2/2", "status": "Running", "restarts": 0},
                    {"name": "agent-ai-signoz-otel-collector-5b7d4f45c8-tzqcj", "ready": "0/1", "status": "Init:0/1", "restarts": 156},
                    {"name": "agent-ai-signoz-zookeeper-0", "ready": "1/1", "status": "Running", "restarts": 0},
                ]
            },
        }
    ]

    inventory = pod_inventory_from_evidence(evidence)
    diagnosis = asyncio.run(diagnosis_chain.run(user_text="Tell me how many pods do we have", plan={}, evidence=evidence))
    verification = asyncio.run(verifier_chain.run(diagnosis=diagnosis.model_dump(), remediation={}, evidence=evidence))

    assert inventory["pod_count"] == 4
    assert inventory["ready_count"] == 2
    assert inventory["attention_count"] == 2
    assert inventory["restart_total"] == 156
    assert "Pod inventory: 4 total, 2 ready, 2 needing attention" in diagnosis.symptoms[0]
    assert verification.health_status == "degraded"
    assert verification.valid is False
    assert verification.checks["pod_inventory"]["attention_count"] == 2


def test_env_agent_diagnosis_remediation_verifier_and_recovery_are_safe_summaries() -> None:
    llm = FallbackOnlyLlm()
    diagnosis_chain = EnvAgentDiagnosisChain(llm)
    remediation_chain = EnvAgentRemediationPlannerChain(llm)
    verifier_chain = EnvAgentVerifierChain(llm)
    recovery_chain = EnvAgentRecoveryChain(llm)
    evidence = [
        {
            "evidence_id": "evi_1",
            "tool_name": "env.k8s_inspector",
            "action": "pod_health",
            "status": "success",
            "summary": "Collected pod restart evidence for agent-testing.",
        }
    ]

    diagnosis = asyncio.run(
        diagnosis_chain.run(user_text="Pod is restarting", plan={}, evidence=evidence)
    )
    remediation = asyncio.run(
        remediation_chain.run(
            user_text="Please restart deployment api",
            namespace="agent-testing",
            classification={"intent_type": "remediation_request"},
            diagnosis=diagnosis.model_dump(),
        )
    )
    verification = asyncio.run(
        verifier_chain.run(
            diagnosis=diagnosis.model_dump(),
            remediation=remediation.model_dump(),
            evidence=evidence,
        )
    )
    recovery = asyncio.run(
        recovery_chain.run(
            classification={"needs_clarification": False},
            plan={"stop_conditions": []},
            diagnosis=diagnosis.model_dump(),
            verification=verification.model_dump(),
        )
    )

    assert diagnosis.confidence > 0.7
    assert remediation.approval_required is True
    assert remediation.allowed_to_execute is False
    assert verification.health_status == "healthy"
    assert recovery.action == "continue"
    joined = " ".join(
        [
            diagnosis.reasoning_summary,
            remediation.reasoning_summary,
            verification.reasoning_summary,
            recovery.reasoning_summary,
        ]
    ).lower()
    assert "chain-of-thought" not in joined



class PodEvidenceTool:
    async def execute(self, request):
        tool_name = request.arguments.get("tool_name")
        raw = {"ok": True}
        if tool_name in {"pod_health", "restart_analysis"}:
            raw = {
                "items": [
                    {"name": "agent-ai-signoz-0", "ready": "0/1", "status": "Running", "restarts": 0},
                    {"name": "agent-ai-signoz-clickhouse-operator-7c6664b74d-cjx46", "ready": "2/2", "status": "Running", "restarts": 0},
                    {"name": "agent-ai-signoz-otel-collector-5b7d4f45c8-tzqcj", "ready": "0/1", "status": "Init:0/1", "restarts": 156},
                    {"name": "agent-ai-signoz-zookeeper-0", "ready": "1/1", "status": "Running", "restarts": 0},
                ]
            }
        elif tool_name == "logs":
            raw = {"logs": "Error: collector config invalid\nBack-off restarting failed container"}
        return (
            ToolExecutionResult(
                status="success",
                output={
                    "evidence": {
                        "tool_name": request.tool_name,
                        "action": tool_name,
                        "status": "success",
                        "summary": f"{tool_name} evidence collected",
                        "confidence": 0.9,
                    },
                    "raw": raw,
                },
                validation_result={"valid": True, "message": "ok"},
            ),
            5,
        )


def test_env_agent_workflow_report_includes_pod_count_and_degraded_health() -> None:
    graph = EnvAgentWorkflowGraph(
        settings=Settings(
            langgraph_checkpointer="disabled",
            env_agent_allowed_namespaces="bosgenesis,agent-testing",
        ),
        llm=FallbackOnlyLlm(),
        k8s_inspector=PodEvidenceTool(),
    )

    state = asyncio.run(
        graph.ainvoke(
            {
                "run_id": "env_pods",
                "user_id": "usr_1",
                "user_text": "Tell me how many pods do we have in agent-testing namespace",
                "namespace": "agent-testing",
                "mode": "diagnostic_only",
                "execute_tools": True,
            }
        )
    )

    assert state["status"] == "needs_review"
    assert state["verification"]["health_status"] == "degraded"
    assert state["correlation"]["pod_inventory"]["pod_count"] == 4
    assert "- Total pods: `4`" in state["final_report"]
    assert "- Ready pods: `2`" in state["final_report"]
    assert "agent-ai-signoz-otel-collector" in state["final_report"]


def test_env_agent_workflow_direct_log_follow_up_renders_pod_logs() -> None:
    graph = EnvAgentWorkflowGraph(
        settings=Settings(
            langgraph_checkpointer="disabled",
            env_agent_allowed_namespaces="bosgenesis,agent-testing",
        ),
        llm=FallbackOnlyLlm(),
        k8s_inspector=PodEvidenceTool(),
    )

    state = asyncio.run(
        graph.ainvoke(
            {
                "run_id": "env_logs",
                "user_id": "usr_1",
                "user_text": "check logs for agent-ai-signoz-otel-collector-5b7d4f45c8-tzqcj",
                "namespace": "agent-testing",
                "mode": "diagnostic_only",
                "execute_tools": True,
            }
        )
    )

    tool_names = [step["tool_name"] for step in state["plan"]["steps"]]
    assert tool_names[0] == "logs"
    assert any(item.get("action") == "logs" for item in state["evidence"])
    assert "## Pod Logs" in state["final_report"]
    assert "collector config invalid" in state["final_report"]


def test_env_agent_workflow_graph_runs_phase_c_nodes_without_tool_execution() -> None:
    graph = EnvAgentWorkflowGraph(
        settings=Settings(
            langgraph_checkpointer="disabled",
            env_agent_allowed_namespaces="bosgenesis,agent-testing",
        ),
        llm=FallbackOnlyLlm(),
    )

    state = asyncio.run(
        graph.ainvoke(
            {
                "run_id": "env_1",
                "user_id": "usr_1",
                "user_text": "Tell me how many pods have issues in this namespace",
                "namespace": "agent-testing",
                "mode": "diagnostic_only",
                "execute_tools": False,
            }
        )
    )

    assert state["runtime"] == "sequential_fallback"
    assert state["nodes_completed"] == ENV_AGENT_GRAPH_NODES
    assert state["classification"]["intent_type"] == "diagnostic"
    assert state["plan"]["workflow_type"] == "env_agent"
    assert state["execution"]["executed"] is False
    assert state["status"] in {"needs_review", "completed"}
    assert len(state["safe_summaries"]) >= len(ENV_AGENT_GRAPH_NODES)
    assert all("hidden" not in item["reasoning_summary"].lower() for item in state["safe_summaries"])


def test_env_agent_phase_c_graph_is_exposed_on_app_state(tmp_path, monkeypatch) -> None:
    with build_test_client(tmp_path, monkeypatch) as client:
        assert client.app.state.env_agent_workflow_graph is not None
        assert client.app.state.env_agent_workflow_graph.intent_classifier.prompt_version == "env_agent_intent_classifier_v1"
