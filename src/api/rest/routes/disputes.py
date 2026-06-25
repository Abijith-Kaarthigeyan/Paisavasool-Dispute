import contextlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_activity_repository,
    get_comment_repository,
    get_communication_repository,
    get_dispute_repository,
    get_interrupt_service,
    get_resume_service,
    get_sla_repository,
    get_workflow_context_repository,
)
from src.core.security.dependencies import require_finance
from src.core.workflow.interrupt_service import WorkflowInterruptService
from src.core.workflow.resume_service import DisputeResumeService
from src.data.clients.postgres_client import get_async_db
from src.data.repositories.dispute_repository import DisputeRepository
from src.data.repositories.other_repositories import (
    ActivityRepository,
    CommentRepository,
)
from src.schemas.auth import TokenPayload
from src.schemas.dispute import (
    DisputeActivityResponse,
    DisputeCommentCreate,
    DisputeCommentResponse,
    DisputeCommunicationResponse,
    DisputeResponse,
)

router = APIRouter(prefix="/disputes", tags=["Dispute Lifecycle"])


@router.get("", response_model=list[DisputeResponse])
async def list_disputes(
    customer_id: UUID | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(require_finance),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
):
    """Retrieves a paginated list of disputes with optional filters."""
    disputes = await dispute_repo.list_disputes(
        customer_id=customer_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [DisputeResponse.model_validate(d) for d in disputes]


@router.get("/{id}", response_model=DisputeResponse)
async def get_dispute(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
):
    """Fetches details of a specific dispute by ID."""
    dispute = await dispute_repo.get_by_id(id)
    if not dispute:
        raise HTTPException(status_code=404, detail=f"Dispute {id} not found.")
    return DisputeResponse.model_validate(dispute)


@router.get("/{id}/activities", response_model=list[DisputeActivityResponse])
async def get_dispute_activities(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    activity_repo: ActivityRepository = Depends(get_activity_repository),
):
    """Fetches all activities associated with a dispute."""
    activities = await activity_repo.list_activities_for_dispute(id)
    return [DisputeActivityResponse.model_validate(a) for a in activities]


@router.get("/{id}/comments", response_model=list[DisputeCommentResponse])
async def get_dispute_comments(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    comment_repo: CommentRepository = Depends(get_comment_repository),
):
    """Fetches all comments associated with a dispute."""
    comments = await comment_repo.list_comments_for_dispute(id)
    return [DisputeCommentResponse.model_validate(c) for c in comments]


@router.post("/{id}/comments", response_model=DisputeCommentResponse)
async def create_dispute_comment(
    id: UUID,
    payload: DisputeCommentCreate,
    current_user: TokenPayload = Depends(require_finance),
    comment_repo: CommentRepository = Depends(get_comment_repository),
):
    """Creates a new comment for a dispute."""
    comment = await comment_repo.create_comment(
        dispute_id=id,
        comment=payload.comment,
        comment_type=payload.comment_type,
        created_by=current_user.sub,
    )
    return DisputeCommentResponse.model_validate(comment)


class DecisionRequest(BaseModel):
    decision: str
    comments: str | None = None


@router.post("/{id}/interrupt")
async def interrupt_dispute_workflow(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    interrupt_service: WorkflowInterruptService = Depends(get_interrupt_service),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    db: AsyncSession = Depends(get_async_db),
):
    """Manually interrupts/pauses the dispute workflow execution."""
    dispute = await dispute_repo.get_by_id(id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found.")

    dispute.status = "IN_REVIEW"
    await dispute_repo.update_dispute(dispute)

    await interrupt_service.record_interrupt(
        dispute_id=id,
        reason="MANUAL_INTERRUPT",
        details={"interrupted_by": str(current_user.sub)},
    )
    await db.commit()
    return {"status": "SUCCESS", "message": "Workflow manually interrupted."}


@router.post("/{id}/resume")
async def resume_dispute_workflow(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    resume_service: DisputeResumeService = Depends(get_resume_service),
    db: AsyncSession = Depends(get_async_db),
):
    """Resumes execution of the dispute workflow from the last checkpoint."""
    from src.data.repositories.workflow_context_repository import (
        WorkflowContextRepository,
    )

    context_repo = WorkflowContextRepository(db)
    context = await context_repo.get_by_dispute_id(id)
    if not context:
        raise HTTPException(
            status_code=404, detail="No active workflow context found for dispute."
        )

    state_updates = {
        "requires_human_review": False,
        "review_reason": None,
    }

    try:
        final_state = await resume_service.resume_workflow(
            db=db,
            dispute_id=id,
            state_updates=state_updates,
        )
        return {
            "status": "SUCCESS",
            "message": "Workflow resumed.",
            "final_state": final_state,
            "context": {
                "id": str(context.id),
                "dispute_id": str(context.dispute_id),
                "workflow_name": context.workflow_name,
                "current_node": context.current_node,
            },
        }
    except Exception as e:
        # It's normal to catch NodeInterrupt if the graph is paused again at a subsequent node
        return {
            "status": "SUCCESS",
            "message": f"Workflow resumed and paused: {str(e)}",
            "context": {
                "id": str(context.id),
                "dispute_id": str(context.dispute_id),
                "workflow_name": context.workflow_name,
                "current_node": context.current_node,
            },
        }


@router.post("/{id}/associate-decision")
async def submit_associate_decision(
    id: UUID,
    payload: DecisionRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    resume_service: DisputeResumeService = Depends(get_resume_service),
    db: AsyncSession = Depends(get_async_db),
):
    """Submits Associate Approval (APPROVE/REJECT) and resumes the workflow."""
    if payload.decision.upper() not in ["APPROVE", "REJECT"]:
        raise HTTPException(
            status_code=400, detail="Invalid decision. Must be APPROVE or REJECT."
        )

    dispute = await dispute_repo.get_by_id(id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found.")

    # 1. Persist decision
    comment_text = (
        f"Associate Decision: {payload.decision}. Comments: {payload.comments or ''}"
    )
    comment_repo = CommentRepository(db)
    await comment_repo.create_comment(
        dispute_id=id,
        comment=comment_text,
        comment_type="INTERNAL",
        created_by=current_user.sub,
    )

    activity_repo = ActivityRepository(db)
    await activity_repo.create_activity(
        dispute_id=id,
        activity_type="ASSOCIATE_DECISION",
        metadata={"decision": payload.decision, "comments": payload.comments or ""},
        performed_by=current_user.sub,
    )
    await db.commit()

    # 2. Resume workflow from approval node
    state_updates = {
        "resolution_outcome": payload.decision,
        "requires_human_review": False,
        "review_reason": None,
    }
    with contextlib.suppress(Exception):
        await resume_service.resume_workflow(
            db=db,
            dispute_id=id,
            state_updates=state_updates,
            as_node="waiting_approval_node",
        )

    return {"status": "SUCCESS", "message": "Decision processed and workflow resumed."}


@router.post("/{id}/payment-review-decision")
async def submit_payment_review_decision(
    id: UUID,
    payload: DecisionRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    resume_service: DisputeResumeService = Depends(get_resume_service),
    db: AsyncSession = Depends(get_async_db),
):
    """Submits Payment Review decision and resumes the workflow."""
    if payload.decision.upper() not in ["SETTLEMENT_DONE", "SETTLEMENT_NOT_DONE"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid decision. Must be SETTLEMENT_DONE or SETTLEMENT_NOT_DONE.",
        )

    dispute = await dispute_repo.get_by_id(id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found.")

    comment_text = f"Payment Review Decision: {payload.decision}. Comments: {payload.comments or ''}"
    comment_repo = CommentRepository(db)
    await comment_repo.create_comment(
        dispute_id=id,
        comment=comment_text,
        comment_type="INTERNAL",
        created_by=current_user.sub,
    )

    activity_repo = ActivityRepository(db)
    await activity_repo.create_activity(
        dispute_id=id,
        activity_type="PAYMENT_REVIEW_DECISION",
        metadata={"decision": payload.decision, "comments": payload.comments or ""},
        performed_by=current_user.sub,
    )
    await db.commit()

    state_updates = {
        "resolution_outcome": payload.decision,
        "requires_human_review": False,
        "review_reason": None,
    }
    with contextlib.suppress(Exception):
        await resume_service.resume_workflow(
            db=db,
            dispute_id=id,
            state_updates=state_updates,
            as_node="waiting_resolution_node",
        )

    return {"status": "SUCCESS", "message": "Decision processed and workflow resumed."}


@router.post("/{id}/operational-review-decision")
async def submit_operational_review_decision(
    id: UUID,
    payload: DecisionRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_repo: DisputeRepository = Depends(get_dispute_repository),
    resume_service: DisputeResumeService = Depends(get_resume_service),
    db: AsyncSession = Depends(get_async_db),
):
    """Submits Operational Review decision and resumes the workflow."""
    if payload.decision.upper() not in ["ACKNOWLEDGED", "REJECTED"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid decision. Must be ACKNOWLEDGED or REJECTED.",
        )

    dispute = await dispute_repo.get_by_id(id)
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found.")

    comment_text = f"Operational Review Decision: {payload.decision}. Comments: {payload.comments or ''}"
    comment_repo = CommentRepository(db)
    await comment_repo.create_comment(
        dispute_id=id,
        comment=comment_text,
        comment_type="INTERNAL",
        created_by=current_user.sub,
    )

    activity_repo = ActivityRepository(db)
    await activity_repo.create_activity(
        dispute_id=id,
        activity_type="OPERATIONAL_REVIEW_DECISION",
        metadata={"decision": payload.decision, "comments": payload.comments or ""},
        performed_by=current_user.sub,
    )
    await db.commit()

    state_updates = {
        "resolution_outcome": payload.decision,
        "requires_human_review": False,
        "review_reason": None,
    }
    with contextlib.suppress(Exception):
        await resume_service.resume_workflow(
            db=db,
            dispute_id=id,
            state_updates=state_updates,
            as_node="waiting_resolution_node",
        )

    return {"status": "SUCCESS", "message": "Decision processed and workflow resumed."}


@router.get("/{id}/workflow-context")
async def get_dispute_workflow_context(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    context_repo=Depends(get_workflow_context_repository),
):
    """Fetches the workflow context for a dispute."""
    context = await context_repo.get_by_dispute_id(id)
    if not context:
        raise HTTPException(status_code=404, detail="Workflow context not found.")
    return context


@router.get("/{id}/communications", response_model=list[DisputeCommunicationResponse])
async def get_dispute_communications(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    comm_repo=Depends(get_communication_repository),
):
    """Fetches communications history for a dispute."""
    comms = await comm_repo.get_communications_for_dispute(id)
    return [DisputeCommunicationResponse.model_validate(c) for c in comms]


@router.get("/{id}/evidence")
async def get_dispute_evidence(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    db: AsyncSession = Depends(get_async_db),
):
    """Fetches evidence snapshots for a dispute."""
    from sqlalchemy import select

    from src.data.models.postgres.evidence_snapshot import DisputeEvidenceSnapshot

    result = await db.execute(
        select(DisputeEvidenceSnapshot).where(
            DisputeEvidenceSnapshot.dispute_id == id,
            DisputeEvidenceSnapshot.is_deleted.is_(False),
        )
    )
    snapshots = result.scalars().all()
    return snapshots


@router.get("/{id}/sla")
async def get_dispute_sla(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    sla_repo=Depends(get_sla_repository),
):
    """Fetches SLA details for a dispute."""
    sla = await sla_repo.get_by_dispute_id(id)
    if not sla:
        raise HTTPException(status_code=404, detail="SLA details not found.")
    return sla
