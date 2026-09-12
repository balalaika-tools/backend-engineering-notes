"""In-process health and readiness behavior."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi.testclient import TestClient
from orchestrator.bootstrap.app import RuntimeFactory, create_app
from orchestrator.ports.token_verifier import TokenClaims


class AcceptingVerifier:
    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        del token, required_scope
        return TokenClaims(client_id="test", scopes=frozenset())


@dataclass
class FakeRuntime:
    database_available: bool
    publisher_alive: bool
    token_verifier: AcceptingVerifier = AcceptingVerifier()

    async def database_ready(self) -> bool:
        return self.database_available

    def publisher_ready(self) -> bool:
        return self.publisher_alive


def _runtime_factory(
    database_available: bool,
    *,
    publisher_alive: bool = True,
) -> RuntimeFactory:
    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeRuntime]:
        yield FakeRuntime(
            database_available=database_available,
            publisher_alive=publisher_alive,
        )

    return factory


def test_health_is_live_without_business_work() -> None:
    with TestClient(create_app(_runtime_factory(database_available=False))) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_database_health() -> None:
    with TestClient(create_app(_runtime_factory(database_available=True))) as client:
        ready = client.get("/ready")
    with TestClient(create_app(_runtime_factory(database_available=False))) as client:
        unavailable = client.get("/ready")

    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "outbox_publisher": "ok"}
    assert unavailable.status_code == 503
    assert unavailable.json()["checks"] == {
        "database": "failed",
        "outbox_publisher": "ok",
    }


def test_readiness_fails_when_publisher_task_is_dead() -> None:
    with TestClient(
        create_app(_runtime_factory(database_available=True, publisher_alive=False))
    ) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {
        "database": "ok",
        "outbox_publisher": "failed",
    }
