"""HTTP contract tests for investigation report selectors."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from orchestrator.api.dependencies import get_fetch_reports
from orchestrator.application.fetch_reports import FetchReports, InvestigationReportBatch
from orchestrator.bootstrap.app import RuntimeFactory, create_app
from orchestrator.bootstrap.runtime import RuntimeState
from orchestrator.ports.token_verifier import TokenClaims, TokenVerifier

INVESTIGATION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


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


class CapturingAction:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str] | None, list[uuid.UUID] | None]] = []

    async def execute(
        self,
        *,
        exception_ids: list[str] | None,
        investigation_ids: list[uuid.UUID] | None,
    ) -> InvestigationReportBatch:
        self.calls.append((exception_ids, investigation_ids))
        return InvestigationReportBatch(reports=())


class UnusedUnitOfWork:
    async def __aenter__(self) -> "UnusedUnitOfWork":
        raise AssertionError("Invalid queries must fail before database access")

    async def __aexit__(self, *_args: object) -> None:
        return None


def _runtime_factory() -> RuntimeFactory:
    @asynccontextmanager
    async def runtime_factory() -> AsyncIterator[RuntimeState]:
        yield FakeRuntime(token_verifier=AcceptingVerifier())

    return runtime_factory


def _client(action: object) -> TestClient:
    app = create_app(_runtime_factory())
    app.dependency_overrides[get_fetch_reports] = lambda: action
    return TestClient(app)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"exception_ids": ["EX-1"]}, (["EX-1"], None)),
        ({"investigation_ids": [str(INVESTIGATION_ID)]}, (None, [INVESTIGATION_ID])),
    ],
)
def test_reports_accepts_either_selector(
    payload: dict[str, list],
    expected: tuple[list[str] | None, list[uuid.UUID] | None],
) -> None:
    action = CapturingAction()

    with _client(action) as client:
        response = client.post(
            "/v1/agent/investigations/reports",
            json=payload,
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 200
    assert response.json() == {"reports": []}
    assert action.calls == [expected]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"exception_ids": ["EX-1"], "investigation_ids": [str(INVESTIGATION_ID)]},
    ],
)
def test_reports_requires_exactly_one_selector(payload: dict[str, object]) -> None:
    action = FetchReports(
        unit_of_work_factory=UnusedUnitOfWork,
        max_batch_size=10,
        inline_limit_bytes=256_000,
    )

    with _client(action) as client:
        response = client.post(
            "/v1/agent/investigations/reports",
            json=payload,
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_batch",
            "message": "Provide exactly one of exception_ids or investigation_ids",
        }
    }
