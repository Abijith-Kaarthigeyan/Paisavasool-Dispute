from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.escalation import DisputeEscalation


class EscalationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, escalation_id: UUID) -> DisputeEscalation | None:
        result = await self.db.execute(
            select(DisputeEscalation).where(
                DisputeEscalation.id == escalation_id,
                DisputeEscalation.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_escalations_for_dispute(
        self, dispute_id: UUID
    ) -> list[DisputeEscalation]:
        result = await self.db.execute(
            select(DisputeEscalation).where(
                DisputeEscalation.dispute_id == dispute_id,
                DisputeEscalation.is_deleted.is_(False),
            )
        )
        return list(result.scalars().all())

    async def create_escalation(
        self,
        *,
        dispute_id: UUID,
        level: int,
        reason: str | None = None,
        escalated_to: UUID,
    ) -> DisputeEscalation:
        escalation = DisputeEscalation(
            dispute_id=dispute_id,
            level=level,
            reason=reason,
            escalated_to=escalated_to,
            escalated_at=datetime.now(),
            resolved=False,
        )
        self.db.add(escalation)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return escalation

    async def update_escalation(
        self, escalation: DisputeEscalation
    ) -> DisputeEscalation:
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return escalation
