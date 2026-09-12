"""Explicit OpenTelemetry lifecycle and structured logging configuration."""

from platform_observability.logging import (
    LoggingConfig,
    LoggingConfigurationError,
    configure_logging,
)
from platform_observability.providers import (
    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    ObservabilityError,
    ProviderOwnershipError,
    Providers,
    ShutdownFailure,
    TelemetryConfig,
    TelemetryConfigurationError,
    TelemetryLifecycleClosedError,
    configure_observability,
    shutdown_observability,
)

__all__ = [
    "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS",
    "LoggingConfig",
    "LoggingConfigurationError",
    "ObservabilityError",
    "ProviderOwnershipError",
    "Providers",
    "ShutdownFailure",
    "TelemetryConfig",
    "TelemetryConfigurationError",
    "TelemetryLifecycleClosedError",
    "configure_logging",
    "configure_observability",
    "shutdown_observability",
]
