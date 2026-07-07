from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.dispute import Dispute
from src.data.models.postgres.review_queue import DisputeReviewQueue


class ReviewQueueRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, item_id: UUID) -> DisputeReviewQueue | None:
        result = await self.db.execute(
            select(DisputeReviewQueue).where(
                DisputeReviewQueue.id == item_id,
                DisputeReviewQueue.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_dispute_id(self, dispute_id: UUID) -> DisputeReviewQueue | None:
        result = await self.db.execute(
            select(DisputeReviewQueue).where(
                DisputeReviewQueue.dispute_id == dispute_id,
                DisputeReviewQueue.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def list_review_queue(
        self, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[DisputeReviewQueue]:
        query = select(DisputeReviewQueue).where(
            DisputeReviewQueue.is_deleted.is_(False)
        )
        if status:
            query = query.where(DisputeReviewQueue.status == status)

        query = (
            query.order_by(DisputeReviewQueue.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def list_review_queue_for_associate(
        self,
        associate_id: UUID,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DisputeReviewQueue]:
        query = (
            select(DisputeReviewQueue)
            .join(Dispute, DisputeReviewQueue.dispute_id == Dispute.id)
            .where(
                DisputeReviewQueue.is_deleted.is_(False),
                Dispute.is_deleted.is_(False),
                Dispute.assigned_to == associate_id,
            )
        )
        if status:
            query = query.where(DisputeReviewQueue.status == status)

        query = (
            query.order_by(DisputeReviewQueue.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def create_review_queue_item(
        self,
        *,
        dispute_id: UUID,
        review_reason: str,
        assigned_to: UUID | None = None,
        status: str = "PENDING",
        error_message: str | None = None,
        stack_trace: str | None = None,
        retry_count: int = 0,
    ) -> DisputeReviewQueue:
        item = DisputeReviewQueue(
            dispute_id=dispute_id,
            review_reason=review_reason,
            assigned_to=assigned_to,
            status=status,
            error_message=error_message,
            stack_trace=stack_trace,
            retry_count=retry_count,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.db.add(item)
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return item

    async def update_review_queue_item(
        self, item: DisputeReviewQueue
    ) -> DisputeReviewQueue:
        item.updated_at = datetime.now()
        if not self.db.sync_session._flushing:
            await self.db.flush()
        return item
