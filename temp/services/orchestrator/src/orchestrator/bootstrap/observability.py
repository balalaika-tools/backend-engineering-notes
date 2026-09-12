"""Map resolved orchestrator settings to the shared observability contract."""

import socket

import platform_observability
from orchestrator.config.settings import Settings


def configure_observability(settings: Settings) -> platform_observability.Providers | None:
    """Configure shared providers and logging from resolved service settings."""

    providers = platform_observability.configure_observability(
        platform_observability.TelemetryConfig(
            service_name="orchestrator",
            service_namespace=settings.service_namespace,
            service_version=settings.service_version,
            service_instance_id=settings.service_instance_id or socket.gethostname(),
            environment_name=settings.environment_name,
            endpoint=str(settings.otlp_endpoint) if settings.otlp_endpoint else None,
            export_interval_millis=settings.otel_export_interval_millis,
        )
    )
    try:
        platform_observability.configure_logging(
            platform_observability.LoggingConfig(
                service_name="orchestrator",
                log_level=settings.log_level,
                full_exception_trace=settings.log_full_exception_trace,
            ),
            logger_provider=providers.logger if providers else None,
        )
    except BaseException:
        platform_observability.shutdown_observability()
        raise
    return providers
