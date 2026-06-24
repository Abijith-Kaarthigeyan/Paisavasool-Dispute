from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_assignment_service
from src.core.security.dependencies import require_finance
from src.core.services.assignment_service import AssignmentService
from src.schemas.auth import TokenPayload
from src.schemas.dispute import DisputeAssignmentRequest, DisputeResponse

router = APIRouter(prefix="/disputes", tags=["Assignments"])


@router.post("/{id}/assign", response_model=DisputeResponse)
async def assign_dispute(
    id: UUID,
    current_user: TokenPayload = Depends(require_finance),
    assignment_service: AssignmentService = Depends(get_assignment_service),
):
    """Automatically assigns a dispute based on associate workload."""
    try:
        dispute = await assignment_service.assign_dispute(id, performed_by=current_user.sub)
        return DisputeResponse.model_validate(dispute)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{id}/reassign", response_model=DisputeResponse)
async def reassign_dispute(
    id: UUID,
    payload: DisputeAssignmentRequest,
    current_user: TokenPayload = Depends(require_finance),
    assignment_service: AssignmentService = Depends(get_assignment_service),
):
    """Manually reassigns a dispute to a specific associate (Finance Manager action)."""
    try:
        dispute = await assignment_service.reassign_dispute(
            id, payload.assigned_to, performed_by=current_user.sub
        )
        return DisputeResponse.model_validate(dispute)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
