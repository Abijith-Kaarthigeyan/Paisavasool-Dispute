from fastapi import APIRouter, Depends

from src.api.dependencies import get_internal_team_config_service
from src.core.security.dependencies import require_admin
from src.core.services.internal_team_config_service import InternalTeamConfigService
from src.schemas.auth import TokenPayload
from src.schemas.internal_team import (
    InternalTeamContactResponse,
    InternalTeamContactsUpdateRequest,
)

router = APIRouter(prefix="/admin", tags=["Admin Configuration"])


@router.get(
    "/internal-team-contacts",
    response_model=list[InternalTeamContactResponse],
)
async def list_internal_team_contacts(
    current_user: TokenPayload = Depends(require_admin),
    config_service: InternalTeamConfigService = Depends(
        get_internal_team_config_service
    ),
) -> list[InternalTeamContactResponse]:
    """Returns configured email addresses for internal escalation teams."""
    contacts = await config_service.list_contacts()
    return [InternalTeamContactResponse.model_validate(c) for c in contacts]


@router.put(
    "/internal-team-contacts",
    response_model=list[InternalTeamContactResponse],
)
async def update_internal_team_contacts(
    payload: InternalTeamContactsUpdateRequest,
    current_user: TokenPayload = Depends(require_admin),
    config_service: InternalTeamConfigService = Depends(
        get_internal_team_config_service
    ),
) -> list[InternalTeamContactResponse]:
    """Updates email addresses for one or more internal escalation teams."""
    updates = [(item.team_key, str(item.email)) for item in payload.teams]
    contacts = await config_service.update_contacts(
        updates,
        updated_by=current_user.sub,
    )
    return [InternalTeamContactResponse.model_validate(c) for c in contacts]
