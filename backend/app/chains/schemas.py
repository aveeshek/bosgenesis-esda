from typing import Literal

from pydantic import BaseModel, Field


WorkflowType = Literal[
    "release_note_creation",
    "health_check_diagnostic",
    "mop_creation",
    "mop_generation",
    "mop_execution",
    "env_agent",
    "helm_management",
    "k8s_management",
    "unknown",
]


class PromptedModel(BaseModel):
    prompt_version: str
    prompt_hash: str
    reasoning_summary: str = ""


class IntentClassification(PromptedModel):
    workflow_type: WorkflowType
    confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_questions: list[str] = Field(default_factory=list)
    target_url: str | None = None
    input_summary: str = ""


class AgentPlanStep(BaseModel):
    title: str
    tool: str
    risk: str = "low"
    status: str = "planned"
    rationale: str = ""


class AgentPlan(PromptedModel):
    workflow_type: WorkflowType
    steps: list[AgentPlanStep] = Field(default_factory=list)


class VerificationResult(PromptedModel):
    valid: bool
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    message: str
    missing_sections: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    policy_notes: list[str] = Field(default_factory=list)
    checks: dict = Field(default_factory=dict)


class RecoveryRecommendation(PromptedModel):
    action: Literal["continue", "retry", "ask_clarifying_question", "escalate", "fail"]
    retryable: bool = False
    recommendations: list[str] = Field(default_factory=list)
    escalation_required: bool = False


class ReportWriterResult(PromptedModel):
    markdown: str
    source_evidence_summary: str = ""
    limitations: list[str] = Field(default_factory=list)
