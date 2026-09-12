"""Structured JSON logging with trace correlation and credential redaction."""

from __future__ import annotations

import logging
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TextIO

import structlog
from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.trace import format_span_id, format_trace_id

_SENSITIVE_KEY = re.compile(
    r"(?:^|[._-])(?:authorization|cookie|password|secret|access_token|refresh_token)(?:$|[._-])",
    re.I,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:bearer\s+|(?:authorization|password|secret|access_token|refresh_token)\s*[=:]\s*)[^\s,;]+"
)
_EXPORT_LOG_PREFIXES = ("opentelemetry.", "urllib3.")
_LOCK = threading.RLock()


class LoggingConfigurationError(RuntimeError):
    """Raised when structured logging is reconfigured incompatibly."""


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Resolved logging inputs owned by a consuming service."""

    service_name: str
    log_level: str
    full_exception_trace: bool = True

    def __post_init__(self) -> None:
        normalized = self.log_level.upper()
        if normalized not in logging.getLevelNamesMapping():
            raise ValueError(f"unknown log level: {self.log_level}")
        object.__setattr__(self, "log_level", normalized)


@dataclass(frozen=True, slots=True)
class _LoggingSetup:
    config: LoggingConfig
    stream: TextIO
    logger_provider: LoggerProvider | None


_active_setup: _LoggingSetup | None = None


class _ExcludeExporterLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(_EXPORT_LOG_PREFIXES)


def _trace_context(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        event["trace_id"] = format_trace_id(context.trace_id)
        event["span_id"] = format_span_id(context.span_id)
    return event


def _service(name: str) -> Callable[[Any, str, dict[str, Any]], dict[str, Any]]:
    def processor(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
        event["service.name"] = name
        return event

    return processor


def _exception_policy(
    include_full: bool,
) -> Callable[[Any, str, dict[str, Any]], dict[str, Any]]:
    def processor(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
        if not include_full and event.pop("exc_info", None):
            event["exception.message"] = "Exception details masked by policy"
            event["app.error.stacktrace_included"] = False
        return event

    return processor


def _rename_stacktrace(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
    rendered = event.pop("exception", None)
    if rendered:
        event["exception.stacktrace"] = rendered
        event["app.error.stacktrace_included"] = True
    return event


def _redact(_logger: Any, _method: str, event: dict[str, Any]) -> dict[str, Any]:
    for key, value in tuple(event.items()):
        if _SENSITIVE_KEY.search(key):
            event[key] = "[REDACTED]"
        elif isinstance(value, str):
            event[key] = _SENSITIVE_TEXT.sub("[REDACTED]", value)
    return event


def configure_logging(
    config: LoggingConfig,
    *,
    logger_provider: LoggerProvider | None = None,
    stream: TextIO | None = None,
) -> None:
    """Configure one JSON stdout route and an optional non-recursive OTLP route."""

    global _active_setup
    resolved_stream = stream if stream is not None else sys.stdout
    requested = _LoggingSetup(config, resolved_stream, logger_provider)
    with _LOCK:
        if _active_setup is not None:
            if requested == _active_setup:
                return
            raise LoggingConfigurationError(
                "structured logging is already active with different configuration"
            )
        shared: list[Any] = [
            structlog.stdlib.add_log_level,
            structlog.stdlib.ExtraAdder(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _service(config.service_name),
            _trace_context,
            _exception_policy(config.full_exception_trace),
            structlog.processors.format_exc_info,
            _rename_stacktrace,
            _redact,
        ]
        formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
        stdout = logging.StreamHandler(resolved_stream)
        stdout.setLevel(config.log_level)
        stdout.setFormatter(formatter)
        handlers: list[logging.Handler] = [stdout]
        if logger_provider is not None:
            otlp = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
            otlp.setLevel(config.log_level)
            otlp.addFilter(_ExcludeExporterLogs())
            otlp.setFormatter(formatter)
            handlers.append(otlp)
        logging.basicConfig(level=config.log_level, handlers=handlers, force=True)
        structlog.configure(
            processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        _active_setup = requested
