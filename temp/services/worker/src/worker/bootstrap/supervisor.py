"""Worker admission lifecycle and concurrency ownership."""

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from worker.adapters.nats.delivery import InvestigationConsumer, InvestigationMessage
from worker.application.reconcile import ReconciliationResult
from worker.application.record_investigation_failure import RecordedFailure
from worker.domain.admission import (
    AdmissionState,
    AimdPolicy,
    WindowCounters,
    cooldown_ttl_seconds,
    decide_aimd,
)
from worker.observability.metrics import (
    investigations_by_status,
    llm_window_failure_rate,
    max_deliver_exhausted,
    outbox_oldest_age,
    stale_processing,
    worker_actual_inflight,
    worker_in_cooldown,
    worker_target,
)
from worker.ports.cooldown_signal import CooldownSignal

logger = logging.getLogger(__name__)
MessageHandler = Callable[[InvestigationMessage], Awaitable[None]]


class InterruptionRecorder(Protocol):
    async def release_interrupted(
        self,
        *,
        investigation_id: uuid.UUID,
        worker_id: str,
    ) -> RecordedFailure: ...


class AdmissionSupervisor:
    def __init__(
        self,
        *,
        consumer: InvestigationConsumer,
        cooldown: CooldownSignal,
        state: AdmissionState,
        handler: MessageHandler,
        fetch_timeout_seconds: float,
        interruption_recorder: InterruptionRecorder,
        worker_id: str,
        shutdown_grace_seconds: float,
    ) -> None:
        self._consumer = consumer
        self._cooldown = cooldown
        self._state = state
        self._handler = handler
        self._fetch_timeout_seconds = fetch_timeout_seconds
        self._interruption_recorder = interruption_recorder
        self._worker_id = worker_id
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._tasks: dict[asyncio.Task[None], InvestigationMessage] = {}
        self._capacity_changed = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        await self._consumer.ensure()
        while not stop.is_set():
            if await self._cooldown.is_active() or not self._state.can_admit:
                await self._wait_for_capacity(stop)
                continue
            message = await self._consumer.fetch_one(timeout_seconds=self._fetch_timeout_seconds)
            if message is None:
                continue
            self._state.admitted()
            task = asyncio.create_task(self._run_one(message))
            self._tasks[task] = message
            task.add_done_callback(self._task_done)
        await self._drain()

    async def _run_one(self, message: InvestigationMessage) -> None:
        try:
            await self._handler(message)
        finally:
            self._state.completed()
            self._capacity_changed.set()

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "Investigation handler failed unexpectedly",
                exc_info=task.exception(),
            )

    async def _wait_for_capacity(self, stop: asyncio.Event) -> None:
        waiters = (
            asyncio.create_task(self._capacity_changed.wait()),
            asyncio.create_task(stop.wait()),
        )
        try:
            await asyncio.wait(
                waiters,
                timeout=1.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)
        self._capacity_changed.clear()

    async def _drain(self) -> None:
        if not self._tasks:
            return
        _, pending = await asyncio.wait(
            self._tasks,
            timeout=self._shutdown_grace_seconds,
        )
        if not pending:
            return
        interrupted = [(task, self._tasks[task]) for task in pending]
        for task, _message in interrupted:
            task.cancel()
        await asyncio.gather(*(task for task, _message in interrupted), return_exceptions=True)
        for _task, message in interrupted:
            await self._release_interrupted(message)

    async def _release_interrupted(self, message: InvestigationMessage) -> None:
        try:
            await self._interruption_recorder.release_interrupted(
                investigation_id=message.event.investigation_id,
                worker_id=self._worker_id,
            )
        finally:
            await message.nak(delay_seconds=0)


class ReconcilerSupervisor:
    def __init__(
        self,
        reconcile_once: Callable[[], Awaitable[ReconciliationResult]],
        *,
        interval_seconds: float,
    ) -> None:
        self._reconcile_once = reconcile_once
        self._interval_seconds = interval_seconds

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                result = await self._reconcile_once()
            except Exception:
                logger.exception("Reconciliation pass failed")
            else:
                stale_processing.set(result.stale_processing)
                for status, count in result.investigations_by_status.items():
                    investigations_by_status.set(count, {"status": status})
                if result.oldest_unpublished_age_seconds is not None:
                    outbox_oldest_age.set(result.oldest_unpublished_age_seconds)
                if result.max_deliver_exhausted:
                    max_deliver_exhausted.add(result.max_deliver_exhausted)
                if result.stale_processing:
                    logger.error(
                        "Stale processing investigations detected",
                        extra={"stale_processing_count": result.stale_processing},
                    )
                if result.outbox_lagging:
                    logger.error(
                        "Oldest unpublished outbox event exceeds five minutes",
                        extra={
                            "outbox_oldest_pending_age_seconds": (
                                result.oldest_unpublished_age_seconds
                            )
                        },
                    )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue


class AimdSupervisor:
    def __init__(
        self,
        *,
        state: AdmissionState,
        counters: WindowCounters,
        cooldown: CooldownSignal,
        policy: AimdPolicy,
        window_seconds: float,
        cooldown_seconds: float,
        increase_step: Callable[[], int],
        jitter_factor: Callable[[], float] = lambda: random.uniform(0.8, 1.2),
    ) -> None:
        self._state = state
        self._counters = counters
        self._cooldown = cooldown
        self._policy = policy
        self._window_seconds = window_seconds
        self._cooldown_seconds = cooldown_seconds
        self._increase_step = increase_step
        self._jitter_factor = jitter_factor

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._window_seconds)
            except TimeoutError:
                observation = self._counters.snapshot_and_reset()
                decision = decide_aimd(
                    current_target=self._state.target,
                    observation=observation,
                    policy=self._policy,
                    increase_step=self._increase_step(),
                )
                self._state.set_target(decision.target)
                worker_actual_inflight.set(self._state.actual_inflight)
                worker_target.set(decision.target)
                llm_window_failure_rate.set(decision.failure_rate)
                if decision.enter_cooldown:
                    await self._cooldown.activate(
                        ttl_seconds=cooldown_ttl_seconds(
                            default_seconds=self._cooldown_seconds,
                            jitter_factor=self._jitter_factor(),
                            retry_after_seconds=observation.retry_after_seconds,
                        )
                    )
                worker_in_cooldown.set(1 if await self._cooldown.is_active() else 0)
                logger.info(
                    "AIMD admission window evaluated",
                    extra={
                        "llm_attempts": observation.total,
                        "llm_failures": observation.failures,
                        "llm_failure_rate": decision.failure_rate,
                        "worker_target_inflight": decision.target,
                    },
                )
