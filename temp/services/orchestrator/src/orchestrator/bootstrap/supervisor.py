"""Long-running orchestrator supervisor loops."""

import asyncio
import logging
from contextlib import suppress

from orchestrator.application.publish_outbox import PublishOutbox
from orchestrator.observability.metrics import outbox_publisher_consecutive_failures

logger = logging.getLogger(__name__)


async def run_outbox_publisher(
    action: PublishOutbox,
    *,
    poll_interval_seconds: float,
    stop: asyncio.Event,
) -> None:
    consecutive_failures = 0
    while not stop.is_set():
        try:
            result = await action.execute()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            consecutive_failures += 1
            outbox_publisher_consecutive_failures.set(consecutive_failures)
            logger.error(
                "outbox_publish_pass_failed",
                exc_info=exc,
                extra={"consecutive_failures": consecutive_failures},
            )
            await _wait_for_stop(
                stop,
                min(poll_interval_seconds * (2 ** (consecutive_failures - 1)), 60.0),
            )
            continue
        consecutive_failures = 0
        outbox_publisher_consecutive_failures.set(0)
        if result.selected:
            continue
        await _wait_for_stop(stop, poll_interval_seconds)


async def _wait_for_stop(stop: asyncio.Event, timeout_seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=timeout_seconds)
    except TimeoutError:
        pass


class OutboxPublisherSupervisor:
    def __init__(self, action: PublishOutbox, *, poll_interval_seconds: float) -> None:
        self._action = action
        self._poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.ready:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            run_outbox_publisher(
                self._action,
                poll_interval_seconds=self._poll_interval_seconds,
                stop=self._stop,
            ),
            name="outbox-publisher",
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(self._task),
                timeout=5.0,
            )
        except TimeoutError:
            await self.cancel()
        except Exception:
            logger.exception("outbox_publisher_stopped_after_failure")

    async def cancel(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
