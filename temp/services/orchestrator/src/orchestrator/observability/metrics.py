"""Transactional-outbox metrics."""

from opentelemetry import metrics

meter = metrics.get_meter(__name__)
outbox_pending = meter.create_gauge("outbox_pending_count", unit="{event}")
outbox_oldest_age = meter.create_gauge("outbox_oldest_pending_age_seconds", unit="s")
outbox_publish = meter.create_counter("outbox_publish_total", unit="{event}")
outbox_publisher_consecutive_failures = meter.create_gauge(
    "outbox_publisher_consecutive_failures",
    unit="{failure}",
)


def initialize_metrics() -> None:
    """Publish the empty-outbox baseline before the first publisher poll."""
    outbox_pending.set(0)
    outbox_oldest_age.set(0)
    outbox_publish.add(0, {"result": "published"})
    outbox_publish.add(0, {"result": "failed"})
    outbox_publisher_consecutive_failures.set(0)
