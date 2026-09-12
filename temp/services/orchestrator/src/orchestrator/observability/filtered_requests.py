"""Bounded selection signals; no database exceptions or row payloads are rendered."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from time import perf_counter

import structlog
from opentelemetry import metrics, trace
from opentelemetry.trace import StatusCode

from orchestrator.ports.exception_candidates import (
    CandidateSourceUnavailableError,
    FilteredSelection,
)
from orchestrator.ports.investigation_store import InvestigationStoreUnavailableError

tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)
requests = meter.create_counter("app.filtered.requests", unit="{request}")
selected_exceptions = meter.create_counter("app.filtered.selected_exceptions", unit="{exception}")
duration = meter.create_histogram(
    "app.filtered.selection.duration",
    unit="s",
    explicit_bucket_boundaries_advisory=(0.01, 0.05, 0.1, 0.5, 1, 5, 10, 30, 60),
)
ctc_failures = meter.create_counter("app.filtered.ctc_failures", unit="{failure}")
log = structlog.get_logger(__name__)


@dataclass(slots=True)
class SelectionTelemetry:
    stage: str = "ctc_selection"
    scanned_rows: int = 0
    pages_read: int = 0
    history_exclusions: int = 0
    selected_count: int = 0
    attached_count: int = 0
    created_count: int = 0
    request_id: str | None = None


@contextmanager
def selection_operation(selection: FilteredSelection) -> Iterator[SelectionTelemetry]:
    stats = SelectionTelemetry()
    started = perf_counter()
    outcome, error_code = "zero_match", "_NONE"
    context = {
        "created_at_gte_supplied": selection.created_at_gte is not None,
        "created_at_gte": selection.created_at_gte.isoformat()
        if selection.created_at_gte
        else None,
        "requested_maximum": selection.max_exceptions,
        "schema": selection.schema,
        "exception_name": selection.exception_name,
    }
    with tracer.start_as_current_span(
        "select filtered investigations", record_exception=False, set_status_on_exception=False
    ) as span:
        try:
            yield stats
            outcome = "selected" if stats.selected_count else "zero_match"
        except Exception as exc:
            outcome = "failed"
            error_code = (
                "ctc_database_unavailable"
                if isinstance(exc, CandidateSourceUnavailableError)
                else "database_unavailable"
                if isinstance(exc, InvestigationStoreUnavailableError)
                else "internal_error"
            )
            span.set_status(StatusCode.ERROR)
            span.set_attribute("error.type", error_code)
            # Selection's explicit privacy contract permits bounded fields only, including on failure.
            log.error("filtered_request_failed", **context, **asdict(stats), error_code=error_code)
            if isinstance(exc, CandidateSourceUnavailableError):
                ctc_failures.add(1, {"error.type": error_code})
            raise
        finally:
            elapsed = perf_counter() - started
            labels = {"app.outcome": outcome, "error.type": error_code}
            requests.add(1, labels)
            selected_exceptions.add(stats.selected_count, labels)
            duration.record(elapsed, labels)
            span.set_attribute("app.outcome", outcome)
            span.set_attribute("app.filtered.selected_count", stats.selected_count)
            span.set_attribute("app.filtered.scanned_rows", stats.scanned_rows)
            if stats.request_id:
                span.set_attribute("app.request.id", stats.request_id)
            if outcome != "failed":
                log.info(
                    "filtered_request_completed",
                    **context,
                    **asdict(stats),
                    duration_seconds=elapsed,
                )
