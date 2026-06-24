from collections.abc import Iterable

from fastapi import Depends, Request

from src.core.exceptions.business_exceptions import (
    ForbiddenException,
    UnauthorizedException,
)
from src.schemas.auth import RoleName, TokenPayload


async def get_current_user(request: Request) -> TokenPayload:
    user_dict = getattr(request.state, "user", None)
    if not user_dict:
        raise UnauthorizedException("User not authenticated")
    return TokenPayload(**user_dict)


def require_roles(allowed_roles: Iterable[RoleName]):
    allowed = set(allowed_roles)

    async def dependency(current_user: TokenPayload = Depends(get_current_user)):
        if current_user.role not in allowed:
            raise ForbiddenException("Insufficient role privileges")
        return current_user

    return dependency


require_admin = require_roles([RoleName.ADMIN])
require_finance = require_roles(
    [RoleName.ADMIN, RoleName.FINANCE_MANAGER, RoleName.FINANCE_ASSOCIATE]
)
