"""Authentication middleware response contracts."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from orchestrator.api.middleware.authentication import AuthenticationMiddleware
from orchestrator.bootstrap.app import RuntimeFactory, create_app
from orchestrator.ports.token_verifier import (
    InvalidTokenError,
    MissingScopeError,
    TokenClaims,
    TokenVerifier,
)


class FakeVerifier:
    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        if token == "invalid":
            raise InvalidTokenError
        if token == "missing-scope":
            raise MissingScopeError
        return TokenClaims(client_id="client-123", scopes=frozenset({required_scope}))


@dataclass
class ScopeVerifier:
    scopes: frozenset[str]

    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        del token
        if required_scope not in self.scopes:
            raise MissingScopeError
        return TokenClaims(client_id="client-123", scopes=self.scopes)


@dataclass
class FakeRuntime:
    token_verifier: TokenVerifier

    async def database_ready(self) -> bool:
        return True

    def publisher_ready(self) -> bool:
        return True


def _runtime_factory() -> RuntimeFactory:
    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeRuntime]:
        yield FakeRuntime(token_verifier=FakeVerifier())

    return factory


def _client() -> TestClient:
    app = create_app(_runtime_factory())

    @app.post("/v1/authentication-probe/status")
    async def protected(request: Request) -> dict[str, str]:
        return {"client_id": str(request.state.client_id)}

    return TestClient(app)


def test_missing_and_invalid_tokens_return_401_error_body() -> None:
    with _client() as client:
        missing = client.post("/v1/authentication-probe/status")
        invalid = client.post(
            "/v1/authentication-probe/status",
            headers={"Authorization": "Bearer invalid"},
        )

    assert missing.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    assert missing.json()["error"]["code"] == "invalid_token"
    assert invalid.status_code == 401
    assert invalid.headers["WWW-Authenticate"] == "Bearer"
    assert invalid.json()["error"]["code"] == "invalid_token"


def test_missing_scope_returns_403_error_body() -> None:
    with _client() as client:
        response = client.post(
            "/v1/authentication-probe/status",
            headers={"Authorization": "Bearer missing-scope"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_valid_token_exposes_trusted_client_id() -> None:
    with _client() as client:
        response = client.post(
            "/v1/authentication-probe/status",
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 200
    assert response.json() == {"client_id": "client-123"}


def test_api_documentation_requires_a_read_token() -> None:
    with _client() as client:
        missing = client.get("/openapi.json")
        authorized = client.get(
            "/openapi.json",
            headers={"Authorization": "Bearer valid"},
        )

    assert missing.status_code == 401
    assert authorized.status_code == 200


def test_filtered_route_rejects_missing_token_and_scope() -> None:
    with _client() as client:
        missing = client.post("/v1/agent/investigate", json={})
        wrong_scope = client.post(
            "/v1/agent/investigate",
            json={},
            headers={"Authorization": "Bearer missing-scope"},
        )
    assert missing.status_code == 401
    assert wrong_scope.status_code == 403


def _scope_client(scopes: frozenset[str]) -> TestClient:
    app = FastAPI()
    app.state.runtime = FakeRuntime(token_verifier=ScopeVerifier(scopes))

    @app.post("/v1/agent/investigate")
    async def investigate_probe() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/v1/agent/investigations/{request_id}")
    async def poll_probe(request_id: str) -> dict[str, str]:
        return {"request_id": request_id}

    @app.post("/v1/agent/investigations/status")
    @app.post("/v1/agent/investigations/reports")
    async def read_probe() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/v1/control-context/refresh")
    async def refresh_probe() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(AuthenticationMiddleware)
    return TestClient(app)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/agent/investigations/request-id"),
        ("POST", "/v1/agent/investigations/status"),
        ("POST", "/v1/agent/investigations/reports"),
    ],
)
def test_read_only_token_is_limited_to_the_three_read_operations(
    method: str,
    path: str,
) -> None:
    with _scope_client(frozenset({"investigations/read"})) as client:
        allowed = client.request(method, path, headers={"Authorization": "Bearer token"})
        submission = client.post(
            "/v1/agent/investigate", headers={"Authorization": "Bearer token"}
        )
        refresh = client.post(
            "/v1/control-context/refresh", headers={"Authorization": "Bearer token"}
        )

    assert allowed.status_code == 200
    assert submission.status_code == 403
    assert refresh.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/v1/agent/investigations/request-id",
        "/v1/agent/investigations/status",
        "/v1/agent/investigations/reports",
    ],
)
def test_write_only_token_is_limited_to_submission_and_refresh(path: str) -> None:
    method = "GET" if path.endswith("request-id") else "POST"
    with _scope_client(frozenset({"investigations/write"})) as client:
        denied = client.request(method, path, headers={"Authorization": "Bearer token"})
        submission = client.post(
            "/v1/agent/investigate", headers={"Authorization": "Bearer token"}
        )
        refresh = client.post(
            "/v1/control-context/refresh", headers={"Authorization": "Bearer token"}
        )

    assert denied.status_code == 403
    assert submission.status_code == 200
    assert refresh.status_code == 200
