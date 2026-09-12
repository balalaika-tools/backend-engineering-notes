"""HTTP contract for polling an accepted investigation request."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from orchestrator.api.dependencies import (
    get_query_investigation_request_status,
    get_request_filtered_investigations,
    get_request_investigations,
)
from orchestrator.application.query_status import (
    InvestigationRequestNotFoundError,
    InvestigationRequestStatus,
    InvestigationStatusItem,
    RequestStatusSummary,
)
from orchestrator.application.request_filtered_investigations import BulkLimitExceededError
from orchestrator.bootstrap.app import create_app
from orchestrator.bootstrap.runtime import RuntimeState
from orchestrator.domain.investigation import ExternalInvestigationStatus
from orchestrator.ports.exception_candidates import CandidateSourceUnavailableError
from orchestrator.ports.token_verifier import TokenClaims, TokenVerifier

REQUEST_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")
INVESTIGATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000020")


@dataclass
class RecordingVerifier:
    required_scopes: list[str] = field(default_factory=list)

    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        assert token == "valid"
        self.required_scopes.append(required_scope)
        return TokenClaims(client_id="trusted-client", scopes=frozenset({required_scope}))


@dataclass
class FakeRuntime:
    token_verifier: TokenVerifier

    async def database_ready(self) -> bool:
        return True

    def publisher_ready(self) -> bool:
        return True


class FoundAction:
    async def execute(self, *, request_id: uuid.UUID, client_id: str) -> InvestigationRequestStatus:
        assert request_id == REQUEST_ID
        assert client_id == "trusted-client"
        return InvestigationRequestStatus(
            request_id=request_id,
            status="completed",
            summary=RequestStatusSummary(total=1, in_progress=0, completed=1, failed=0),
            investigations=(
                InvestigationStatusItem(
                    investigation_id=INVESTIGATION_ID,
                    exception_id="EX-1",
                    status=ExternalInvestigationStatus.COMPLETED,
                    attempt_count=1,
                    created_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
                    started_at=datetime(2026, 9, 8, 10, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 9, 8, 10, 2, tzinfo=UTC),
                    last_error_code=None,
                ),
            ),
        )


class MissingAction:
    async def execute(self, *, request_id: uuid.UUID, client_id: str) -> InvestigationRequestStatus:
        del request_id, client_id
        raise InvestigationRequestNotFoundError


def _client(action: object, verifier: RecordingVerifier) -> TestClient:
    @asynccontextmanager
    async def runtime_factory() -> AsyncIterator[RuntimeState]:
        yield FakeRuntime(token_verifier=verifier)

    app = create_app(runtime_factory)
    app.dependency_overrides[get_query_investigation_request_status] = lambda: action
    app.dependency_overrides[get_request_filtered_investigations] = lambda: action
    app.dependency_overrides[get_request_investigations] = lambda: action
    return TestClient(app)


def test_request_status_returns_batch_contract_with_read_scope() -> None:
    verifier = RecordingVerifier()
    with _client(FoundAction(), verifier) as client:
        response = client.get(
            f"/v1/agent/investigations/{REQUEST_ID}",
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "request_id": str(REQUEST_ID),
        "status": "completed",
        "summary": {"total": 1, "in_progress": 0, "completed": 1, "failed": 0},
        "investigations": [
            {
                "investigation_id": str(INVESTIGATION_ID),
                "exception_id": "EX-1",
                "status": "completed",
                "attempt_count": 1,
                "created_at": "2026-09-08T10:00:00Z",
                "started_at": "2026-09-08T10:01:00Z",
                "completed_at": "2026-09-08T10:02:00Z",
                "last_error_code": None,
            }
        ],
    }
    assert verifier.required_scopes == ["investigations/read"]


def test_unknown_or_foreign_request_uses_stable_not_found_error() -> None:
    verifier = RecordingVerifier()
    with _client(MissingAction(), verifier) as client:
        response = client.get(
            f"/v1/agent/investigations/{REQUEST_ID}",
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "request_not_found",
            "message": "Investigation request was not found",
        }
    }


class FilteredAction:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.error = error

    async def execute(self, **kwargs):
        from orchestrator.application.request_investigations import InvestigationBatch

        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return InvestigationBatch(request_id=REQUEST_ID, investigations=())


def test_filtered_defaults_accept_zero_with_write_scope_and_status_uri() -> None:
    action, verifier = FilteredAction(), RecordingVerifier()
    with _client(action, verifier) as client:
        response = client.post(
            "/v1/agent/investigate",
            json={},
            headers={"Authorization": "Bearer valid"},
        )
    assert response.status_code == 202
    assert response.json() == {
        "request_id": str(REQUEST_ID),
        "selected_count": 0,
        "status_url": f"/v1/agent/investigations/{REQUEST_ID}",
        "investigations": [],
    }
    assert verifier.required_scopes == ["investigations/write"]
    assert action.calls[0]["max_exceptions"] == 1
    assert action.calls[0]["created_at_gte"] is None
    assert action.calls[0]["client_id"] == "trusted-client"


@pytest.mark.parametrize(
    "body",
    [
        {"created_at_gte": "2026-09-11T00:00:00"},
        {"max_exceptions": 0},
        {"max_exceptions": -1},
        {"max_exceptions": True},
        {"max_exceptions": 1.5},
    ],
)
def test_filtered_invalid_inputs_do_not_call_action(body) -> None:
    action = FilteredAction()
    with _client(action, RecordingVerifier()) as client:
        response = client.post(
            "/v1/agent/investigate",
            json=body,
            headers={"Authorization": "Bearer valid"},
        )
    assert response.status_code == 422
    assert action.calls == []


def test_filtered_timezone_offset_is_preserved_as_an_instant() -> None:
    action = FilteredAction()
    with _client(action, RecordingVerifier()) as client:
        response = client.post(
            "/v1/agent/investigate",
            json={"created_at_gte": "2026-09-11T03:00:00+03:00"},
            headers={"Authorization": "Bearer valid"},
        )
    assert response.status_code == 202
    assert action.calls[0]["created_at_gte"] == datetime(2026, 9, 11, tzinfo=UTC)


@pytest.mark.parametrize(
    "error,expected_status,code",
    [
        (BulkLimitExceededError("Limit is 100"), 400, "bulk_limit_exceeded"),
        (
            CandidateSourceUnavailableError("sensitive database details"),
            503,
            "ctc_database_unavailable",
        ),
    ],
)
def test_filtered_failure_contract(error, expected_status, code) -> None:
    with _client(FilteredAction(error), RecordingVerifier()) as client:
        response = client.post(
            "/v1/agent/investigate",
            json={},
            headers={"Authorization": "Bearer valid"},
        )
    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code
    assert "sensitive" not in response.text
