"""HTTP contract for configuration reset failures."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi.testclient import TestClient
from orchestrator.api.dependencies import get_reset_configuration
from orchestrator.application.reset_configuration import ConfigurationReset
from orchestrator.bootstrap.app import create_app
from orchestrator.bootstrap.runtime import RuntimeState
from orchestrator.ports.manual_store import ManualStoreUnavailableError
from orchestrator.ports.token_verifier import TokenClaims, TokenVerifier


class AcceptingVerifier:
    async def verify(self, token: str, *, required_scope: str) -> TokenClaims:
        del token
        return TokenClaims(client_id="operator", scopes=frozenset({required_scope}))


@dataclass
class FakeRuntime:
    token_verifier: TokenVerifier

    async def database_ready(self) -> bool:
        return True

    def publisher_ready(self) -> bool:
        return True


class UnavailableManualStoreAction:
    async def execute(self, *, purge_manuals: bool) -> ConfigurationReset:
        assert purge_manuals is True
        raise ManualStoreUnavailableError("S3 unavailable")


class CapturingResetAction:
    def __init__(self) -> None:
        self.purge_values: list[bool] = []

    async def execute(self, *, purge_manuals: bool) -> ConfigurationReset:
        self.purge_values.append(purge_manuals)
        return ConfigurationReset(
            generation=len(self.purge_values),
            manuals_deleted=0,
        )


def test_manual_purge_failure_uses_stable_503_contract() -> None:
    @asynccontextmanager
    async def runtime_factory() -> AsyncIterator[RuntimeState]:
        yield FakeRuntime(token_verifier=AcceptingVerifier())

    app = create_app(runtime_factory)
    app.dependency_overrides[get_reset_configuration] = lambda: UnavailableManualStoreAction()

    with TestClient(app) as client:
        response = client.post(
            "/v1/control-context/refresh",
            json={"purge_manuals": True},
            headers={"Authorization": "Bearer valid"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_unavailable"


def test_refresh_supports_absent_body_and_manual_purge() -> None:
    @asynccontextmanager
    async def runtime_factory() -> AsyncIterator[RuntimeState]:
        yield FakeRuntime(token_verifier=AcceptingVerifier())

    action = CapturingResetAction()
    app = create_app(runtime_factory)
    app.dependency_overrides[get_reset_configuration] = lambda: action

    with TestClient(app) as client:
        without_body = client.post(
            "/v1/control-context/refresh",
            headers={"Authorization": "Bearer valid"},
        )
        with_purge = client.post(
            "/v1/control-context/refresh",
            json={"purge_manuals": True},
            headers={"Authorization": "Bearer valid"},
        )

    assert without_body.json() == {"generation": 1}
    assert with_purge.json() == {"generation": 2}
    assert action.purge_values == [False, True]
