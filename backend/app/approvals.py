from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.config import Settings
from backend.app.db.database import RunRepository
from backend.app.policy.evaluator import PolicyDecision, PolicyGuard
from backend.app.tools.contracts import ToolExecutionRequest


class ApprovalService:
    def __init__(
        self,
        *,
        repository: RunRepository,
        settings: Settings,
        policy_guard: PolicyGuard,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.policy_guard = policy_guard

    def create_request(
        self,
        *,
        request: ToolExecutionRequest,
        requested_by_user_id: str,
        decision: PolicyDecision,
    ) -> dict:
        expires_at = datetime.now(UTC) + timedelta(minutes=self.settings.approval_expiration_minutes)
        run_id = request.run_id if request.run_id.startswith("run_") else None
        return self.repository.create_approval_request(
            approval_id=f"appr_{uuid4().hex}",
            run_id=run_id,
            requested_by_user_id=requested_by_user_id,
            workflow_type=request.workflow_type,
            tool_name=request.tool_name,
            environment=request.environment,
            namespace=request.namespace,
            risk_level=decision.risk_level,
            request_json=request.model_dump(),
            policy_decision=decision.to_dict(),
            expected_impact=decision.expected_impact,
            rollback_note=decision.rollback_note,
            expires_at=expires_at,
        )

    def list_requests(self, *, status: str | None = None) -> list[dict]:
        approvals = self.repository.list_approvals(status=status)
        return [self._expire_if_needed(approval) for approval in approvals]

    def get_request(self, approval_id: str) -> dict | None:
        approval = self.repository.get_approval(approval_id)
        if not approval:
            return None
        return self._expire_if_needed(approval)

    def approve(
        self,
        approval_id: str,
        *,
        approver_user_id: str,
        approver_roles: list[str],
        notes: str = "",
    ) -> dict | None:
        approval = self.get_request(approval_id)
        if not approval or approval["status"] != "pending":
            return approval
        request = ToolExecutionRequest(**approval["request"])
        decision = self.policy_guard.evaluate_tool(request, user_roles=approver_roles)
        if decision.decision == "deny":
            return self.repository.update_approval(
                approval_id,
                status="rejected",
                reviewed_by_user_id=approver_user_id,
                review_notes=notes or "Rejected because policy recheck denied the action.",
                policy_decision=decision.to_dict(),
                risk_level=decision.risk_level,
                expected_impact=decision.expected_impact,
                rollback_note=decision.rollback_note,
            )
        return self.repository.update_approval(
            approval_id,
            status="approved",
            reviewed_by_user_id=approver_user_id,
            review_notes=notes,
            policy_decision=decision.to_dict(),
            risk_level=decision.risk_level,
            expected_impact=decision.expected_impact,
            rollback_note=decision.rollback_note,
        )

    def reject(
        self,
        approval_id: str,
        *,
        reviewer_user_id: str,
        notes: str = "",
    ) -> dict | None:
        approval = self.get_request(approval_id)
        if not approval or approval["status"] != "pending":
            return approval
        return self.repository.update_approval(
            approval_id,
            status="rejected",
            reviewed_by_user_id=reviewer_user_id,
            review_notes=notes,
        )

    def modify_and_recheck(
        self,
        approval_id: str,
        *,
        modified_request: ToolExecutionRequest,
        reviewer_user_id: str,
        reviewer_roles: list[str],
        notes: str = "",
    ) -> dict | None:
        approval = self.get_request(approval_id)
        if not approval or approval["status"] != "pending":
            return approval
        decision = self.policy_guard.evaluate_tool(modified_request, user_roles=reviewer_roles)
        status = "rejected" if decision.decision == "deny" else "pending"
        review_notes = notes
        if decision.decision == "deny" and not review_notes:
            review_notes = "Modified request was denied by policy recheck."
        return self.repository.update_approval(
            approval_id,
            status=status,
            reviewed_by_user_id=reviewer_user_id if status == "rejected" else None,
            review_notes=review_notes,
            request_json=modified_request.model_dump(),
            policy_decision=decision.to_dict(),
            risk_level=decision.risk_level,
            expected_impact=decision.expected_impact,
            rollback_note=decision.rollback_note,
        )

    def _expire_if_needed(self, approval: dict) -> dict:
        if approval["status"] != "pending":
            return approval
        expires_at = datetime.fromisoformat(approval["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at > datetime.now(UTC):
            return approval
        expired = self.repository.update_approval(approval["approval_id"], status="expired")
        return expired or approval
