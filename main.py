from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from src.api.middleware.auth import auth_middleware
from src.api.middleware.cors import setup_cors
from src.api.middleware.logging import logging_middleware
from src.api.middleware.request_id import request_id_middleware
from src.api.rest.routes.assignments import router as assignments_router
from src.api.rest.routes.cases import router as cases_router
from src.api.rest.routes.disputes import router as disputes_router
from src.api.rest.routes.recommendations import router as recommendations_router
from src.api.rest.routes.review_queue import router as review_queue_router
from src.core.config.settings import settings
from src.core.exceptions.base import AppException
from src.core.exceptions.handlers import (
    app_exception_handler,
    global_exception_handler,
    validation_exception_handler,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup operations (e.g. database connections check or queue initialization)
    yield
    # Shutdown operations


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="Dispute Management Service for Paisa Vasool Accounts Receivable Assistant.",
    lifespan=lifespan,
)

setup_cors(app)

# Register custom exception handlers
app.add_exception_handler(AppException, app_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# Include API routes
app.include_router(cases_router, prefix="/api/v1")
app.include_router(disputes_router, prefix="/api/v1")
app.include_router(assignments_router, prefix="/api/v1")
app.include_router(recommendations_router, prefix="/api/v1")
app.include_router(review_queue_router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"message": "Paisa Vasool Dispute Service"}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "dispute-service",
    }


# Register Middlewares (note: middleware execution order is bottom-to-top)
@app.middleware("http")
async def app_logging_middleware(request: Request, call_next):
    return await logging_middleware(request, call_next)


@app.middleware("http")
async def app_request_id_middleware(request: Request, call_next):
    return await request_id_middleware(request, call_next)


@app.middleware("http")
async def app_auth_middleware(request: Request, call_next):
    return await auth_middleware(request, call_next)
