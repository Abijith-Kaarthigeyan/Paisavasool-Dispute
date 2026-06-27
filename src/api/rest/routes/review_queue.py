from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import get_review_queue_service
from src.core.security.dependencies import require_finance
from src.core.services.review_queue_service import ReviewQueueService
from src.schemas.auth import TokenPayload
from src.schemas.dispute import ReviewQueueResolveRequest, ReviewQueueResponse

router = APIRouter(prefix="/review-queue", tags=["Review Queue"])


@router.get("", response_model=list[ReviewQueueResponse])
async def list_review_queue(
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(require_finance),
    review_queue_service: ReviewQueueService = Depends(get_review_queue_service),
):
    """Retrieves the current dispute review queue items."""
    items = await review_queue_service.list_review_queue(
        status=status, limit=limit, offset=offset
    )
    return [ReviewQueueResponse.model_validate(i) for i in items]


@router.post("/{id}/resolve")
async def resolve_review_queue_item(
    id: UUID,
    payload: ReviewQueueResolveRequest,
    current_user: TokenPayload = Depends(require_finance),
    review_queue_service: ReviewQueueService = Depends(get_review_queue_service),
):
    """Resolves a review queue item, updates the dispute metadata, and resumes execution from validation."""
    return await review_queue_service.resolve_item(
        id,
        invoice_number=payload.invoice_number,
        dispute_category=payload.dispute_category,
        comments=payload.comments,
        performed_by=current_user.sub,
    )
