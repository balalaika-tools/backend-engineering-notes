"""Bounded platform, admission, and GenAI metrics."""

from typing import TYPE_CHECKING, Any

from opentelemetry import metrics
from opentelemetry.metrics import Observation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

meter = metrics.get_meter(__name__)
investigation_duration = meter.create_histogram("investigation_duration_seconds", unit="s")
investigations = meter.create_counter("investigation_total", unit="{investigation}")
investigations_by_status = meter.create_gauge("investigations_by_status", unit="{investigation}")
stale_processing = meter.create_gauge("stale_processing_count", unit="{investigation}")
max_deliver_exhausted = meter.create_counter("max_deliver_exhausted_total", unit="{investigation}")
outbox_oldest_age = meter.create_gauge("outbox_oldest_pending_age_seconds", unit="s")
worker_actual_inflight = meter.create_gauge("worker_actual_inflight", unit="{investigation}")
worker_target = meter.create_gauge("worker_target", unit="{investigation}")
worker_in_cooldown = meter.create_gauge("worker_in_cooldown")
llm_attempts = meter.create_counter("llm_attempts_total", unit="{attempt}")
llm_window_failure_rate = meter.create_gauge("llm_window_failure_rate")
ctc_api_requests = meter.create_counter("ctc_api_requests_total", unit="{request}")
model_duration = meter.create_histogram("gen_ai.client.operation.duration", unit="s")
token_usage = meter.create_histogram("gen_ai.client.token.usage", unit="{token}")
tool_duration = meter.create_histogram("gen_ai.execute_tool.duration", unit="s")
agent_duration = meter.create_histogram("gen_ai.invoke_agent.duration", unit="s")


def register_ctc_pool_metric(engine: "AsyncEngine") -> Any:
    """Observe checked-out CTC connections without adding query-path instrumentation."""

    def observe(_options: Any) -> list[Observation]:
        checkedout = getattr(engine.sync_engine.pool, "checkedout", None)
        value = int(checkedout()) if callable(checkedout) else 0
        return [Observation(value)]

    return meter.create_observable_gauge(
        "ctc_db_pool_in_use",
        callbacks=[observe],
        unit="{connection}",
    )


def initialize_metrics() -> None:
    """Publish a meaningful zero baseline before the first investigation arrives."""
    investigations.add(0, {"status": "completed", "error_code": "_NONE"})
    for status in ("queued", "processing", "completed", "failed"):
        investigations_by_status.set(0, {"status": status})
    stale_processing.set(0)
    max_deliver_exhausted.add(0)
    outbox_oldest_age.set(0)
    worker_actual_inflight.set(0)
    worker_in_cooldown.set(0)
    llm_attempts.add(0, {"result": "success"})
    llm_attempts.add(0, {"result": "error"})
    llm_window_failure_rate.set(0)
    ctc_api_requests.add(0, {"endpoint": "other", "status": "no_requests"})
