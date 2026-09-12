"""HTTP contracts for investigation batch commands."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from orchestrator.api.dependencies import (
    get_request_filtered_investigations,
    get_request_investigations,
)
from orchestrator.application.request_investigations import (
    BatchTooLargeError,
    InvestigationBatch,
    RequestedInvestigation,
)
from orchestrator.bootstrap.app import RuntimeFactory, create_app
from orchestrator.bootstrap.runtime import RuntimeState
from orchestrator.domain.investigation import ExternalInvestigationStatus
from orchestrator.ports.investigation_store import InvestigationStoreUnavailableError
from orchestrator.ports.token_verifier import TokenClaims, TokenVerifier

REQUEST_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
INVESTIGATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


class AcceptingVerifier:
    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        del token
        return TokenClaims(client_id="trusted-client", scopes=frozenset({required_scope}))


@dataclass
class FakeRuntime:
    token_verifier: TokenVerifier

    async def database_ready(self) -> bool:
        return True

    def publisher_ready(self) -> bool:
        return True


class AcceptingAction:
    async def execute(
        self,
        *,
        client_id: str,
        exception_ids: list[str],
        traceparent: str,
    ) -> InvestigationBatch:
        assert client_id == "trusted-client"
        assert exception_ids == ["EX-1", "EX-1"]
        assert traceparent == "00-test-trace"
        return InvestigationBatch(
            request_id=REQUEST_ID,
            investigations=(
                RequestedInvestigation(
                    investigation_id=INVESTIGATION_ID,
                    exception_id="EX-1",
                    status=ExternalInvestigationStatus.IN_PROGRESS,
                    created=True,
                ),
            ),
        )


class RejectingAction:
    async def execute(
        self,
        *,
        client_id: str,
        exception_ids: list[str],
        traceparent: str,
    ) -> InvestigationBatch:
        del client_id, exception_ids, traceparent
        raise BatchTooLargeError(1)


class UnavailableAction:
    async def execute(self, **_values: object) -> InvestigationBatch:
        raise InvestigationStoreUnavailableError("database unavailable")


def _runtime_factory() -> RuntimeFactory:
    @asynccontextmanager
    async def runtime_factory() -> AsyncIterator[RuntimeState]:
        yield FakeRuntime(token_verifier=AcceptingVerifier())

    return runtime_factory


def _client(action: object) -> TestClient:
    app = create_app(_runtime_factory())
    app.dependency_overrides[get_request_investigations] = lambda: action
    app.dependency_overrides[get_request_filtered_investigations] = lambda: action
    return TestClient(app)


def test_trigger_returns_accepted_batch_contract() -> None:
    with _client(AcceptingAction()) as client:
        response = client.post(
            "/v1/agent/investigate",
            json={"exception_ids": ["EX-1", "EX-1"]},
            headers={
                "Authorization": "Bearer valid",
                "traceparent": "00-test-trace",
            },
        )

    assert response.status_code == 202
    assert response.json() == {
        "request_id": str(REQUEST_ID),
        "selected_count": 1,
        "status_url": f"/v1/agent/investigations/{REQUEST_ID}",
        "investigations": [
            {
                "investigation_id": str(INVESTIGATION_ID),
                "exception_id": "EX-1",
                "status": "in_progress",
                "created": True,
            }
        ],
    }


def test_trigger_maps_batch_limit_failure_to_stable_error() -> None:
    with _client(RejectingAction()) as client:
        response = client.post(
            "/v1/agent/investigate",
            json={"exception_ids": ["EX-1", "EX-2"]},
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "batch_too_large",
            "message": "Batch exceeds the configured limit of 1",
        }
    }


def test_request_schema_failure_uses_422_error_envelope() -> None:
    with _client(AcceptingAction()) as client:
        response = client.post(
            "/v1/agent/investigate",
            json={"exception_ids": [123]},
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "The request body is invalid",
        }
    }


def test_database_failure_uses_retryable_503_error() -> None:
    with _client(UnavailableAction()) as client:
        response = client.post(
            "/v1/agent/investigate",
            json={"exception_ids": ["EX-1"]},
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"


def test_application_exposes_exactly_the_canonical_business_routes() -> None:
    app = create_app(_runtime_factory())
    business_routes = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/v1/")
        for method in operations
    }

    assert business_routes == {
        ("POST", "/v1/agent/investigate"),
        ("GET", "/v1/agent/investigations/{request_id}"),
        ("POST", "/v1/agent/investigations/status"),
        ("POST", "/v1/agent/investigations/reports"),
        ("POST", "/v1/control-context/refresh"),
    }


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {},
        {"json": None},
        {"json": {"exception_ids": None}},
        {"json": {"exception_ids": ["EX-1"], "max_exceptions": None}},
        {"json": {"exception_ids": ["EX-1"], "created_at_gte": None}},
        {"json": {"unexpected": "field"}},
        {"json": {"max_exceptions": None}},
    ],
)
def test_invalid_submission_shapes_never_dispatch(request_kwargs: dict[str, object]) -> None:
    class UnexpectedAction:
        async def execute(self, **_values: object) -> InvestigationBatch:
            raise AssertionError("invalid submission dispatched durable work")

    with _client(UnexpectedAction()) as client:
        if "json" in request_kwargs:
            response = client.post(
                "/v1/agent/investigate",
                headers={"Authorization": "Bearer valid"},
                json=request_kwargs["json"],
            )
        else:
            response = client.post(
                "/v1/agent/investigate",
                headers={"Authorization": "Bearer valid"},
            )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
