from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.assignment import DisputeAssignment


class AssignmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, assignment_id: UUID) -> DisputeAssignment | None:
        result = await self.db.execute(
            select(DisputeAssignment).where(
                DisputeAssignment.id == assignment_id,
                DisputeAssignment.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_assignment_for_dispute(
        self, dispute_id: UUID
    ) -> DisputeAssignment | None:
        result = await self.db.execute(
            select(DisputeAssignment).where(
                DisputeAssignment.dispute_id == dispute_id,
                DisputeAssignment.active.is_(True),
                DisputeAssignment.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def deactivate_all_assignments_for_dispute(self, dispute_id: UUID) -> None:
        await self.db.execute(
            update(DisputeAssignment)
            .where(
                DisputeAssignment.dispute_id == dispute_id,
                DisputeAssignment.active.is_(True),
            )
            .values(active=False)
        )

    async def create_assignment(
        self,
        *,
        dispute_id: UUID,
        assigned_to: UUID,
        assigned_by: UUID | None = None,
        active: bool = True,
    ) -> DisputeAssignment:
        assignment = DisputeAssignment(
            dispute_id=dispute_id,
            assigned_to=assigned_to,
            assigned_by=assigned_by,
            active=active,
            assigned_at=datetime.now(),
        )
        self.db.add(assignment)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return assignment
