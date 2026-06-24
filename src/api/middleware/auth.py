import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from src.core.config.settings import settings
from src.observability.logging.logger import logger

PUBLIC_ROUTES = {
    "/",
    "/health",
    "/docs",
    "/openapi.json",
    "/favicon.ico",
}


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS":
        return await call_next(request)
    if path in PUBLIC_ROUTES or path.startswith("/docs") or path.startswith("/openapi"):
        return await call_next(request)

    token = _extract_access_token(request)
    if not token:
        return JSONResponse(
            status_code=401,
            content={"success": False, "error": {"message": "Missing access token"}},
        )

    # 1. Try local verification of system JWT signed with shared SECRET_KEY
    is_system_verified = False
    try:
        from jose import jwt as jose_jwt

        payload = jose_jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        if payload.get("service") is True and payload.get("sub") == "ar-service":
            request.state.user = {
                "sub": "00000000-0000-0000-0000-000000000101",
                "email": "system@ar-service.internal",
                "role": "ADMIN",
                "is_active": True,
            }
            is_system_verified = True
    except Exception as e:
        logger.debug("Local verification failed or skipped: %s", str(e))

    if is_system_verified:
        return await call_next(request)

    # Validate token and retrieve user profile via Auth Service REST API
    try:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {token}"}
            cookies = {"access_token": token}

            response = await client.get(
                f"{settings.AUTH_SERVICE_URL}/auth/me",
                headers=headers,
                cookies=cookies,
                timeout=5.0,
            )

            if response.status_code != 200:
                logger.warning(
                    "Auth validation failed: status %d", response.status_code
                )
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "error": {"message": "Invalid or expired token"},
                    },
                )

            user_data = response.json()

            role_data = user_data.get("role")
            if isinstance(role_data, dict):
                role_name = role_data.get("role_name")
            else:
                role_name = role_data

            request.state.user = {
                "sub": user_data["id"],
                "email": user_data["email"],
                "role": role_name,
                "is_active": user_data.get("is_active", True),
            }

    except Exception as e:
        logger.error("Failed to communicate with Auth Service: %s", str(e))
        return JSONResponse(
            status_code=503,
            content={
                "success": False,
                "error": {"message": "Authentication service unavailable"},
            },
        )

    return await call_next(request)


def _extract_access_token(request: Request) -> str | None:
    # 1. Try Cookie
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token

    # 2. Try Authorization Header
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return None
