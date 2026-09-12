"""Orchestrator mapping to the shared observability API."""

from types import SimpleNamespace
from typing import cast

import pytest
from orchestrator.bootstrap import observability as bootstrap
from orchestrator.config.settings import Settings
from platform_observability import LoggingConfig, Providers, TelemetryConfig


def _settings(**overrides: object) -> Settings:
    values = {
        "ENVIRONMENT_NAME": "local",
        "CTC_DATABASE_URL": "postgresql+asyncpg://ctc_reader@localhost/ctc",
        "CTC_RECONCILIATION_SCHEMA": "positions",
        "PLATFORM_DATABASE_URL": "postgresql+asyncpg://platform@localhost/platform",
        "NATS_URL": "nats://localhost:4222",
        "S3_BUCKET": "reports",
        "COGNITO_ISSUER": "https://issuer.example.com",
        "COGNITO_AUDIENCE": "investigations",
        "SERVICE_INSTANCE_ID": "orchestrator-1",
    }
    values.update(overrides)
    return Settings(**values)


def test_maps_resolved_settings_and_supports_disabled_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry: list[TelemetryConfig] = []
    logging: list[tuple[LoggingConfig, object | None]] = []
    monkeypatch.setattr(
        bootstrap.platform_observability,
        "configure_observability",
        lambda config: telemetry.append(config) or None,
    )
    monkeypatch.setattr(
        bootstrap.platform_observability,
        "configure_logging",
        lambda config, *, logger_provider=None: logging.append((config, logger_provider)),
    )

    settings = _settings()
    assert bootstrap.configure_observability(settings) is None
    assert telemetry == [
        TelemetryConfig(
            "orchestrator",
            "exception-investigation",
            "unknown",
            "orchestrator-1",
            "local",
            None,
            15_000,
        )
    ]
    assert logging == [(LoggingConfig("orchestrator", settings.log_level, True), None)]


def test_logging_failure_closes_acquired_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = cast(Providers, SimpleNamespace(logger=object()))
    shutdown_calls: list[bool] = []
    monkeypatch.setattr(
        bootstrap.platform_observability, "configure_observability", lambda _config: provider
    )
    monkeypatch.setattr(
        bootstrap.platform_observability,
        "configure_logging",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("logging failed")),
    )
    monkeypatch.setattr(
        bootstrap.platform_observability,
        "shutdown_observability",
        lambda: shutdown_calls.append(True),
    )

    with pytest.raises(RuntimeError, match="logging failed"):
        bootstrap.configure_observability(_settings(OTLP_ENDPOINT="http://collector:4318"))

    assert shutdown_calls == [True]
