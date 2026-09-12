"""Shared structured logging behavior."""

from __future__ import annotations

import io
import json
import logging

import pytest
import structlog
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from platform_observability import LoggingConfig, LoggingConfigurationError, configure_logging
from platform_observability.logging import (
    _exception_policy,
    _ExcludeExporterLogs,
    _redact,
    _rename_stacktrace,
    _trace_context,
)


def test_redaction_masks_structured_and_embedded_credentials() -> None:
    event = _redact(
        None,
        "error",
        {
            "authorization": "Bearer canary-secret",
            "event": "request failed password=hunter2",
            "input_tokens": 42,
        },
    )

    assert event["authorization"] == "[REDACTED]"
    assert "hunter2" not in event["event"]
    assert event["input_tokens"] == 42


def test_masked_exception_policy_removes_raw_exception() -> None:
    event = _exception_policy(False)(None, "error", {"exc_info": RuntimeError("secret")})

    assert "exc_info" not in event
    assert event["exception.message"] == "Exception details masked by policy"
    assert event["app.error.stacktrace_included"] is False


def test_rendered_exception_uses_the_established_stacktrace_field() -> None:
    event = _rename_stacktrace(None, "error", {"exception": "Traceback: boom"})

    assert event["exception.stacktrace"] == "Traceback: boom"
    assert event["app.error.stacktrace_included"] is True


def test_trace_context_adds_fixed_width_ids() -> None:
    tracer = TracerProvider(resource=Resource({}), shutdown_on_exit=False).get_tracer("test")
    with tracer.start_as_current_span("job"):
        event = _trace_context(None, "info", {})

    assert len(event["trace_id"]) == 32
    assert len(event["span_id"]) == 16


def test_otlp_handler_excludes_its_own_transport_logs() -> None:
    filter_ = _ExcludeExporterLogs()

    assert not filter_.filter(logging.LogRecord("opentelemetry.exporter", 20, "", 1, "x", (), None))
    assert not filter_.filter(logging.LogRecord("urllib3.connectionpool", 20, "", 1, "x", (), None))
    assert filter_.filter(logging.LogRecord("worker.application", 20, "", 1, "x", (), None))


def test_equivalent_configuration_delivers_one_json_record() -> None:
    stream = io.StringIO()
    config = LoggingConfig("worker", "INFO", True)
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider(resource=Resource({}), shutdown_on_exit=False)
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    configure_logging(config, stream=stream, logger_provider=provider)
    configure_logging(config, stream=stream, logger_provider=provider)

    structlog.get_logger("worker.test").info("job_completed", authorization="secret")

    records = stream.getvalue().splitlines()
    assert len(records) == 1
    event = json.loads(records[0])
    assert event["event"] == "job_completed"
    assert event["service.name"] == "worker"
    assert event["authorization"] == "[REDACTED]"
    assert len(exporter.get_finished_logs()) == 1
    handlers = tuple(logging.getLogger().handlers)

    with pytest.raises(LoggingConfigurationError):
        configure_logging(
            LoggingConfig("orchestrator", "INFO", True),
            stream=stream,
            logger_provider=provider,
        )

    assert tuple(logging.getLogger().handlers) == handlers
    provider.shutdown()
