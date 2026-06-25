from backend.app.tools.contracts import ToolExecutionRequest, ToolExecutionResult


def test_tool_execution_request_defaults() -> None:
    request = ToolExecutionRequest(
        run_id="run_1",
        step_id="step_1",
        tool_name="rest.get",
        user_id="usr_1",
    )
    assert request.workflow_type == "health_check_diagnostic"
    assert request.autonomy_mode == "observe_only"


def test_tool_execution_result_status() -> None:
    result = ToolExecutionResult(status="success", output={"ok": True})
    assert result.status == "success"
    assert result.output == {"ok": True}
