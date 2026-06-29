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

    async def escalate_to_manager_manually(
        self, dispute_id: UUID, reason: str | None = None
    ) -> None:
        """Manually escalates a dispute to the manager, marking it as ESCALATED."""
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise ValidationException("Dispute not found.")

        existing_escalations = await self.escalation_repo.get_escalations_for_dispute(
            dispute_id
        )
        existing_levels = {esc.level for esc in existing_escalations}

        if 2 in existing_levels:
            raise ValidationException("Dispute is already escalated to the manager.")

        escalated_to = dispute.manager_id
        if not escalated_to and dispute.assigned_to:
            from src.data.repositories.user_repository import UserRepository

            user_repo = UserRepository(self.dispute_repo.db)
            manager = await user_repo.get_manager_for_associate(dispute.assigned_to)
            if manager:
                escalated_to = manager.id

        if not escalated_to:
            from sqlalchemy import select

            from src.data.models.postgres.user_mapping import RoleMapping, UserMapping
            from src.data.repositories.user_repository import UserRepository

            user_repo = UserRepository(self.dispute_repo.db)
            result = await user_repo.db.execute(
                select(UserMapping)
                .join(RoleMapping, UserMapping.role_id == RoleMapping.id)
                .where(
                    UserMapping.is_active.is_(True),
                    RoleMapping.role_name == "FINANCE_MANAGER",
                )
            )
            managers = result.scalars().all()
            if managers:
                escalated_to = managers[0].id
            else:
                raise EscalationException(
                    "Cannot escalate to manager: No finance manager found in the system."
                )

        await self.escalation_repo.create_escalation(
            dispute_id=dispute_id,
            level=2,
            reason=reason or "Manual escalation by associate.",
            escalated_to=escalated_to,
        )

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
            metadata={
                "level": 2,
                "escalated_to": str(escalated_to),
                "reason": reason or "Manual escalation by associate.",
                "manual": True,
            },
        )

        from src.data.repositories.other_repositories import CommentRepository

        comment_repo = CommentRepository(self.dispute_repo.db)
        await comment_repo.create_comment(
            dispute_id=dispute_id,
            comment=f"Dispute escalated to manager. Reason: {reason or 'No reason provided.'}",
            comment_type="INTERNAL",
            created_by=dispute.assigned_to or escalated_to,
        )

        await self.dispute_repo.db.commit()
