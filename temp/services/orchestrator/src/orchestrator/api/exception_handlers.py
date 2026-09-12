"""Stable application error responses for the HTTP boundary."""

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from orchestrator.ports.config_store import ConfigStoreUnavailableError
from orchestrator.ports.investigation_store import InvestigationStoreUnavailableError
from orchestrator.ports.manual_store import ManualStoreUnavailableError


@dataclass(slots=True)
class ApiError(Exception):
    code: str
    message: str
    status_code: int


async def api_error_handler(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ApiError):
        raise error
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
    )


async def request_validation_error_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    return JSONResponse(
        status_code=422 if _request.url.path == "/v1/agent/investigate" else 400,
        content={
            "error": {
                "code": "invalid_request",
                "message": "The request body is invalid",
            }
        },
    )


async def dependency_unavailable_handler(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(
        error,
        (
            ConfigStoreUnavailableError,
            InvestigationStoreUnavailableError,
            ManualStoreUnavailableError,
        ),
    ):
        raise error
    code = (
        "database_unavailable"
        if isinstance(error, (ConfigStoreUnavailableError, InvestigationStoreUnavailableError))
        else "service_unavailable"
    )
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": code,
                "message": "A required service is temporarily unavailable",
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(ConfigStoreUnavailableError, dependency_unavailable_handler)
    app.add_exception_handler(InvestigationStoreUnavailableError, dependency_unavailable_handler)
    app.add_exception_handler(ManualStoreUnavailableError, dependency_unavailable_handler)
