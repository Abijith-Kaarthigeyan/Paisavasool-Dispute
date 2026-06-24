from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.data.models.postgres.recommendation import DisputeResolutionRecommendation


class RecommendationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, rec_id: UUID) -> DisputeResolutionRecommendation | None:
        result = await self.db.execute(
            select(DisputeResolutionRecommendation).where(
                DisputeResolutionRecommendation.id == rec_id,
                DisputeResolutionRecommendation.is_deleted.is_(False),
            )
        )
        return result.scalar_one_or_none()

    async def get_recommendations_for_dispute(
        self, dispute_id: UUID
    ) -> list[DisputeResolutionRecommendation]:
        result = await self.db.execute(
            select(DisputeResolutionRecommendation)
            .where(
                DisputeResolutionRecommendation.dispute_id == dispute_id,
                DisputeResolutionRecommendation.is_deleted.is_(False),
            )
            .order_by(DisputeResolutionRecommendation.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_latest_recommendation(
        self, dispute_id: UUID
    ) -> DisputeResolutionRecommendation | None:
        result = await self.db.execute(
            select(DisputeResolutionRecommendation)
            .where(
                DisputeResolutionRecommendation.dispute_id == dispute_id,
                DisputeResolutionRecommendation.is_deleted.is_(False),
            )
            .order_by(DisputeResolutionRecommendation.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create_recommendation(
        self,
        *,
        dispute_id: UUID,
        recommended_action: str,
        confidence: float,
        created_by_agent: str,
        recommended_invoice_json: dict | None = None,
    ) -> DisputeResolutionRecommendation:
        rec = DisputeResolutionRecommendation(
            dispute_id=dispute_id,
            recommended_action=recommended_action,
            confidence=confidence,
            created_by_agent=created_by_agent,
            recommended_invoice_json=recommended_invoice_json,
            created_at=datetime.now(),
        )
        self.db.add(rec)
        await self.db.flush()
        return rec
