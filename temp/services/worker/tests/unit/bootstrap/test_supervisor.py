"""Heartbeat and bounded worker shutdown behavior."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from worker.adapters.nats.delivery import InvestigationMessage
from worker.application.reconcile import ReconciliationResult
from worker.application.record_investigation_failure import FailureOutcome, RecordedFailure
from worker.bootstrap import supervisor as supervisor_module
from worker.bootstrap.supervisor import AdmissionSupervisor, ReconcilerSupervisor
from worker.domain.admission import AdmissionState
from worker.domain.investigation import InvestigationRequested
from worker.ports.reconciliation_store import RetentionDeletes


def _event() -> InvestigationRequested:
    return InvestigationRequested(
        event_id=uuid.uuid4(),
        event_type="investigation.requested",
        occurred_at=datetime.now(UTC),
        investigation_id=uuid.uuid4(),
        request_id=uuid.uuid4(),
        client_id="client",
        exception_id="EX-1",
        traceparent=None,
    )


@dataclass
class FakeMessage:
    event: InvestigationRequested = field(default_factory=_event)
    progress_count: int = 0
    nak_delays: list[float | None] = field(default_factory=list)

    @property
    def trace_carrier(self) -> dict[str, str]:
        return {}

    async def ack(self) -> None:
        return None

    async def nak(self, *, delay_seconds: float | None = None) -> None:
        self.nak_delays.append(delay_seconds)

    async def term(self) -> None:
        return None

    async def in_progress(self) -> None:
        self.progress_count += 1


@dataclass
class FakeInterruptionRecorder:
    released: list[uuid.UUID] = field(default_factory=list)

    async def release_interrupted(
        self,
        *,
        investigation_id: uuid.UUID,
        worker_id: str,
    ) -> RecordedFailure:
        assert worker_id == "worker-a"
        self.released.append(investigation_id)
        return RecordedFailure(FailureOutcome.RETRY, 1)


class InactiveCooldown:
    async def activate(self, *, ttl_seconds: float) -> bool:
        del ttl_seconds
        return True

    async def is_active(self) -> bool:
        return False


class OneMessageConsumer:
    def __init__(self, message: FakeMessage) -> None:
        self._message = message
        self._fetched = False

    async def ensure(self) -> None:
        return None

    async def fetch_one(self, *, timeout_seconds: float) -> InvestigationMessage | None:
        if not self._fetched:
            self._fetched = True
            return self._message
        await asyncio.sleep(timeout_seconds)
        return None


@pytest.mark.asyncio
async def test_drain_timeout_naks_message_and_releases_lease() -> None:
    message = FakeMessage()
    interruption_recorder = FakeInterruptionRecorder()
    handler_started = asyncio.Event()

    async def handler(_message: InvestigationMessage) -> None:
        handler_started.set()
        await asyncio.Event().wait()

    stop = asyncio.Event()
    supervisor = AdmissionSupervisor(
        consumer=OneMessageConsumer(message),
        cooldown=InactiveCooldown(),
        state=AdmissionState(initial_target=1, max_per_instance=1),
        handler=handler,
        fetch_timeout_seconds=0.001,
        interruption_recorder=interruption_recorder,
        worker_id="worker-a",
        shutdown_grace_seconds=0.001,
    )
    task = asyncio.create_task(supervisor.run(stop))
    await asyncio.wait_for(handler_started.wait(), timeout=1)

    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert interruption_recorder.released == [message.event.investigation_id]
    assert message.nak_delays == [0]


def _reconciliation_result(*, exhausted: int = 0) -> ReconciliationResult:
    return ReconciliationResult(
        stale_processing=0,
        oldest_unpublished_age_seconds=None,
        max_deliver_exhausted=exhausted,
        retention_deletes=RetentionDeletes(),
        investigations_by_status={},
        investigation_results=(),
    )


@pytest.mark.asyncio
async def test_reconciler_runs_injected_pass_periodically_until_stopped() -> None:
    stop = asyncio.Event()
    calls = 0

    async def reconcile_once() -> ReconciliationResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            stop.set()
        return _reconciliation_result()

    await asyncio.wait_for(
        ReconcilerSupervisor(reconcile_once, interval_seconds=0).run(stop),
        timeout=1,
    )

    assert calls == 2


@pytest.mark.asyncio
async def test_reconciler_supervises_pass_error_and_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    stop = asyncio.Event()
    calls = 0

    async def reconcile_once() -> ReconciliationResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database unavailable")
        stop.set()
        return _reconciliation_result()

    await asyncio.wait_for(
        ReconcilerSupervisor(reconcile_once, interval_seconds=0).run(stop),
        timeout=1,
    )

    assert calls == 2
    assert "Reconciliation pass failed" in caplog.text


class RecordingMetric:
    def __init__(self) -> None:
        self.values: list[tuple[float, object | None]] = []

    def set(self, value: float, attributes: object | None = None) -> None:
        self.values.append((value, attributes))

    def add(self, value: float, attributes: object | None = None) -> None:
        self.values.append((value, attributes))


@pytest.mark.asyncio
async def test_reconciler_reports_committed_pass_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = asyncio.Event()
    stale_metric = RecordingMetric()
    status_metric = RecordingMetric()
    exhausted_metric = RecordingMetric()
    outbox_metric = RecordingMetric()
    monkeypatch.setattr(supervisor_module, "stale_processing", stale_metric)
    monkeypatch.setattr(supervisor_module, "investigations_by_status", status_metric)
    monkeypatch.setattr(supervisor_module, "max_deliver_exhausted", exhausted_metric)
    monkeypatch.setattr(supervisor_module, "outbox_oldest_age", outbox_metric)

    async def reconcile_once() -> ReconciliationResult:
        stop.set()
        return ReconciliationResult(
            stale_processing=2,
            oldest_unpublished_age_seconds=360,
            max_deliver_exhausted=1,
            retention_deletes=RetentionDeletes(),
            investigations_by_status={"queued": 3},
            investigation_results=(),
        )

    await ReconcilerSupervisor(reconcile_once, interval_seconds=60).run(stop)

    assert stale_metric.values == [(2, None)]
    assert status_metric.values == [(3, {"status": "queued"})]
    assert exhausted_metric.values == [(1, None)]
    assert outbox_metric.values == [(360, None)]
