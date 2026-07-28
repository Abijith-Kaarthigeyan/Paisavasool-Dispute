from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from src.api.dependencies import (
    get_case_attachment_service,
    get_case_intake_service,
    get_case_service,
)
from src.core.security.dependencies import require_finance
from src.core.services.case_attachment_service import CaseAttachmentService
from src.core.services.case_intake_service import CaseIntakeService
from src.core.services.case_service import CaseService
from src.schemas.auth import TokenPayload
from src.schemas.case import CaseAttachmentResponse, CaseIntakeRequest, CaseResponse
from src.schemas.dispute import DisputeResponse

router = APIRouter(prefix="/cases", tags=["Case Management"])


@router.get("", response_model=list[CaseResponse])
async def list_cases(
    response: Response,
    status: str | None = Query(None, description="Filter by case status"),
    search: str | None = Query(
        None, description="Search by case number, customer email, or email subject"
    ),
    created_at_from: datetime | None = Query(
        None, description="Created-at range start"
    ),
    created_at_to: datetime | None = Query(None, description="Created-at range end"),
    sort_by: str | None = Query(
        None,
        description="Allowlisted sort column: created_at, case_number, customer_email, status",
    ),
    sort_order: str | None = Query(
        "desc", description="Sort direction: asc or desc (default desc)"
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(require_finance),
    case_service: CaseService = Depends(get_case_service),
):
    """Retrieves a paginated list of cases."""
    cases, total = await case_service.list_cases(
        status=status,
        search=search,
        created_at_from=created_at_from,
        created_at_to=created_at_to,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )
    response.headers["X-Total-Count"] = str(total)
    return [CaseResponse.model_validate(c) for c in cases]


@router.post("/intake", status_code=201)
async def create_case_intake(
    payload: CaseIntakeRequest,
    current_user: TokenPayload = Depends(require_finance),
    intake_service: CaseIntakeService = Depends(get_case_intake_service),
):
    """Processes customer email intake, checks idempotency, and queues triage/generation workflow."""
    return await intake_service.process_intake(
        payload,
        performed_by=current_user.sub,
    )


@router.get("/{id}/disputes", response_model=list[DisputeResponse])
async def get_case_disputes(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    case_service: CaseService = Depends(get_case_service),
):
    """Retrieves all disputes associated with a specific case."""
    disputes = await case_service.list_disputes_for_case_for_user(
        id, current_user.role, current_user.sub
    )
    return [DisputeResponse.model_validate(d) for d in disputes]


@router.get("/{case_id}/attachments", response_model=list[CaseAttachmentResponse])
async def list_case_attachments(
    case_id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    attachment_service: CaseAttachmentService = Depends(get_case_attachment_service),
):
    """Lists PDF attachments stored for a case at intake."""
    attachments = await attachment_service.list_attachments(case_id)
    return [CaseAttachmentResponse.model_validate(a) for a in attachments]


@router.get("/{case_id}/attachments/{attachment_id}/file")
async def download_case_attachment(
    case_id: UUID,
    attachment_id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    attachment_service: CaseAttachmentService = Depends(get_case_attachment_service),
):
    """Streams a case attachment inline (e.g. PDF in browser)."""
    content, attachment = await attachment_service.get_attachment_file(
        case_id, attachment_id
    )
    return Response(
        content=content,
        media_type=attachment.mime_type,
        headers={"Content-Disposition": f'inline; filename="{attachment.filename}"'},
    )


@router.get("/{id}", response_model=CaseResponse)
async def get_case(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    case_service: CaseService = Depends(get_case_service),
):
    """Fetches details of a specific case by ID."""
    case = await case_service.get_case(id)
    return CaseResponse.model_validate(case)
