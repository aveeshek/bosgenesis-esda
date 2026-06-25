from dataclasses import dataclass
from typing import Literal


WorkflowType = Literal["health_check_diagnostic", "release_note_creation", "unknown"]


@dataclass(frozen=True)
class RouterDecision:
    workflow_type: WorkflowType
    confidence: float
    reason: str


class RouterGraph:
    """Phase 1 router skeleton.

    The production classifier is a later chain. V1 routes only the bounded health-check
    diagnostic workflow unless a dedicated endpoint selects a workflow explicitly.
    """

    async def classify(self, user_message: str) -> RouterDecision:
        normalized = user_message.lower()
        if "release note" in normalized or "release-note" in normalized:
            return RouterDecision(
                workflow_type="release_note_creation",
                confidence=0.6,
                reason="Dedicated release-note path is available, but chat routing is not enabled yet.",
            )
        if "health" in normalized or "diagnostic" in normalized or "check" in normalized:
            return RouterDecision(
                workflow_type="health_check_diagnostic",
                confidence=0.8,
                reason="Matched the Phase 1 bounded read-only diagnostic workflow.",
            )
        return RouterDecision(
            workflow_type="unknown",
            confidence=0.0,
            reason="No Phase 1 workflow match.",
        )