from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.core.exceptions.base import AppException
from src.observability.logging.logger import logger


async def app_exception_handler(request: Request, exc: AppException):
    logger.warning("%s %s %s", request.method, request.url.path, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "error": {"message": exc.message}},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "%s %s validation failed",
        request.method,
        request.url.path,
        extra={"details": exc.errors()},
    )
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {"message": "Validation failed", "details": exc.errors()},
        },
    )


async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("%s %s unhandled error", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": {"message": "Internal server error"}},
    )
