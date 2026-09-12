"""Focused verification of the durable HTTP-to-worker trace shape."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from nats.js.client import JetStreamContext
from opentelemetry import trace
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from orchestrator.adapters.nats_publisher import NatsJetStreamPublisher
from worker.adapters.nats.handler import NatsInvestigationHandler
from worker.domain.investigation import InvestigationRequested


class CapturingJetStream:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}

    async def publish(self, _subject: str, _data: bytes, **kwargs: Any) -> None:
        self.headers = dict(kwargs["headers"])


class Message:
    def __init__(self, carrier: dict[str, str]) -> None:
        self.trace_carrier = carrier
        self.event = InvestigationRequested(
            event_id=uuid.uuid4(),
            event_type="investigation.requested",
            occurred_at=datetime.now(UTC),
            investigation_id=uuid.uuid4(),
            request_id=uuid.uuid4(),
            client_id="test-client",
            exception_id="EX-1",
            traceparent=carrier["traceparent"],
        )
        self.acked = False

    async def ack(self) -> None:
        self.acked = True

    async def nak(self, *, delay_seconds: float | None = None) -> None:
        raise AssertionError(f"unexpected nak: {delay_seconds}")

    async def term(self) -> None:
        raise AssertionError("unexpected term")

    async def in_progress(self) -> None:
        pass


class DeliveryState:
    async def claim(self, *_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(attempt_count=1)

    async def get(self, *_args: Any, **_kwargs: Any) -> Any:
        return None

    async def extend_lease(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def release_for_retry(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    async def fail(self, *_args: Any, **_kwargs: Any) -> bool:
        return True


class TracedAction:
    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    async def execute(self, **_kwargs: Any) -> None:
        with self._tracer.start_as_current_span("invoke_agent exception_analysis"):
            with self._tracer.start_as_current_span("write CTC comment"):
                pass


@pytest.mark.asyncio
async def test_outbox_header_continues_one_trace_through_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("trace-propagation-test")
    monkeypatch.setattr(trace, "get_tracer", lambda *_args, **_kwargs: tracer)
    jetstream = CapturingJetStream()
    publisher = NatsJetStreamPublisher(cast(JetStreamContext, cast(Any, jetstream)))

    with tracer.start_as_current_span("accept investigation"):
        payload: dict[str, object] = {}
        inject(cast(dict[str, str], payload))
        await publisher.publish(
            subject="investigations.requested",
            payload=payload,
            message_id=uuid.uuid4(),
        )

    message = Message(jetstream.headers)
    handler = NatsInvestigationHandler(
        action=TracedAction(tracer),
        delivery_state=DeliveryState(),
        failure_recorder=cast(Any, object()),
        worker_id="worker-test",
        lease_seconds=90,
        heartbeat_interval_seconds=3600,
        retry_delays_seconds=(60, 120, 300),
    )
    await handler.handle(message)
    provider.shutdown()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    expected = {
        "accept investigation",
        "send investigations.requested",
        "process investigations.requested",
        "claim investigation",
        "invoke_agent exception_analysis",
        "write CTC comment",
    }
    assert expected <= spans.keys()
    assert message.acked
    assert len({spans[name].context.trace_id for name in expected}) == 1
    assert (
        spans["send investigations.requested"].parent.span_id
        == spans["accept investigation"].context.span_id
    )
    assert (
        spans["process investigations.requested"].parent.span_id
        == spans["send investigations.requested"].context.span_id
    )
    assert (
        spans["claim investigation"].parent.span_id
        == spans["process investigations.requested"].context.span_id
    )
    assert (
        spans["invoke_agent exception_analysis"].parent.span_id
        == spans["process investigations.requested"].context.span_id
    )
    assert (
        spans["write CTC comment"].parent.span_id
        == spans["invoke_agent exception_analysis"].context.span_id
    )
    assert all(not span.events for span in spans.values())
