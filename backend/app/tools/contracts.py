from typing import Literal

from pydantic import BaseModel, Field


class ToolExecutionRequest(BaseModel):
    run_id: str
    step_id: str
    tool_name: str
    workflow_type: str = "health_check_diagnostic"
    environment: str = "local"
    namespace: str | None = None
    user_id: str
    arguments: dict = Field(default_factory=dict)
    autonomy_mode: str = "observe_only"


class ToolExecutionResult(BaseModel):
    status: Literal["success", "failed", "blocked", "approval_required"]
    output: dict | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    validation_result: dict | None = None
    error: dict | None = None
