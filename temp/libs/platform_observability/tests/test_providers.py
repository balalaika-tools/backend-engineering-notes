"""Public provider lifecycle and isolation contracts."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from platform_observability.providers import Providers, _shutdown_handles

REPOSITORY = Path(__file__).parents[3]
LIBRARY_SOURCE = REPOSITORY / "libs/platform_observability/src"


def _run_isolated(source: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(LIBRARY_SOURCE)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_import_is_inert_and_does_not_load_service_configuration() -> None:
    result = _run_isolated(
        """
        import logging
        import sys
        from opentelemetry import metrics, trace
        from opentelemetry._logs import get_logger_provider

        before = (trace.get_tracer_provider(), metrics.get_meter_provider(), get_logger_provider())
        handlers = tuple(logging.getLogger().handlers)
        import platform_observability
        after = (trace.get_tracer_provider(), metrics.get_meter_provider(), get_logger_provider())
        assert all(left is right for left, right in zip(before, after))
        assert tuple(logging.getLogger().handlers) == handlers
        assert "worker.config.settings" not in sys.modules
        assert "orchestrator.config.settings" not in sys.modules
        """
    )

    assert result.returncode == 0, result.stderr


def test_disabled_repeated_conflicting_and_closed_lifecycle() -> None:
    result = _run_isolated(
        """
        from platform_observability import (
            TelemetryConfig,
            TelemetryConfigurationError,
            TelemetryLifecycleClosedError,
            configure_observability,
            shutdown_observability,
        )

        def config(endpoint, name="worker"):
            return TelemetryConfig(
                service_name=name,
                service_namespace="exception-investigation",
                service_version="test",
                service_instance_id="instance-1",
                environment_name="local",
                endpoint=endpoint,
                export_interval_millis=60_000,
            )

        assert configure_observability(config(None)) is None
        first = configure_observability(config("http://127.0.0.1:9/"))
        assert first is configure_observability(config("http://127.0.0.1:9"))
        try:
            configure_observability(config("http://127.0.0.1:9", name="other"))
        except TelemetryConfigurationError:
            pass
        else:
            raise AssertionError("conflicting configuration was accepted")
        assert shutdown_observability(timeout_seconds=1) == ()
        assert shutdown_observability(timeout_seconds=1) == ()
        try:
            configure_observability(config("http://127.0.0.1:9"))
        except TelemetryLifecycleClosedError:
            pass
        else:
            raise AssertionError("closed lifecycle restarted")
        """
    )

    assert result.returncode == 0, result.stderr


def test_incompatible_existing_provider_is_rejected_without_replacement() -> None:
    result = _run_isolated(
        """
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from platform_observability import ProviderOwnershipError, TelemetryConfig
        from platform_observability import configure_observability

        existing = TracerProvider(shutdown_on_exit=False)
        trace.set_tracer_provider(existing)
        config = TelemetryConfig("worker", "namespace", "test", "one", "local", "http://x")
        try:
            configure_observability(config)
        except ProviderOwnershipError:
            pass
        else:
            raise AssertionError("incompatible provider ownership was accepted")
        assert trace.get_tracer_provider() is existing
        existing.shutdown()
        """
    )

    assert result.returncode == 0, result.stderr


def test_explicit_resource_ignores_ambient_service_identity() -> None:
    result = _run_isolated(
        """
        import os
        os.environ["OTEL_RESOURCE_ATTRIBUTES"] = "service.name=ambient"
        from platform_observability import TelemetryConfig, configure_observability
        from platform_observability import shutdown_observability

        config = TelemetryConfig("worker", "namespace", "test", "one", "local", "http://x")
        providers = configure_observability(config)
        assert providers is not None
        assert providers.tracer.resource.attributes["service.name"] == "worker"
        shutdown_observability(timeout_seconds=1)
        """
    )

    assert result.returncode == 0, result.stderr


def test_partial_registration_failure_disposes_every_constructed_signal() -> None:
    result = _run_isolated(
        """
        from platform_observability import TelemetryConfig
        import platform_observability.providers as module

        class Handle:
            def __init__(self): self.closed = False
            def shutdown(self): self.closed = True

        handles = [Handle(), Handle(), Handle()]
        built = module.Providers(*handles)
        module._build_providers = lambda _config: built
        module.metrics.set_meter_provider = lambda _provider: (_ for _ in ()).throw(RuntimeError("boom"))
        config = TelemetryConfig("worker", "namespace", "test", "one", "local", "http://x")
        try:
            module.configure_observability(config)
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("partial registration failure was hidden")
        assert all(handle.closed for handle in handles)
        """
    )

    assert result.returncode == 0, result.stderr


class _Handle:
    def __init__(self, *, failure: Exception | None = None, delay: float = 0) -> None:
        self.failure = failure
        self.delay = delay
        self.calls = 0

    def shutdown(self) -> None:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.failure:
            raise self.failure


def test_shutdown_is_best_effort_and_bounded_by_one_total_deadline() -> None:
    blocked = _Handle(delay=0.5)
    failed = _Handle(failure=RuntimeError("export failed"))
    healthy = _Handle()
    providers = Providers(blocked, failed, healthy)  # type: ignore[arg-type]

    started = time.monotonic()
    failures = _shutdown_handles(providers, timeout_seconds=0.05)
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    assert blocked.calls == failed.calls == healthy.calls == 1
    assert {(failure.signal, failure.detail) for failure in failures} >= {
        ("traces", "cleanup exceeded the total deadline"),
        ("metrics", "RuntimeError: export failed"),
    }
