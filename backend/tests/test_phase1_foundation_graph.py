import asyncio

from backend.app.config import Settings
from backend.app.graphs.foundation import ReadOnlyDemoGraph


class FakeLlm:
    async def diagnostic_plan(self, *, goal: str, target_url: str, namespace: str | None) -> dict:
        return {
            "prompt_version": "test_v1",
            "prompt_hash": "abc123",
            "reasoning_summary": f"Plan {goal} for {target_url} in {namespace}",
            "steps": [
                {"title": "Read health endpoint", "tool": "rest.get", "risk": "low"},
                {"title": "Write final report", "tool": "validation", "risk": "low"},
            ],
        }


def test_read_only_demo_graph_executes_with_fallback_or_langgraph() -> None:
    graph = ReadOnlyDemoGraph(settings=Settings(langgraph_checkpointer="disabled"), llm=FakeLlm())

    result = asyncio.run(
        graph.ainvoke(
            {
                "run_id": "run_1",
                "user_id": "usr_1",
                "goal": "check service",
                "target_url": "http://localhost:8080/health",
                "namespace": "bosgenesis",
            }
        )
    )

    assert result["runtime"] == "sequential_fallback"
    assert result["scope"]["autonomy_mode"] == "observe_only"
    assert result["plan"]["steps"][0]["tool"] == "rest.get"
    assert "check service" in result["final_report"]