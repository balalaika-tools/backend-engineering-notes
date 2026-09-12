"""Exported selection signals retain bounded labels and safe correlated context."""

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from orchestrator.observability import filtered_requests as telemetry
from orchestrator.ports.exception_candidates import (
    CandidateSourceUnavailableError,
    FilteredSelection,
)
from structlog.testing import capture_logs


def test_success_zero_and_failure_export_safe_signals(monkeypatch) -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    spans = InMemorySpanExporter()
    traces = TracerProvider()
    traces.add_span_processor(SimpleSpanProcessor(spans))
    monkeypatch.setattr(telemetry, "tracer", traces.get_tracer("test"))
    for name, instrument in [
        ("requests", meter.create_counter("app.filtered.requests")),
        ("selected_exceptions", meter.create_counter("app.filtered.selected_exceptions")),
        ("duration", meter.create_histogram("app.filtered.selection.duration")),
        ("ctc_failures", meter.create_counter("app.filtered.ctc_failures")),
    ]:
        monkeypatch.setattr(telemetry, name, instrument)
    with capture_logs() as logs:
        with telemetry.selection_operation(
            FilteredSelection(None, 2, "positions", "Break")
        ) as stats:
            stats.request_id = "request-one"
            stats.pages_read = 2
            stats.scanned_rows = 4
            stats.history_exclusions = 2
            stats.selected_count = 2
            stats.created_count = 1
            stats.attached_count = 1
        with telemetry.selection_operation(FilteredSelection(None, 1, "positions", "Break")):
            pass
        with pytest.raises(CandidateSourceUnavailableError):
            with telemetry.selection_operation(FilteredSelection(None, 1, "positions", "Break")):
                raise CandidateSourceUnavailableError(
                    "postgresql://reader:credential-canary@db/ctc row-payload-canary"
                )
    assert len(logs) == 3
    assert logs[0]["selected_count"] == 2
    assert logs[0]["pages_read"] == 2
    assert logs[1]["selected_count"] == 0
    assert logs[2]["stage"] == "ctc_selection"
    assert logs[2]["error_code"] == "ctc_database_unavailable"
    assert "credential-canary" not in str(logs)
    assert "row-payload-canary" not in str(logs)
    data = reader.get_metrics_data()
    outcomes = set()
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                for point in metric.data.data_points:
                    assert set(point.attributes) <= {"app.outcome", "error.type"}
                    if "app.outcome" in point.attributes:
                        outcomes.add(point.attributes["app.outcome"])
    assert outcomes == {"selected", "zero_match", "failed"}
    exported = spans.get_finished_spans()
    assert len(exported) == 3
    assert exported[-1].status.is_ok is False
    assert all(not span.events for span in exported)
    assert "credential-canary" not in str([span.attributes for span in exported])
    provider.shutdown()
    traces.shutdown()
