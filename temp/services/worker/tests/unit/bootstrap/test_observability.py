"""Worker mapping to the shared observability API."""

from types import SimpleNamespace
from typing import cast

import pytest
from platform_observability import LoggingConfig, Providers, TelemetryConfig
from worker.bootstrap import observability as bootstrap
from worker.config.settings import Settings


def _settings() -> Settings:
    return cast(
        Settings,
        SimpleNamespace(
            service_namespace="exception-investigation",
            service_version="test-version",
            service_instance_id=None,
            environment_name="local",
            otlp_endpoint=None,
            otel_export_interval_millis=15_000,
            log_level="INFO",
            log_full_exception_trace=True,
        ),
    )


def test_maps_worker_identity_and_logging_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_settings = _settings()
    configured = cast(Providers, SimpleNamespace(logger=object()))
    telemetry: list[TelemetryConfig] = []
    logging: list[LoggingConfig] = []
    monkeypatch.setattr(
        bootstrap.platform_observability,
        "configure_observability",
        lambda config: telemetry.append(config) or configured,
    )
    monkeypatch.setattr(
        bootstrap.platform_observability,
        "configure_logging",
        lambda config, *, logger_provider=None: logging.append(config),
    )
    monkeypatch.setattr(bootstrap.socket, "gethostname", lambda: "worker-host")

    result = bootstrap.configure_observability(worker_settings)

    assert result is configured
    assert telemetry[0].service_name == "worker"
    assert telemetry[0].service_instance_id == (
        worker_settings.service_instance_id or "worker-host"
    )
    assert telemetry[0].endpoint == (
        str(worker_settings.otlp_endpoint) if worker_settings.otlp_endpoint else None
    )
    assert logging == [
        LoggingConfig(
            "worker",
            worker_settings.log_level,
            worker_settings.log_full_exception_trace,
        )
    ]


def test_worker_logging_failure_closes_acquired_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_settings = _settings()
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
        bootstrap.configure_observability(worker_settings)

    assert shutdown_calls == [True]
