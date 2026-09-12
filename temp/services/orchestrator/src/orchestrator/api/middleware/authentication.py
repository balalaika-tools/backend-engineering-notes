"""Bearer authentication for every versioned API endpoint."""

import re
from collections.abc import Awaitable, Callable
from typing import cast

from fastapi.responses import JSONResponse
from orchestrator.bootstrap.runtime import RuntimeState
from orchestrator.ports.token_verifier import (
    InvalidTokenError,
    MissingScopeError,
    TokenVerifierUnavailableError,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

RequestHandler = Callable[[Request], Awaitable[Response]]
_DOCUMENTATION_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})
_READ_OPERATIONS = frozenset(
    {
        ("POST", "/v1/agent/investigations/status"),
        ("POST", "/v1/agent/investigations/reports"),
    }
)
_REQUEST_POLL_PATH = re.compile(r"^/v1/agent/investigations/[^/]+$")


def _error(*, status_code: int, code: str, message: str) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


def _required_scope(request: Request) -> str:
    if request.url.path in _DOCUMENTATION_PATHS:
        return "investigations/read"
    if (request.method, request.url.path) in _READ_OPERATIONS:
        return "investigations/read"
    if request.method == "GET" and _REQUEST_POLL_PATH.fullmatch(request.url.path):
        return "investigations/read"
    return "investigations/write"


class AuthenticationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestHandler) -> Response:
        if not (request.url.path.startswith("/v1") or request.url.path in _DOCUMENTATION_PATHS):
            return await call_next(request)

        authorization = request.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token.strip():
            return _error(
                status_code=401,
                code="invalid_token",
                message="A valid bearer token is required",
            )

        runtime = cast(RuntimeState, request.app.state.runtime)
        try:
            claims = await runtime.token_verifier.verify(
                token.strip(),
                required_scope=_required_scope(request),
            )
        except MissingScopeError:
            return _error(
                status_code=403,
                code="insufficient_scope",
                message="The bearer token lacks the required scope",
            )
        except InvalidTokenError:
            return _error(
                status_code=401,
                code="invalid_token",
                message="Bearer token validation failed",
            )
        except TokenVerifierUnavailableError:
            return _error(
                status_code=503,
                code="authentication_unavailable",
                message="Token verification is temporarily unavailable",
            )

        request.state.client_id = claims.client_id
        request.state.token_scopes = claims.scopes
        return await call_next(request)
