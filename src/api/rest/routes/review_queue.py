import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_dispute_repository,
    get_resume_service,
    get_review_queue_repository,
)
from src.core.security.dependencies import require_finance
from src.core.workflow.resume_service import DisputeResumeService
from src.data.clients.postgres_client import get_async_db
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.review_queue_repository import ReviewQueueRepository
from src.schemas.auth import TokenPayload
from src.schemas.dispute import ReviewQueueResponse

router = APIRouter(prefix="/review-queue", tags=["Review Queue"])


class ReviewQueueResolveRequest(BaseModel):
    invoice_number: str
    dispute_category: str
    comments: str | None = None


@router.get("", response_model=list[ReviewQueueResponse])
async def list_review_queue(
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(require_finance),
    review_repo: ReviewQueueRepository = Depends(get_review_queue_repository),
):
    """Retrieves the current dispute review queue items."""
    items = await review_repo.list_review_queue(
        status=status, limit=limit, offset=offset
    )
    return [ReviewQueueResponse.model_validate(i) for i in items]


@router.post("/{id}/resolve")
async def resolve_review_queue_item(
    id: UUID,
    payload: ReviewQueueResolveRequest,
    current_user: TokenPayload = Depends(require_finance),
    review_repo: ReviewQueueRepository = Depends(get_review_queue_repository),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    resume_service: DisputeResumeService = Depends(get_resume_service),
    db: AsyncSession = Depends(get_async_db),
):
    """Resolves a review queue item, updates the dispute metadata, and resumes execution from validation."""
    item = await review_repo.get_by_id(id)
    if not item:
        raise HTTPException(status_code=404, detail="Review queue item not found.")

    dispute = await dispute_repo.get_by_id(item.dispute_id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found.")

    # 1. Update review queue item status
    item.status = "RESOLVED"
    await review_repo.update_review_queue_item(item)

    # 2. Update dispute fields
    from src.core.workflow.triage_agent import DisputeTriageAgent

    norm_cat = DisputeTriageAgent.normalize_category(payload.dispute_category)

    dispute.invoice_number = payload.invoice_number
    dispute.dispute_category = norm_cat
    dispute.status = "OPEN"

    # Query AR schema for invoice details if found
    is_sqlite = (db.bind.dialect.name == "sqlite") if db.bind else False
    table_name = "invoices" if is_sqlite else "ar.invoices"
    inv_res = await db.execute(
        text(
            f"SELECT id, customer_id FROM {table_name} WHERE invoice_number = :num AND is_deleted = false"
        ),
        {"num": payload.invoice_number},
    )
    row = inv_res.first()
    if row:
        dispute.invoice_id = row[0]
        dispute.customer_id = row[1]

    await dispute_repo.update_dispute(dispute)

    # 3. Create comment & activity log
    await db.execute(
        text(
            "INSERT INTO dispute_comments (id, dispute_id, comment, comment_type, created_by, created_at) "
            "VALUES (:c_id, :d_id, :comment, 'INTERNAL', :user_id, NOW())"
        ),
        {
            "c_id": uuid4(),
            "d_id": dispute.id,
            "comment": f"Resolved from review queue: {payload.comments or ''}",
            "user_id": current_user.sub,
        },
    )

    await db.execute(
        text(
            "INSERT INTO dispute_activities (id, dispute_id, activity_type, activity_metadata, performed_by, created_at) "
            "VALUES (:act_id, :d_id, 'REVIEW_QUEUE_RESOLVED', :meta, :user_id, NOW())"
        ),
        {
            "act_id": uuid4(),
            "d_id": dispute.id,
            "meta": f'{{"invoice_number": "{payload.invoice_number}", "dispute_category": "{norm_cat}"}}',
            "user_id": current_user.sub,
        },
    )

    await db.commit()

    # 4. Resume workflow from Validation Node
    state_updates = {
        "requires_human_review": False,
        "review_reason": None,
        "invoice_number": dispute.invoice_number,
        "dispute_category": dispute.dispute_category,
        "invoice_id": dispute.invoice_id,
        "customer_id": dispute.customer_id,
    }

    # Asynchronously resume LangGraph from Validation Node
    try:
        await resume_service.resume_workflow(
            db=db,
            dispute_id=dispute.id,
            state_updates=state_updates,
            as_node="validation_node",
        )
    except Exception as e:
        # If it raises interrupt again, that's fine, it means the graph paused again at a future node
        logger = logging.getLogger("resolve")
        logger.info("Resumed graph paused again: %s", str(e))

    return {"status": "SUCCESS", "message": "Dispute resolved and workflow resumed."}
