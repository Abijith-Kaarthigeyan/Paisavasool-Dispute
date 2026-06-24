from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, EmailStr


class RoleName(StrEnum):
    ADMIN = "ADMIN"
    FINANCE_MANAGER = "FINANCE_MANAGER"
    FINANCE_ASSOCIATE = "FINANCE_ASSOCIATE"


class TokenPayload(BaseModel):
    sub: UUID
    email: EmailStr
    role: RoleName
    is_active: bool
