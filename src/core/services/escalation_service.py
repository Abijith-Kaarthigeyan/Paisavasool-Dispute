from uuid import UUID

from src.core.exceptions.business_exceptions import (
    EscalationException,
    ValidationException,
)
from src.core.services.audit_service import AuditService
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.escalation_repository import EscalationRepository
from src.data.repositories.sla_repository import SLARepository


class EscalationService:
    def __init__(
        self,
        escalation_repo: EscalationRepository,
        sla_repo: SLARepository,
        dispute_repo: DisputeRepository,
        audit_service: AuditService,
    ):
        self.escalation_repo = escalation_repo
        self.sla_repo = sla_repo
        self.dispute_repo = dispute_repo
        self.audit_service = audit_service

    async def check_and_trigger_escalations(self, dispute_id: UUID) -> None:
        """Evaluates dispute SLA progress and triggers L1 or L2 escalations as needed."""
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise ValidationException("Dispute not found.")

        sla = await self.sla_repo.get_by_dispute_id(dispute_id)
        if not sla:
            return  # No SLA created yet

        existing_escalations = await self.escalation_repo.get_escalations_for_dispute(
            dispute_id
        )
        existing_levels = {esc.level for esc in existing_escalations}

        # Level 1 Escalation: SLA status is AT_RISK or BREACHED (at >= 80%), assign L1 if not already triggered
        if (
            sla.status in ["AT_RISK", "BREACHED"] or sla.current_percentage >= 80.0
        ) and 1 not in existing_levels:
            # Escalates to associate
            escalated_to = dispute.assigned_to
            if not escalated_to:
                raise EscalationException(
                    "Cannot escalate L1: No associate assigned to dispute."
                )

            await self.escalation_repo.create_escalation(
                dispute_id=dispute_id,
                level=1,
                reason=f"SLA target at risk. Current progress: {sla.current_percentage}%",
                escalated_to=escalated_to,
            )

            await self.audit_service.log_event(
                dispute_id=dispute_id,
                action="ESCALATED",
                metadata={"level": 1, "escalated_to": str(escalated_to)},
            )

        # Level 2 Escalation: SLA status is BREACHED (at >= 100%), assign L2 if not already triggered
        if (
            sla.status == "BREACHED" or sla.current_percentage >= 100.0
        ) and 2 not in existing_levels:
            # Escalates to manager
            escalated_to = dispute.manager_id
            if not escalated_to:
                # Fallback to assignee if manager not set
                escalated_to = dispute.assigned_to

            if not escalated_to:
                raise EscalationException(
                    "Cannot escalate L2: No assignee/manager to escalate to."
                )

            await self.escalation_repo.create_escalation(
                dispute_id=dispute_id,
                level=2,
                reason=f"SLA breached. Current progress: {sla.current_percentage}%",
                escalated_to=escalated_to,
            )

            # Mark dispute as ESCALATED
            old_status = dispute.status
            dispute.status = "ESCALATED"
            await self.dispute_repo.update_dispute(dispute)

            await self.audit_service.log_event(
                dispute_id=dispute_id,
                action="STATUS_CHANGED",
                metadata={
                    "field": "status",
                    "old_value": old_status,
                    "new_value": "ESCALATED",
                },
            )

            await self.audit_service.log_event(
                dispute_id=dispute_id,
                action="ESCALATED",
                metadata={"level": 2, "escalated_to": str(escalated_to)},
            )

    async def resolve_escalations(self, dispute_id: UUID) -> None:
        """Marks all active escalations on a dispute as resolved (e.g. when dispute is resolved/closed)."""
        escalations = await self.escalation_repo.get_escalations_for_dispute(dispute_id)
        for esc in escalations:
            if not esc.resolved:
                esc.resolved = True
                await self.escalation_repo.update_escalation(esc)

        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="ESCALATIONS_RESOLVED",
        )
