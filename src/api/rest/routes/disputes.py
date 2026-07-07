from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.dependencies import (
    get_associate_communication_service,
    get_dispute_close_service,
    get_dispute_decision_service,
    get_dispute_service,
    get_dispute_workflow_service,
    get_escalation_service,
)
from src.core.security.dependencies import require_finance, require_roles
from src.core.services.associate_communication_service import (
    AssociateCommunicationService,
)
from src.core.services.dispute_close_service import DisputeCloseService
from src.core.services.dispute_decision_service import DisputeDecisionService
from src.core.services.dispute_service import DisputeService
from src.core.services.dispute_workflow_service import DisputeWorkflowService
from src.core.services.escalation_service import EscalationService
from src.schemas.auth import RoleName, TokenPayload
from src.schemas.dispute import (
    AssociateCommunicationDraftRequest,
    AssociateCommunicationDraftResponse,
    AssociateCommunicationSendRequest,
    DisputeActivityResponse,
    DisputeCloseRequest,
    DisputeCommentCreate,
    DisputeCommentResponse,
    DisputeCommunicationResponse,
    DisputeDecisionRequest,
    DisputeEscalateRequest,
    DisputeResponse,
)

router = APIRouter(prefix="/disputes", tags=["Dispute Lifecycle"])

require_finance_or_manager = require_roles(
    [RoleName.FINANCE_ASSOCIATE, RoleName.FINANCE_MANAGER]
)


@router.get("", response_model=list[DisputeResponse])
async def list_disputes(
    customer_id: UUID | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Retrieves a paginated list of disputes with optional filters."""
    disputes = await dispute_service.list_disputes_for_user(
        role=current_user.role,
        user_id=current_user.sub,
        customer_id=customer_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [DisputeResponse.model_validate(d) for d in disputes]


@router.get("/assigned/me", response_model=list[DisputeResponse])
async def get_my_assigned_disputes(
    current_user: TokenPayload = Depends(require_finance_or_manager),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """List disputes assigned to the authenticated user."""
    disputes = await dispute_service.list_assigned_disputes(current_user.sub)
    return [DisputeResponse.model_validate(d) for d in disputes]


@router.get("/{id}", response_model=DisputeResponse)
async def get_dispute(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Fetches details of a specific dispute by ID."""
    dispute = await dispute_service.get_dispute_for_user(
        id, current_user.role, current_user.sub
    )
    return DisputeResponse.model_validate(dispute)


@router.get("/{id}/activities", response_model=list[DisputeActivityResponse])
async def get_dispute_activities(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Fetches all activities associated with a dispute."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    activities = await dispute_service.list_activities(id)
    return [DisputeActivityResponse.model_validate(a) for a in activities]


@router.get("/{id}/comments", response_model=list[DisputeCommentResponse])
async def get_dispute_comments(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Fetches all comments associated with a dispute."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    comments = await dispute_service.list_comments(id)
    return [DisputeCommentResponse.model_validate(c) for c in comments]


@router.post("/{id}/comments", response_model=DisputeCommentResponse)
async def create_dispute_comment(
    id: UUID,
    payload: DisputeCommentCreate,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Creates a new comment for a dispute."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    comment = await dispute_service.create_comment(
        dispute_id=id,
        comment=payload.comment,
        comment_type=payload.comment_type,
        created_by=current_user.sub,
    )
    return DisputeCommentResponse.model_validate(comment)


@router.post("/{id}/interrupt")
async def interrupt_dispute_workflow(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    workflow_service: DisputeWorkflowService = Depends(get_dispute_workflow_service),
):
    """Manually interrupts/pauses the dispute workflow execution."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await workflow_service.interrupt_workflow(id, performed_by=current_user.sub)


@router.post("/{id}/resume")
async def resume_dispute_workflow(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    workflow_service: DisputeWorkflowService = Depends(get_dispute_workflow_service),
):
    """Resumes execution of the dispute workflow from the last checkpoint."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await workflow_service.resume_workflow(id)


@router.post("/{id}/associate-decision")
async def submit_associate_decision(
    id: UUID,
    payload: DisputeDecisionRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    decision_service: DisputeDecisionService = Depends(get_dispute_decision_service),
):
    """Submits Associate Approval (APPROVE/REJECT/EDIT_AND_APPLY) and resumes the workflow."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await decision_service.submit_associate_decision(
        id,
        decision=payload.decision,
        comments=payload.comments,
        amended_invoice_json=payload.amended_invoice_json,
        performed_by=current_user.sub,
    )


@router.post("/{id}/payment-review-decision")
async def submit_payment_review_decision(
    id: UUID,
    payload: DisputeDecisionRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    decision_service: DisputeDecisionService = Depends(get_dispute_decision_service),
):
    """Submits Payment Review decision and resumes the workflow."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await decision_service.submit_payment_review_decision(
        id,
        decision=payload.decision,
        comments=payload.comments,
        performed_by=current_user.sub,
    )


@router.post("/{id}/operational-review-decision")
async def submit_operational_review_decision(
    id: UUID,
    payload: DisputeDecisionRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    decision_service: DisputeDecisionService = Depends(get_dispute_decision_service),
):
    """Submits Operational Review decision and resumes the workflow."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await decision_service.submit_operational_review_decision(
        id,
        decision=payload.decision,
        comments=payload.comments,
        performed_by=current_user.sub,
    )


@router.get("/{id}/workflow-context")
async def get_dispute_workflow_context(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Fetches the workflow context for a dispute."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await dispute_service.get_workflow_context(id)


@router.get("/{id}/communications", response_model=list[DisputeCommunicationResponse])
async def get_dispute_communications(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Fetches communications history for a dispute."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    comms = await dispute_service.list_communications(id)
    return [DisputeCommunicationResponse.model_validate(c) for c in comms]


@router.get(
    "/{id}/communications/draft/latest",
    response_model=AssociateCommunicationDraftResponse,
)
async def get_latest_associate_communication_draft(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    comm_service: AssociateCommunicationService = Depends(
        get_associate_communication_service
    ),
):
    """Returns the latest active associate reply draft (GENERATING or READY)."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    draft = await comm_service.get_latest_draft(id)
    if not draft:
        raise HTTPException(status_code=404, detail="No active draft found.")
    return AssociateCommunicationDraftResponse(
        id=draft.id,
        recipient=draft.recipient or "",
        subject=draft.subject or "",
        body=draft.body or "",
        status=draft.status,
        created_at=draft.created_at,
    )


@router.post(
    "/{id}/communications/draft",
    response_model=AssociateCommunicationDraftResponse,
)
async def draft_associate_communication(
    id: UUID,
    payload: AssociateCommunicationDraftRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    comm_service: AssociateCommunicationService = Depends(
        get_associate_communication_service
    ),
):
    """Generates an AI-assisted email draft for associate review."""
    dispute = await dispute_service.get_dispute_for_user(
        id, current_user.role, current_user.sub
    )
    draft = await comm_service.generate_and_persist_draft(
        dispute,
        trigger="MANUAL_REGENERATE",
        instructions=payload.instructions,
    )
    await comm_service.comm_repo.db.commit()
    return AssociateCommunicationDraftResponse(
        id=draft.id,
        recipient=draft.recipient or "",
        subject=draft.subject or "",
        body=draft.body or "",
        status=draft.status,
        created_at=draft.created_at,
    )


@router.post(
    "/{id}/communications/send",
    response_model=DisputeCommunicationResponse,
)
async def send_associate_communication(
    id: UUID,
    payload: AssociateCommunicationSendRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    comm_service: AssociateCommunicationService = Depends(
        get_associate_communication_service
    ),
):
    """Persists an associate-authored outbound email on the dispute thread."""
    dispute = await dispute_service.get_dispute_for_user(
        id, current_user.role, current_user.sub
    )
    comm = await comm_service.send_email(
        dispute,
        recipient=payload.recipient,
        subject=payload.subject,
        body=payload.body,
        sent_by=current_user.sub,
        attachments=[
            {
                "filename": att.filename,
                "content_base64": att.content_base64,
                "mime_type": att.mime_type,
            }
            for att in payload.attachments
        ],
        pause_sla_till_reply=payload.pause_sla_till_reply,
    )
    return DisputeCommunicationResponse.model_validate(comm)


@router.get("/{id}/evidence")
async def get_dispute_evidence(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Fetches evidence snapshots for a dispute."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await dispute_service.list_evidence_snapshots(id)


@router.get("/{id}/sla")
async def get_dispute_sla(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
):
    """Fetches SLA details for a dispute."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    return await dispute_service.get_sla(id)


@router.post("/{id}/escalate")
async def escalate_dispute_manually(
    id: UUID,
    payload: DisputeEscalateRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    escalation_service: EscalationService = Depends(get_escalation_service),
):
    """Manually escalates a dispute to the manager."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    await escalation_service.escalate_to_manager_manually(
        dispute_id=id,
        reason=payload.comments,
    )
    return {"status": "SUCCESS", "message": "Dispute manually escalated to manager."}


@router.post("/{id}/close", response_model=DisputeResponse)
async def close_dispute_manually(
    id: UUID,
    payload: DisputeCloseRequest,
    current_user: TokenPayload = Depends(require_finance),
    dispute_service: DisputeService = Depends(get_dispute_service),
    close_service: DisputeCloseService = Depends(get_dispute_close_service),
):
    """Manually closes a dispute without completing the automated workflow."""
    await dispute_service.get_dispute_for_user(id, current_user.role, current_user.sub)
    dispute = await close_service.manual_close(
        id,
        resolution_method=payload.resolution_method,
        resolution_outcome=payload.resolution_outcome,
        comments=payload.comments,
        performed_by=current_user.sub,
    )
    return DisputeResponse.model_validate(dispute)
