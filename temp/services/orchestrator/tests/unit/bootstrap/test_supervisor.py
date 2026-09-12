"""Outbox publisher supervisor lifecycle."""

import asyncio
from typing import cast

import pytest
from orchestrator.application.publish_outbox import PublishBatchResult, PublishOutbox
from orchestrator.bootstrap.supervisor import OutboxPublisherSupervisor, run_outbox_publisher


class IdleAction:
    async def execute(self) -> PublishBatchResult:
        return PublishBatchResult(selected=0, published=0, failed=0)


class FailOnceAction:
    def __init__(self, stop: asyncio.Event) -> None:
        self.calls = 0
        self._stop = stop

    async def execute(self) -> PublishBatchResult:
        self.calls += 1
        if self.calls == 1:
            raise OSError("database unavailable")
        self._stop.set()
        return PublishBatchResult(selected=0, published=0, failed=0)


@pytest.mark.asyncio
async def test_readiness_flips_when_publisher_task_is_cancelled() -> None:
    action = cast(PublishOutbox, IdleAction())
    supervisor = OutboxPublisherSupervisor(action, poll_interval_seconds=60)

    supervisor.start()
    await asyncio.sleep(0)
    assert supervisor.ready is True

    await supervisor.cancel()

    assert supervisor.ready is False


@pytest.mark.asyncio
async def test_graceful_stop_interrupts_idle_poll() -> None:
    action = cast(PublishOutbox, IdleAction())
    supervisor = OutboxPublisherSupervisor(action, poll_interval_seconds=60)
    supervisor.start()
    await asyncio.sleep(0)

    await supervisor.stop()

    assert supervisor.ready is False


@pytest.mark.asyncio
async def test_publisher_loop_recovers_after_a_transient_pass_failure() -> None:
    stop = asyncio.Event()
    action = FailOnceAction(stop)

    await run_outbox_publisher(
        cast(PublishOutbox, action),
        poll_interval_seconds=0.001,
        stop=stop,
    )

    assert action.calls == 2
