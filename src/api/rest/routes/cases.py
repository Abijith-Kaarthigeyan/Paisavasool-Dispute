from uuid import UUID

from fastapi import APIRouter, Depends, Query

from src.api.dependencies import get_case_intake_service, get_case_service
from src.core.security.dependencies import require_finance
from src.core.services.case_intake_service import CaseIntakeService
from src.core.services.case_service import CaseService
from src.schemas.auth import TokenPayload
from src.schemas.case import CaseIntakeRequest, CaseResponse
from src.schemas.dispute import DisputeResponse

router = APIRouter(prefix="/cases", tags=["Case Management"])


@router.get("", response_model=list[CaseResponse])
async def list_cases(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: TokenPayload = Depends(require_finance),
    case_service: CaseService = Depends(get_case_service),
):
    """Retrieves a paginated list of cases."""
    cases = await case_service.list_cases(limit=limit, offset=offset)
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
    disputes = await case_service.list_disputes_for_case(id)
    return [DisputeResponse.model_validate(d) for d in disputes]


@router.get("/{id}", response_model=CaseResponse)
async def get_case(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    case_service: CaseService = Depends(get_case_service),
):
    """Fetches details of a specific case by ID."""
    case = await case_service.get_case(id)
    return CaseResponse.model_validate(case)
