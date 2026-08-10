"""FastAPI application for the local Meridian frontend."""

from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from meridian.api.models import ErrorResponse
from meridian.api.routes import approvals, documents, evaluations, intakes, runs, scenarios, system
from meridian.version import VERSION


_APPROVAL_DECISION_SUFFIX = "/decision"


def _is_approval_decision(request: Request) -> bool:
    return request.url.path.startswith("/api/v1/approvals/") and request.url.path.endswith(
        _APPROVAL_DECISION_SUFFIX
    )


async def _approval_http_exception_handler(request: Request, exc: HTTPException):
    if _is_approval_decision(request) and exc.status_code in {404, 409, 422}:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail and "message" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
    return await http_exception_handler(request, exc)


async def _approval_validation_exception_handler(request: Request, exc: RequestValidationError):
    if _is_approval_decision(request):
        invalid_decision = any(error.get("loc", ())[-1:] == ("decision",) for error in exc.errors())
        invalid_approver = any(error.get("loc", ())[-1:] == ("approver",) for error in exc.errors())
        error = ErrorResponse(
            code=(
                "invalid_decision"
                if invalid_decision
                else "approver_required"
                if invalid_approver
                else "request_validation_error"
            ),
            message=(
                "approval decision must be 'approved' or 'rejected'"
                if invalid_decision
                else "a named approver is required"
                if invalid_approver
                else "approval decision body is malformed"
            ),
        )
        return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=error.model_dump())
    return await request_validation_exception_handler(request, exc)


def _local_origins() -> list[str]:
    configured = os.environ.get("MERIDIAN_CORS_ORIGINS")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app() -> FastAPI:
    app = FastAPI(title="Meridian API", version=VERSION)
    app.add_exception_handler(HTTPException, _approval_http_exception_handler)
    app.add_exception_handler(RequestValidationError, _approval_validation_exception_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_local_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )
    api = APIRouter(prefix="/api/v1")
    api.include_router(system.router)
    api.include_router(scenarios.router)
    api.include_router(runs.router)
    api.include_router(intakes.router)
    api.include_router(approvals.router)
    api.include_router(evaluations.router)
    api.include_router(documents.router)
    app.include_router(api)
    return app


app = create_app()
