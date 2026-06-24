from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.sla import DisputeSLA


class SLARepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, sla_id: UUID) -> DisputeSLA | None:
        result = await self.db.execute(
            select(DisputeSLA).where(
                DisputeSLA.id == sla_id,
                DisputeSLA.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_dispute_id(self, dispute_id: UUID) -> DisputeSLA | None:
        result = await self.db.execute(
            select(DisputeSLA).where(
                DisputeSLA.dispute_id == dispute_id,
                DisputeSLA.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def create_sla(
        self,
        *,
        dispute_id: UUID,
        sla_minutes: int,
        started_at: datetime,
        is_paused: bool = False,
        current_percentage: float = 0.0,
        status: str = "ON_TRACK",
    ) -> DisputeSLA:
        sla = DisputeSLA(
            dispute_id=dispute_id,
            sla_minutes=sla_minutes,
            started_at=started_at,
            is_paused=is_paused,
            current_percentage=current_percentage,
            status=status,
            accumulated_paused_minutes=0.0,
        )
        self.db.add(sla)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return sla

    async def update_sla(self, sla: DisputeSLA) -> DisputeSLA:
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return sla
