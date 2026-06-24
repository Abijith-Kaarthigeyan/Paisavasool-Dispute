from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_recommendation_service
from src.core.security.dependencies import require_finance
from src.core.services.recommendation_service import RecommendationService
from src.schemas.auth import TokenPayload
from src.schemas.dispute import DisputeRecommendationResponse

router = APIRouter(prefix="/disputes", tags=["Recommendations"])


@router.get("/{id}/recommendations", response_model=list[DisputeRecommendationResponse])
async def get_dispute_recommendations(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    rec_service: RecommendationService = Depends(get_recommendation_service),
):
    """Retrieves all recommendation versions for a specific dispute."""
    recs = await rec_service.list_recommendations_history(id)
    return [DisputeRecommendationResponse.model_validate(r) for r in recs]
