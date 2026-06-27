from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

InternalTeamKey = Literal["FINANCE_TEAM", "QUALITY_TEAM", "LOGISTICS_TEAM"]


class InternalTeamContactResponse(BaseModel):
    team_key: str
    display_name: str
    email: EmailStr
    updated_at: datetime
    updated_by: UUID | None = None

    model_config = {"from_attributes": True}


class InternalTeamContactUpdateItem(BaseModel):
    team_key: InternalTeamKey
    email: EmailStr


class InternalTeamContactsUpdateRequest(BaseModel):
    teams: list[InternalTeamContactUpdateItem] = Field(min_length=1)
