import random
from uuid import UUID

from src.core.exceptions.business_exceptions import (
    AssignmentException,
    ValidationException,
)
from src.core.services.audit_service import AuditService
from src.data.models.postgres.dispute import Dispute
from src.data.repositories.assignment_repository import AssignmentRepository
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.user_repository import UserRepository


class AssignmentService:
    def __init__(
        self,
        dispute_repo: DisputeRepository,
        assignment_repo: AssignmentRepository,
        user_repo: UserRepository,
        audit_service: AuditService,
    ):
        self.dispute_repo = dispute_repo
        self.assignment_repo = assignment_repo
        self.user_repo = user_repo
        self.audit_service = audit_service

    async def calculate_workload(self, associate_id: UUID) -> int:
        """Count active disputes (status not in RESOLVED, CLOSED, FAILED) assigned to an associate."""
        return await self.dispute_repo.count_active_disputes_for_associate(associate_id)

    async def assign_dispute(
        self, dispute_id: UUID, performed_by: UUID | None = None
    ) -> Dispute:
        """Automatically assigns a dispute to the active associate with the lowest workload."""
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise ValidationException("Dispute not found.")

        # 1. Fetch all active associates
        associates = await self.user_repo.get_active_finance_associates()
        if not associates:
            raise AssignmentException(
                "No active finance associates available for assignment."
            )

        # 2. Calculate workload for each
        counts = []
        for assoc in associates:
            workload = await self.calculate_workload(assoc.id)
            counts.append((assoc, workload))

        # 3. Determine lowest workload
        min_workload = min(w for _, w in counts)

        # 4. Filter associates with lowest workload
        candidates = [assoc for assoc, w in counts if w == min_workload]

        # 5. Select one randomly
        selected_associate = random.choice(candidates)

        # 6. Update dispute
        dispute.assigned_to = selected_associate.id
        dispute.manager_id = selected_associate.manager_id
        await self.dispute_repo.update_dispute(dispute)

        # 7. Create assignment record
        await self.assignment_repo.deactivate_all_assignments_for_dispute(dispute.id)
        await self.assignment_repo.create_assignment(
            dispute_id=dispute.id,
            assigned_to=selected_associate.id,
            assigned_by=performed_by,
            active=True,
        )

        # 8. Log activity/audit
        await self.audit_service.log_event(
            dispute_id=dispute.id,
            action="ASSIGNED",
            performed_by=performed_by,
            metadata={
                "assigned_to": str(selected_associate.id),
                "manager_id": str(selected_associate.manager_id)
                if selected_associate.manager_id
                else None,
                "auto": True,
            },
        )

        return dispute

    async def reassign_dispute(
        self, dispute_id: UUID, new_associate_id: UUID, performed_by: UUID
    ) -> Dispute:
        """Manually reassigns a dispute to a specific associate (Finance Manager action)."""
        dispute = await self.dispute_repo.get_by_id(dispute_id)
        if not dispute:
            raise ValidationException("Dispute not found.")

        new_associate = await self.user_repo.get_user_by_id(new_associate_id)
        if not new_associate:
            raise ValidationException("New associate user not found.")

        # Update dispute
        dispute.assigned_to = new_associate_id
        dispute.manager_id = new_associate.manager_id
        await self.dispute_repo.update_dispute(dispute)

        # Deactivate old & create new assignment history
        await self.assignment_repo.deactivate_all_assignments_for_dispute(dispute.id)
        await self.assignment_repo.create_assignment(
            dispute_id=dispute.id,
            assigned_to=new_associate_id,
            assigned_by=performed_by,
            active=True,
        )

        # Audit the reassignment
        await self.audit_service.log_event(
            dispute_id=dispute.id,
            action="ASSIGNED",
            performed_by=performed_by,
            metadata={
                "assigned_to": str(new_associate_id),
                "manager_id": str(new_associate.manager_id)
                if new_associate.manager_id
                else None,
                "auto": False,
            },
        )

        return dispute
