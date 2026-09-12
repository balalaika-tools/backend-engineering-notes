"""JetStream acknowledgement policy around durable investigation ownership."""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from worker.adapters.nats.handler import NatsInvestigationHandler
from worker.application.record_investigation_failure import (
    ClassifiedInvestigationFailure,
    FailureOutcome,
    RecordedFailure,
)
from worker.domain.investigation import (
    InvestigationRequested,
    InvestigationState,
    InvestigationStatus,
    PermanentInvestigationError,
    TransientInvestigationError,
)

NOW = datetime(2026, 9, 7, 12, tzinfo=UTC)
INVESTIGATION_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _state(
    *,
    status: InvestigationStatus = InvestigationStatus.PROCESSING,
    attempts: int = 1,
    worker_id: str | None = "worker-a",
    lease_expires_at: datetime | None = None,
) -> InvestigationState:
    return InvestigationState(
        id=INVESTIGATION_ID,
        request_id=uuid.uuid4(),
        exception_id="EX-1",
        status=status,
        attempt_count=attempts,
        worker_id=worker_id,
        lease_expires_at=lease_expires_at or NOW + timedelta(seconds=90),
        last_heartbeat_at=NOW,
        started_at=NOW,
        completed_at=None,
        last_error_code=None,
        analysis=None,
        analysis_persisted_at=None,
        report=None,
        report_uri=None,
        comment_outcome=None,
        comment_detail=None,
        codes_outcome=None,
        codes_detail=None,
    )


class FakeMessage:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._event = InvestigationRequested(
            event_id=uuid.uuid4(),
            event_type="investigation.requested",
            occurred_at=NOW,
            investigation_id=INVESTIGATION_ID,
            request_id=uuid.uuid4(),
            client_id="client",
            exception_id="EX-1",
            traceparent=None,
        )
        self.nak_delays: list[float | None] = []

    @property
    def event(self) -> InvestigationRequested:
        return self._event

    @property
    def trace_carrier(self) -> dict[str, str]:
        return {}

    async def ack(self) -> None:
        self._events.append("ack")

    async def nak(self, *, delay_seconds: float | None = None) -> None:
        self._events.append("nak")
        self.nak_delays.append(delay_seconds)

    async def term(self) -> None:
        self._events.append("term")

    async def in_progress(self) -> None:
        self._events.append("heartbeat")


class FakeAction:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.wait_forever = False
        self.cancelled = False
        self.started = asyncio.Event()

    async def execute(self, **_values: object) -> None:
        self.events.append("action")
        self.started.set()
        if self.wait_forever:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True
        if self.error is not None:
            raise self.error
        self.events.append("committed")


class FakeDeliveryState:
    def __init__(self, state: InvestigationState | None) -> None:
        self.state = state
        self.claim_error: Exception | None = None
        self.extend_result = True
        self.extend_error_once: Exception | None = None

    async def get(self, _investigation_id: uuid.UUID) -> InvestigationState | None:
        return self.state

    async def claim(
        self,
        _investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> InvestigationState | None:
        del worker_id, lease_seconds
        if self.claim_error is not None:
            raise self.claim_error
        if self.state is None or self.state.status is not InvestigationStatus.QUEUED:
            return None
        self.state = replace(
            self.state,
            status=InvestigationStatus.PROCESSING,
            worker_id="worker-a",
            attempt_count=self.state.attempt_count + 1,
        )
        return self.state

    async def extend_lease(
        self,
        _investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> bool:
        del worker_id, lease_seconds
        if self.extend_error_once is not None:
            error, self.extend_error_once = self.extend_error_once, None
            raise error
        return self.extend_result


class FakeFailureRecorder:
    def __init__(
        self,
        events: list[str],
        outcome: FailureOutcome = FailureOutcome.RETRY,
        *,
        attempt_count: int = 2,
        error: Exception | None = None,
    ) -> None:
        self.events = events
        self.result = RecordedFailure(outcome, attempt_count)
        self.error = error
        self.failures: list[ClassifiedInvestigationFailure] = []

    async def execute(
        self,
        *,
        investigation_id: uuid.UUID,
        worker_id: str,
        failure: ClassifiedInvestigationFailure,
    ) -> RecordedFailure:
        assert investigation_id == INVESTIGATION_ID
        assert worker_id == "worker-a"
        self.failures.append(failure)
        if self.error is not None:
            raise self.error
        self.events.append("failure_commit")
        return self.result


def _handler(
    action: FakeAction,
    state: FakeDeliveryState,
    *,
    heartbeat_interval: float = 3600,
    failure_outcome: FailureOutcome = FailureOutcome.RETRY,
    attempt_count: int = 2,
    retry_multiplier: float = 1,
    failure_recording_error: Exception | None = None,
) -> NatsInvestigationHandler:
    return NatsInvestigationHandler(
        action=action,
        delivery_state=state,
        failure_recorder=FakeFailureRecorder(
            action.events,
            failure_outcome,
            attempt_count=attempt_count,
            error=failure_recording_error,
        ),
        worker_id="worker-a",
        lease_seconds=90,
        heartbeat_interval_seconds=heartbeat_interval,
        retry_delays_seconds=(60, 120, 300, 300),
        retry_multiplier=lambda: retry_multiplier,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_success_acknowledges_only_after_the_action_commits() -> None:
    events: list[str] = []
    action = FakeAction(events)
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=0))

    await _handler(action, state).handle(FakeMessage(events))

    assert events == ["action", "committed", "ack"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [InvestigationStatus.COMPLETED, InvestigationStatus.FAILED])
async def test_redelivery_of_terminal_work_is_acknowledged_without_action(
    status: InvestigationStatus,
) -> None:
    events: list[str] = []
    action = FakeAction(events)
    state = FakeDeliveryState(_state(status=status, worker_id=None))

    await _handler(action, state).handle(FakeMessage(events))

    assert events == ["ack"]


@pytest.mark.asyncio
async def test_live_lease_is_nakd_for_its_remaining_duration() -> None:
    events: list[str] = []
    action = FakeAction(events)
    state = FakeDeliveryState(
        _state(lease_expires_at=NOW + timedelta(seconds=45), worker_id="worker-other")
    )
    message = FakeMessage(events)

    await _handler(action, state).handle(message)

    assert events == ["nak"]
    assert message.nak_delays == [45]


@pytest.mark.asyncio
async def test_transient_failure_releases_and_uses_attempt_backoff() -> None:
    events: list[str] = []
    action = FakeAction(
        events,
        TransientInvestigationError("provider down", error_code="analysis_unavailable"),
    )
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=1))
    message = FakeMessage(events)

    await _handler(action, state).handle(message)

    assert message.nak_delays == [120]
    assert events[-2:] == ["failure_commit", "nak"]
    assert events[-1] == "nak"


@pytest.mark.asyncio
async def test_permanent_failure_is_committed_before_message_termination() -> None:
    events: list[str] = []
    action = FakeAction(
        events,
        PermanentInvestigationError("not found", error_code="exception_not_found"),
    )
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=0))

    await _handler(action, state, failure_outcome=FailureOutcome.FAILED).handle(FakeMessage(events))

    assert events[-2:] == ["failure_commit", "term"]
    assert events[-1] == "term"


@pytest.mark.asyncio
async def test_last_transient_attempt_fails_and_terminates() -> None:
    events: list[str] = []
    action = FakeAction(
        events,
        TransientInvestigationError("still down", error_code="analysis_unavailable"),
    )
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=4))

    await _handler(
        action,
        state,
        failure_outcome=FailureOutcome.FAILED,
        attempt_count=5,
    ).handle(FakeMessage(events))

    assert events[-2:] == ["failure_commit", "term"]


@pytest.mark.asyncio
async def test_retry_delay_applies_configured_jitter_after_commit() -> None:
    events: list[str] = []
    action = FakeAction(
        events,
        TransientInvestigationError("provider down", error_code="analysis_unavailable"),
    )
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=1))
    message = FakeMessage(events)

    await _handler(action, state, retry_multiplier=1.2).handle(message)

    assert message.nak_delays == [144]
    assert events[-2:] == ["failure_commit", "nak"]


@pytest.mark.asyncio
async def test_failure_recording_ownership_loss_requests_immediate_redelivery() -> None:
    events: list[str] = []
    action = FakeAction(
        events,
        PermanentInvestigationError("not found", error_code="exception_not_found"),
    )
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=0))
    message = FakeMessage(events)

    await _handler(action, state, failure_outcome=FailureOutcome.OWNERSHIP_LOST).handle(message)

    assert message.nak_delays == [0]
    assert events[-2:] == ["failure_commit", "nak"]


@pytest.mark.asyncio
async def test_failure_recording_error_does_not_terminate_uncommitted_delivery() -> None:
    events: list[str] = []
    action = FakeAction(
        events,
        PermanentInvestigationError("not found", error_code="exception_not_found"),
    )
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=0))
    message = FakeMessage(events)

    await _handler(
        action,
        state,
        failure_recording_error=RuntimeError("commit failed"),
    ).handle(message)

    assert message.nak_delays == [30]
    assert "term" not in events


@pytest.mark.asyncio
async def test_database_failure_at_claim_naks_for_30_seconds() -> None:
    events: list[str] = []
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED))
    state.claim_error = OSError("database unavailable")
    message = FakeMessage(events)

    await _handler(FakeAction(events), state).handle(message)

    assert message.nak_delays == [30]


@pytest.mark.asyncio
async def test_heartbeat_ownership_loss_cancels_action_and_naks_immediately() -> None:
    events: list[str] = []
    action = FakeAction(events)
    action.wait_forever = True
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=0))
    state.extend_result = False
    message = FakeMessage(events)

    await _handler(action, state, heartbeat_interval=0).handle(message)

    assert "heartbeat" in events
    assert message.nak_delays == [0]


@pytest.mark.asyncio
async def test_transient_heartbeat_error_does_not_cancel_owned_action() -> None:
    events: list[str] = []
    action = FakeAction(events)
    action.wait_forever = True
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=0))
    state.extend_error_once = OSError("database reconnecting")
    state.extend_result = False
    message = FakeMessage(events)

    await _handler(action, state, heartbeat_interval=0).handle(message)

    assert events.count("heartbeat") >= 2
    assert message.nak_delays == [0]


@pytest.mark.asyncio
async def test_cancelling_handler_cancels_its_action_and_heartbeat_tasks() -> None:
    events: list[str] = []
    action = FakeAction(events)
    action.wait_forever = True
    state = FakeDeliveryState(_state(status=InvestigationStatus.QUEUED, attempts=0))
    task = asyncio.create_task(_handler(action, state).handle(FakeMessage(events)))
    await asyncio.wait_for(action.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert action.cancelled is True
