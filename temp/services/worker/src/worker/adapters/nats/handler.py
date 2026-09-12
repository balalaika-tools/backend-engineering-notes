"""Translate one JetStream delivery into durable investigation lifecycle outcomes."""

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Status, StatusCode
from worker.adapters.nats.delivery import InvestigationMessage
from worker.application.record_investigation_failure import (
    ClassifiedInvestigationFailure,
    FailureOutcome,
    RecordedFailure,
    classify_investigation_failure,
)
from worker.domain.investigation import (
    InvestigationOwnershipLostError,
    InvestigationState,
    InvestigationStatus,
    PermanentInvestigationError,
    TransientInvestigationError,
)
from worker.observability.metrics import investigation_duration, investigations

logger = logging.getLogger(__name__)


class InvestigationAction(Protocol):
    async def execute(self, *, investigation_id: uuid.UUID, worker_id: str) -> None: ...


class InvestigationDeliveryState(Protocol):
    async def get(self, investigation_id: uuid.UUID) -> InvestigationState | None: ...

    async def claim(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> InvestigationState | None: ...

    async def extend_lease(
        self,
        investigation_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: float,
    ) -> bool: ...


class FailureRecorder(Protocol):
    async def execute(
        self,
        *,
        investigation_id: uuid.UUID,
        worker_id: str,
        failure: ClassifiedInvestigationFailure,
    ) -> RecordedFailure: ...


class NatsInvestigationHandler:
    def __init__(
        self,
        *,
        action: InvestigationAction,
        delivery_state: InvestigationDeliveryState,
        failure_recorder: FailureRecorder,
        worker_id: str,
        lease_seconds: float,
        heartbeat_interval_seconds: float,
        retry_delays_seconds: tuple[float, ...],
        retry_multiplier: Callable[[], float] = lambda: random.uniform(0.8, 1.2),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        tracer: trace.Tracer | None = None,
    ) -> None:
        if not retry_delays_seconds:
            raise ValueError("At least one investigation retry delay is required")
        self._action = action
        self._delivery_state = delivery_state
        self._failure_recorder = failure_recorder
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._retry_delays_seconds = retry_delays_seconds
        self._retry_multiplier = retry_multiplier
        self._clock = clock
        self._monotonic = monotonic
        self._tracer = tracer or trace.get_tracer(__name__)

    async def handle(self, message: InvestigationMessage) -> None:
        carrier = getattr(message, "trace_carrier", None) or {
            "traceparent": message.event.traceparent or ""
        }
        parent = extract(carrier, context=otel_context.Context())
        started = time.perf_counter()
        with self._tracer.start_as_current_span(
            "process investigations.requested",
            context=parent,
            kind=trace.SpanKind.CONSUMER,
            record_exception=False,
            attributes={
                "messaging.system": "nats",
                "messaging.destination.name": "investigations.requested",
                "messaging.operation.name": "process",
                "messaging.operation.type": "process",
                "app.workflow.name": "exception_investigation",
                "app.workflow.run.id": str(message.event.investigation_id),
            },
        ):
            outcome, error_code = await self._handle(message)
            attributes = {
                "status": outcome,
                "error_code": error_code,
            }
            investigations.add(1, attributes)
            investigation_duration.record(time.perf_counter() - started, attributes)

    async def _handle(self, message: InvestigationMessage) -> tuple[str, str]:
        investigation_id = message.event.investigation_id
        try:
            with self._tracer.start_as_current_span(
                "claim investigation",
                record_exception=False,
            ) as span:
                try:
                    claimed = await self._delivery_state.claim(
                        investigation_id,
                        worker_id=self._worker_id,
                        lease_seconds=self._lease_seconds,
                    )
                except Exception as exc:
                    span.set_attribute("error.type", type(exc).__name__)
                    raise
        except Exception:
            await message.nak(delay_seconds=30)
            return "retried", "database_unavailable"
        if claimed is None:
            try:
                await self._handle_unclaimed(message)
            except Exception as exc:
                logger.error("unclaimed_investigation_lookup_failed", exc_info=exc)
                await message.nak(delay_seconds=30)
                return "retried", "database_unavailable"
            else:
                return "skipped", "_NONE"

        try:
            await self._run_owned(message)
        except InvestigationOwnershipLostError:
            _mark_failure(InvestigationOwnershipLostError, "retried")
            await message.nak(delay_seconds=0)
            return "retried", "ownership_lost"
        except PermanentInvestigationError as exc:
            return await self._record_failure(message, claimed, exc)
        except TransientInvestigationError as exc:
            return await self._record_failure(message, claimed, exc)
        except Exception as exc:
            return await self._record_failure(message, claimed, exc)
        else:
            trace.get_current_span().set_attribute("app.outcome", "success")
            await message.ack()
            return "completed", "_NONE"

    def _log_failure(
        self,
        message: InvestigationMessage,
        claimed: InvestigationState,
        error: Exception,
    ) -> None:
        logger.error(
            "job_failed",
            exc_info=error,
            extra={
                "investigation_id": str(message.event.investigation_id),
                "event_id": str(message.event.event_id),
                "request_id": str(message.event.request_id),
                "worker_id": self._worker_id,
                "attempt": claimed.attempt_count,
                "error.type": type(error).__name__,
            },
        )

    async def _handle_unclaimed(self, message: InvestigationMessage) -> None:
        state = await self._delivery_state.get(message.event.investigation_id)
        if state is None or state.status in {
            InvestigationStatus.COMPLETED,
            InvestigationStatus.FAILED,
        }:
            await message.ack()
            return
        delay = 0.0
        if state.lease_expires_at is not None:
            delay = max(0.0, (state.lease_expires_at - self._clock()).total_seconds())
        await message.nak(delay_seconds=delay)

    async def _run_owned(self, message: InvestigationMessage) -> None:
        action_task = asyncio.create_task(
            self._action.execute(
                investigation_id=message.event.investigation_id,
                worker_id=self._worker_id,
            )
        )
        heartbeat_task = asyncio.create_task(self._heartbeat(message))
        tasks = (action_task, heartbeat_task)
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                await heartbeat_task
            await action_task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _heartbeat(self, message: InvestigationMessage) -> None:
        lease_deadline = self._monotonic() + self._lease_seconds
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            try:
                await message.in_progress()
            except Exception as exc:
                logger.warning("nats_heartbeat_failed", exc_info=exc)
            try:
                extended = await self._delivery_state.extend_lease(
                    message.event.investigation_id,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
            except Exception as exc:
                logger.warning("lease_heartbeat_failed", exc_info=exc)
                if self._monotonic() >= lease_deadline:
                    raise InvestigationOwnershipLostError(
                        "Investigation lease expired while heartbeat was unavailable"
                    ) from exc
                continue
            if not extended:
                raise InvestigationOwnershipLostError("Heartbeat lost lease ownership")
            lease_deadline = self._monotonic() + self._lease_seconds

    async def _record_failure(
        self,
        message: InvestigationMessage,
        claimed: InvestigationState,
        error: Exception,
    ) -> tuple[str, str]:
        self._log_failure(message, claimed, error)
        failure = classify_investigation_failure(error)
        try:
            recorded = await self._failure_recorder.execute(
                investigation_id=claimed.id,
                worker_id=self._worker_id,
                failure=failure,
            )
        except Exception as persistence_error:
            logger.error("failure_recording_failed", exc_info=persistence_error)
            _mark_failure(type(persistence_error), "retried")
            await message.nak(delay_seconds=30)
            return "retried", "database_unavailable"
        if recorded.outcome is FailureOutcome.OWNERSHIP_LOST:
            _mark_failure(type(error), "retried")
            await message.nak(delay_seconds=0)
            return "retried", "ownership_lost"
        if recorded.outcome is FailureOutcome.FAILED:
            _mark_failure(type(error), "failed")
            await message.term()
            return "failed", failure.error_code
        _mark_failure(type(error), "retried")
        await message.nak(delay_seconds=self._retry_delay(recorded.attempt_count or 1))
        return "retried", failure.error_code

    def _retry_delay(self, attempt_count: int) -> float:
        index = min(max(attempt_count - 1, 0), len(self._retry_delays_seconds) - 1)
        return self._retry_delays_seconds[index] * self._retry_multiplier()


def _mark_failure(error_type: type[BaseException], outcome: str) -> None:
    span = trace.get_current_span()
    span.set_status(Status(StatusCode.ERROR))
    span.set_attribute("error.type", error_type.__name__)
    span.set_attribute("app.outcome", outcome)
