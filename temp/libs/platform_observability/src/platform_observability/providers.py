"""Process-wide OpenTelemetry providers with an explicit, bounded lifecycle."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider, set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON

DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_DIAGNOSTIC_LOG = logging.getLogger("opentelemetry.platform_observability")
_LOCK = threading.RLock()


class ObservabilityError(RuntimeError):
    """Base error for explicit observability configuration failures."""


class TelemetryConfigurationError(ObservabilityError):
    """Raised when active telemetry is configured with different inputs."""


class ProviderOwnershipError(TelemetryConfigurationError):
    """Raised when another owner has already registered an SDK provider."""


class TelemetryLifecycleClosedError(ObservabilityError):
    """Raised when telemetry is initialized after its lifecycle was closed."""


@dataclass(frozen=True, slots=True)
class TelemetryConfig:
    """Resolved provider inputs owned by a consuming service."""

    service_name: str
    service_namespace: str
    service_version: str
    service_instance_id: str
    environment_name: str
    endpoint: str | None
    export_interval_millis: int = 15_000

    def __post_init__(self) -> None:
        endpoint = self.endpoint.rstrip("/") if self.endpoint else None
        object.__setattr__(self, "endpoint", endpoint)
        if self.export_interval_millis <= 0:
            raise ValueError("export_interval_millis must be positive")


@dataclass(frozen=True, slots=True)
class Providers:
    """The three enabled signal providers owned by this process."""

    tracer: TracerProvider
    meter: MeterProvider
    logger: LoggerProvider


@dataclass(frozen=True, slots=True)
class ShutdownFailure:
    """A cleanup failure surfaced without raising over application work."""

    signal: str
    detail: str


_active_config: TelemetryConfig | None = None
_providers: Providers | None = None
_closed = False
_shutdown_failures: tuple[ShutdownFailure, ...] = ()


def _is_default_proxy(provider: object) -> bool:
    return type(provider).__name__ in {
        "ProxyTracerProvider",
        "_ProxyMeterProvider",
        "ProxyLoggerProvider",
    }


def _assert_provider_ownership_available() -> None:
    current = {
        "trace": trace.get_tracer_provider(),
        "metrics": metrics.get_meter_provider(),
        "logs": get_logger_provider(),
    }
    conflicts = [name for name, provider in current.items() if not _is_default_proxy(provider)]
    if conflicts:
        joined = ", ".join(conflicts)
        raise ProviderOwnershipError(f"OpenTelemetry provider already owned for: {joined}")


def _resource(config: TelemetryConfig) -> Resource:
    # Resource.create consults OTEL_RESOURCE_ATTRIBUTES. The plain constructor
    # keeps the library's explicit inputs authoritative.
    return Resource(
        {
            "service.namespace": config.service_namespace,
            "service.name": config.service_name,
            "service.version": config.service_version,
            "service.instance.id": config.service_instance_id,
            "deployment.environment.name": config.environment_name,
        }
    )


def _build_providers(config: TelemetryConfig) -> Providers:
    assert config.endpoint is not None
    resource = _resource(config)
    constructed: list[tuple[str, object]] = []
    try:
        tracer = TracerProvider(resource=resource, sampler=ALWAYS_ON, shutdown_on_exit=False)
        constructed.append(("traces", tracer))
        tracer.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{config.endpoint}/v1/traces"))
        )
        meter = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{config.endpoint}/v1/metrics"),
                    export_interval_millis=config.export_interval_millis,
                )
            ],
            shutdown_on_exit=False,
        )
        constructed.append(("metrics", meter))
        logger = LoggerProvider(resource=resource, shutdown_on_exit=False)
        constructed.append(("logs", logger))
        logger.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{config.endpoint}/v1/logs"))
        )
    except BaseException:
        _shutdown_objects(constructed, timeout_seconds=DEFAULT_SHUTDOWN_TIMEOUT_SECONDS)
        raise
    return Providers(tracer=tracer, meter=meter, logger=logger)


def _shutdown_handles(
    providers: Providers,
    *,
    timeout_seconds: float,
) -> tuple[ShutdownFailure, ...]:
    return _shutdown_objects(
        [
            ("logs", providers.logger),
            ("traces", providers.tracer),
            ("metrics", providers.meter),
        ],
        timeout_seconds=timeout_seconds,
    )


def _shutdown_objects(
    objects: list[tuple[str, object]],
    *,
    timeout_seconds: float,
) -> tuple[ShutdownFailure, ...]:
    failures: list[ShutdownFailure] = []
    failures_lock = threading.Lock()
    deadline = time.monotonic() + max(0.0, timeout_seconds)

    def dispose(signal: str, provider: object) -> None:
        try:
            provider.shutdown()  # type: ignore[attr-defined]
        except BaseException as exc:
            failure = ShutdownFailure(signal, f"{type(exc).__name__}: {exc}")
            with failures_lock:
                failures.append(failure)
            _DIAGNOSTIC_LOG.warning("telemetry_shutdown_failed", extra={"signal": signal})

    threads = [
        threading.Thread(
            target=dispose,
            args=(signal, provider),
            name=f"otel-{signal}-shutdown",
            daemon=True,
        )
        for signal, provider in objects
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    for thread in threads:
        if thread.is_alive():
            signal = thread.name.removeprefix("otel-").removesuffix("-shutdown")
            failures.append(ShutdownFailure(signal, "cleanup exceeded the total deadline"))
            _DIAGNOSTIC_LOG.warning("telemetry_shutdown_timed_out", extra={"signal": signal})
    return tuple(failures)


def configure_observability(config: TelemetryConfig) -> Providers | None:
    """Register providers once, or return ``None`` when export is disabled."""

    global _active_config, _closed, _providers
    with _LOCK:
        if _closed:
            raise TelemetryLifecycleClosedError(
                "OpenTelemetry lifecycle is closed; restart the process to initialize again"
            )
        if _providers is not None:
            if config == _active_config:
                return _providers
            raise TelemetryConfigurationError(
                "OpenTelemetry is already active with different configuration"
            )
        if config.endpoint is None:
            return None
        _assert_provider_ownership_available()
        built: Providers | None = None
        try:
            built = _build_providers(config)
            trace.set_tracer_provider(built.tracer)
            metrics.set_meter_provider(built.meter)
            set_logger_provider(built.logger)
            if (
                trace.get_tracer_provider() is not built.tracer
                or metrics.get_meter_provider() is not built.meter
                or get_logger_provider() is not built.logger
            ):
                _closed = True
                raise ProviderOwnershipError(
                    "OpenTelemetry provider ownership changed during initialization"
                )
        except BaseException:
            if built is not None:
                _shutdown_handles(built, timeout_seconds=DEFAULT_SHUTDOWN_TIMEOUT_SECONDS)
            raise
        _active_config = config
        _providers = built
        return built


def shutdown_observability(
    *, timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
) -> tuple[ShutdownFailure, ...]:
    """Dispose every signal at most once within one shared total time budget."""

    global _closed, _providers, _shutdown_failures
    with _LOCK:
        if _providers is None:
            return _shutdown_failures
        providers, _providers = _providers, None
        _closed = True
    failures = _shutdown_handles(providers, timeout_seconds=timeout_seconds)
    with _LOCK:
        _shutdown_failures = failures
    return failures
