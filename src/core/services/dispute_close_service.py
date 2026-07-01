from datetime import UTC, datetime
from uuid import UUID

from src.core.config.settings import settings
from src.core.exceptions.business_exceptions import (
    DisputeNotFoundException,
    ValidationException,
)
from src.core.services.audit_service import AuditService
from src.core.services.escalation_service import EscalationService
from src.core.services.sla_service import SLAService
from src.core.services.workflow_context_service import WorkflowContextService
from src.data.clients.ar_service_client import ARServiceClient
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import CommentRepository
from src.data.repositories.sla_repository import SLARepository
from src.data.repositories.workflow_context_repository import WorkflowContextRepository
from src.observability.logging.logger import logger

TERMINAL_REJECT_STATUSES = frozenset({"FAILED", "CANCELLED"})
ALLOWED_MANUAL_OUTCOMES = frozenset({"CUSTOMER_CORRECT", "COMPANY_CORRECT"})
ALLOWED_RESOLUTION_METHODS = frozenset({"PHONE", "IN_PERSON", "EMAIL", "OTHER"})

RESOLUTION_METHOD_LABELS = {
    "PHONE": "phone call",
    "IN_PERSON": "in-person discussion",
    "EMAIL": "email conversation",
    "OTHER": "mutual agreement outside paisavasool",
}

OUTCOME_LABELS = {
    "CUSTOMER_CORRECT": "Customer correct",
    "COMPANY_CORRECT": "Company correct",
}


def canonical_resolution_outcome(outcome: str | None) -> str | None:
    """Maps workflow/decision outcome strings to canonical DB outcomes."""
    if outcome in ["APPROVE", "SETTLEMENT_DONE", "ACKNOWLEDGED", "CUSTOMER_CORRECT"]:
        return "CUSTOMER_CORRECT"
    if outcome in ["REJECT", "SETTLEMENT_NOT_DONE", "REJECTED", "COMPANY_CORRECT"]:
        return "COMPANY_CORRECT"
    return outcome


class DisputeCloseService:
    def __init__(
        self,
        dispute_repo: DisputeRepository,
        sla_repo: SLARepository,
        audit_service: AuditService,
        ar_client: ARServiceClient,
        escalation_service: EscalationService,
        workflow_context_service: WorkflowContextService,
        workflow_context_repo: WorkflowContextRepository,
        comment_repo: CommentRepository,
    ):
        self.dispute_repo = dispute_repo
        self.sla_repo = sla_repo
        self.audit_service = audit_service
        self.ar_client = ar_client
        self.escalation_service = escalation_service
        self.workflow_context_service = workflow_context_service
        self.workflow_context_repo = workflow_context_repo
        self.comment_repo = comment_repo

    async def execute_dispute_close(
        self,
        dispute,
        outcome: str | None,
        *,
        performed_by: UUID | None = None,
        close_reason: str | None = None,
        resolution_method: str | None = None,
    ) -> str | None:
        """Applies close side effects: status, SLA, AR resume, escalations, audit."""
        canonical_outcome = canonical_resolution_outcome(outcome)

        dispute.resolution_outcome = canonical_outcome
        old_status = dispute.status
        dispute.status = "CLOSED"

        now = datetime.now(UTC)
        dispute.resolved_at = now
        dispute.closed_at = now
        await self.dispute_repo.update_dispute(dispute)

        sla_service = SLAService(
            self.sla_repo, self.dispute_repo, self.audit_service, settings
        )
        await sla_service.handle_status_change(dispute.id, old_status, "CLOSED")
        await sla_service.calculate_progress(dispute.id)

        try:
            await self.ar_client.resume_collections(dispute.invoice_id)
        except Exception as e:
            logger.error("Failed to resume collections in AR service: %s", str(e))

        await self.escalation_service.resolve_escalations(dispute.id)

        status_metadata: dict = {
            "old_status": old_status,
            "new_status": "CLOSED",
            "outcome": canonical_outcome,
        }
        closed_metadata: dict = {"outcome": canonical_outcome}
        if close_reason:
            status_metadata["close_reason"] = close_reason
            closed_metadata["close_reason"] = close_reason
        if resolution_method:
            status_metadata["resolution_method"] = resolution_method
            closed_metadata["resolution_method"] = resolution_method

        await self.audit_service.log_event(
            dispute_id=dispute.id,
            action="STATUS_CHANGED",
            performed_by=performed_by,
            metadata=status_metadata,
        )
        await self.audit_service.log_event(
            dispute_id=dispute.id,
            action="CLOSED",
            performed_by=performed_by,
            metadata=closed_metadata,
        )

        return canonical_outcome

    async def manual_close(
        self,
        dispute_id: UUID,
        *,
        resolution_method: str,
        resolution_outcome: str,
        comments: str,
        performed_by: UUID,
    ):
        """Closes a dispute manually without resuming the workflow graph."""
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise DisputeNotFoundException(f"Dispute {dispute_id} not found.")

        if dispute.status == "CLOSED":
            return dispute

        if dispute.status in TERMINAL_REJECT_STATUSES:
            raise ValidationException(
                f"Cannot close dispute in status {dispute.status}."
            )

        normalized_method = resolution_method.upper()
        if normalized_method not in ALLOWED_RESOLUTION_METHODS:
            raise ValidationException(
                "resolution_method must be PHONE, IN_PERSON, EMAIL, or OTHER."
            )

        normalized_outcome = resolution_outcome.upper()
        if normalized_outcome not in ALLOWED_MANUAL_OUTCOMES:
            raise ValidationException(
                "resolution_outcome must be CUSTOMER_CORRECT or COMPANY_CORRECT."
            )

        method_label = RESOLUTION_METHOD_LABELS[normalized_method]
        outcome_label = OUTCOME_LABELS[normalized_outcome]
        await self.comment_repo.create_comment(
            dispute_id=dispute_id,
            comment=(f"Manual close via {method_label} — {outcome_label}: {comments}"),
            comment_type="INTERNAL",
            created_by=performed_by,
        )
        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="MANUAL_CLOSE",
            performed_by=performed_by,
            metadata={
                "resolution_method": normalized_method,
                "outcome": normalized_outcome,
                "comments": comments,
            },
        )

        canonical_outcome = await self.execute_dispute_close(
            dispute,
            normalized_outcome,
            performed_by=performed_by,
            close_reason="MANUAL",
            resolution_method=normalized_method,
        )

        context = await self.workflow_context_repo.get_by_dispute_id(dispute_id)
        if context:
            workflow_state = {
                **(context.workflow_state or {}),
                "workflow_status": "CLOSED",
                "manual_close": True,
                "resolution_method": normalized_method,
                "resolution_outcome": canonical_outcome,
            }
            await self.workflow_context_service.update_node(
                dispute_id,
                "close_dispute_node",
                workflow_state,
            )

        await self.dispute_repo.db.commit()
        return dispute
