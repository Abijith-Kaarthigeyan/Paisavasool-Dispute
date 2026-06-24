from uuid import UUID

from src.core.exceptions.business_exceptions import ValidationException
from src.core.services.audit_service import AuditService
from src.data.models.postgres.recommendation import DisputeResolutionRecommendation
from src.data.repositories.recommendation_repository import RecommendationRepository


class RecommendationService:
    def __init__(
        self,
        rec_repo: RecommendationRepository,
        audit_service: AuditService,
    ):
        self.rec_repo = rec_repo
        self.audit_service = audit_service

    async def persist_recommendation(
        self,
        *,
        dispute_id: UUID,
        recommended_action: str,
        confidence: float,
        created_by_agent: str,
        recommended_invoice_json: dict | None = None,
    ) -> DisputeResolutionRecommendation:
        """Persists a new recommendation for a dispute."""
        rec = await self.rec_repo.create_recommendation(
            dispute_id=dispute_id,
            recommended_action=recommended_action,
            confidence=confidence,
            created_by_agent=created_by_agent,
            recommended_invoice_json=recommended_invoice_json,
        )

        await self.audit_service.log_event(
            dispute_id=dispute_id,
            action="COMMENT_ADDED",  # standard mapping for a comment / recommendation audit
            metadata={
                "recommendation_id": str(rec.id),
                "action": recommended_action,
                "confidence": confidence,
                "agent": created_by_agent,
            },
        )

        return rec

    async def retrieve_latest_recommendation(self, dispute_id: UUID) -> DisputeResolutionRecommendation:
        """Retrieves the latest version of recommendations for a dispute."""
        rec = await self.rec_repo.get_latest_recommendation(dispute_id)
        if not rec:
            raise ValidationException(f"No recommendation found for dispute {dispute_id}.")
        return rec

    async def list_recommendations_history(self, dispute_id: UUID) -> list[DisputeResolutionRecommendation]:
        """Lists historical recommendations for auditing / version comparison."""
        return await self.rec_repo.get_recommendations_for_dispute(dispute_id)
