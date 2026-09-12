"""Orchestrator bootstrap owns action composition and resource disposal."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from orchestrator.application.fetch_reports import FetchReports
from orchestrator.application.query_status import QueryInvestigationRequestStatus, QueryStatus
from orchestrator.application.request_investigations import RequestInvestigations
from orchestrator.application.reset_configuration import ResetConfiguration
from orchestrator.bootstrap import runtime as runtime_module
from orchestrator.config.secrets import Secrets
from orchestrator.config.settings import Settings
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession


class FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class FakeHttpClient:
    def __init__(self, **_kwargs: object) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakePublisher:
    def __init__(self, _url: str) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeSupervisor:
    def __init__(self, action: object, *, poll_interval_seconds: float) -> None:
        self.action = action
        self.poll_interval_seconds = poll_interval_seconds
        self.started = False
        self.stopped = False
        self.ready = False

    def start(self) -> None:
        self.started = True
        self.ready = True

    async def stop(self) -> None:
        self.stopped = True
        self.ready = False


def _settings() -> Settings:
    return Settings(
        ENVIRONMENT_NAME="local",
        CTC_DATABASE_URL="postgresql+asyncpg://ctc_reader@localhost/ctc",
        CTC_RECONCILIATION_SCHEMA="positions",
        PLATFORM_DATABASE_URL="postgresql+asyncpg://platform@localhost/platform",
        NATS_URL="nats://localhost:4222",
        S3_BUCKET="reports",
        COGNITO_ISSUER="https://issuer.example.com",
        COGNITO_AUDIENCE="investigations",
    )


@pytest.mark.asyncio
async def test_runtime_composes_actions_and_disposes_owned_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    ctc_engine = FakeEngine()
    http_client = FakeHttpClient()
    publisher = FakePublisher("nats://unused")
    shutdown_calls: list[bool] = []
    session_factory = cast(async_sessionmaker[AsyncSession], MagicMock(return_value=AsyncMock()))

    monkeypatch.setattr(runtime_module.ctc_database, "build_engine", lambda **_kwargs: ctc_engine)
    monkeypatch.setattr(runtime_module, "build_engine", lambda **_kwargs: engine)
    monkeypatch.setattr(runtime_module, "build_session_factory", lambda _engine: session_factory)
    monkeypatch.setattr(runtime_module.httpx, "AsyncClient", lambda **_kwargs: http_client)
    monkeypatch.setattr(runtime_module, "JwksTokenVerifier", lambda **_kwargs: cast(Any, object()))
    monkeypatch.setattr(runtime_module, "S3ManualStore", lambda **_kwargs: cast(Any, object()))
    monkeypatch.setattr(runtime_module, "NatsEventPublisher", lambda _url, **_kwargs: publisher)
    monkeypatch.setattr(runtime_module, "OutboxPublisherSupervisor", FakeSupervisor)
    monkeypatch.setattr(
        runtime_module, "shutdown_observability", lambda: shutdown_calls.append(True)
    )

    async with runtime_module.runtime(
        _settings(),
        Secrets(
            CTC_DB_PASSWORD="ctc-secret",
            PLATFORM_DB_PASSWORD="secret",
            NATS_AUTH_TOKEN="nats-secret",
        ),
    ) as value:
        assert isinstance(value.actions.request_investigations, RequestInvestigations)
        assert isinstance(value.actions.query_status, QueryStatus)
        assert isinstance(value.actions.query_request_status, QueryInvestigationRequestStatus)
        assert isinstance(value.actions.fetch_reports, FetchReports)
        assert isinstance(value.actions.reset_configuration, ResetConfiguration)
        supervisor = cast(FakeSupervisor, value._publisher_supervisor)
        assert supervisor.started is True
        assert await value.database_ready() is True

    assert supervisor.stopped is True
    assert publisher.closed is True
    assert http_client.closed is True
    assert engine.disposed is True
    assert ctc_engine.disposed is True
    assert shutdown_calls == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["ctc_engine", "http_client", "composition"])
async def test_partial_startup_disposes_every_constructed_pool(monkeypatch, stage) -> None:
    platform, ctc = FakeEngine(), FakeEngine()
    http = FakeHttpClient()

    def fail(*args, **kwargs):
        raise RuntimeError("construction failed")

    monkeypatch.setattr(runtime_module, "build_engine", lambda **kwargs: platform)
    monkeypatch.setattr(
        runtime_module.ctc_database,
        "build_engine",
        fail if stage == "ctc_engine" else lambda **kwargs: ctc,
    )
    monkeypatch.setattr(
        runtime_module.httpx,
        "AsyncClient",
        fail if stage == "http_client" else lambda **kwargs: http,
    )
    monkeypatch.setattr(runtime_module, "_compose_runtime", fail)
    monkeypatch.setattr(runtime_module, "shutdown_observability", lambda: None)
    with pytest.raises(RuntimeError, match="construction failed"):
        async with runtime_module.runtime(
            _settings(),
            Secrets(CTC_DB_PASSWORD="ctc", PLATFORM_DB_PASSWORD="platform", NATS_AUTH_TOKEN="nats"),
        ):
            pytest.fail("Startup must fail")
    assert platform.disposed
    assert ctc.disposed == (stage != "ctc_engine")
    assert http.closed == (stage == "composition")
