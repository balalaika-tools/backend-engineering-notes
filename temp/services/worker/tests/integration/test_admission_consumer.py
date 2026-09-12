"""Admission capacity and cooldown against a real JetStream consumer."""

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import nats
import pytest
from worker.adapters.nats.consumer import (
    NatsConsumerSettings,
    NatsInvestigationConsumer,
)
from worker.adapters.nats.delivery import InvestigationMessage
from worker.application.record_investigation_failure import FailureOutcome, RecordedFailure
from worker.bootstrap.supervisor import AdmissionSupervisor
from worker.domain.admission import AdmissionState

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env("INTEGRATION_NATS_URL", "INTEGRATION_NATS_AUTH_TOKEN"),
]


def _nats_url() -> str:
    value = os.environ.get("INTEGRATION_NATS_URL")
    if not value:
        pytest.skip("INTEGRATION_NATS_URL is required")
    return value


@dataclass
class FakeCooldown:
    active: bool = True

    async def activate(self, *, ttl_seconds: float) -> bool:
        del ttl_seconds
        self.active = True
        return True

    async def is_active(self) -> bool:
        return self.active


class FakeInterruptionRecorder:
    async def release_interrupted(
        self,
        *,
        investigation_id: uuid.UUID,
        worker_id: str,
    ) -> RecordedFailure:
        del investigation_id, worker_id
        return RecordedFailure(FailureOutcome.RETRY, 1)


def _payload(exception_id: str) -> bytes:
    return json.dumps(
        {
            "event_id": str(uuid.uuid4()),
            "event_type": "investigation.requested",
            "occurred_at": datetime.now(UTC).isoformat(),
            "investigation_id": str(uuid.uuid4()),
            "request_id": str(uuid.uuid4()),
            "client_id": "client",
            "exception_id": exception_id,
        }
    ).encode()


@pytest.mark.asyncio
async def test_admission_fetches_only_with_capacity_and_outside_cooldown() -> None:
    client = await nats.connect(
        _nats_url(),
        token=os.environ["INTEGRATION_NATS_AUTH_TOKEN"],
    )
    jetstream = client.jetstream()
    suffix = uuid.uuid4().hex[:12]
    stream = f"ADMISSION_VERIFY_{suffix.upper()}"
    subject = f"tests.admission.{suffix}"
    consumer = NatsInvestigationConsumer(
        jetstream,
        settings=NatsConsumerSettings(
            stream=stream,
            consumer=f"worker-verifier-{suffix}",
            subject=subject,
            ack_wait_seconds=30,
            max_deliver=5,
            max_ack_pending=10,
            duplicate_window_seconds=120,
        ),
    )
    await consumer.ensure()
    for number in range(3):
        await jetstream.publish(subject, _payload(f"EX-{number}"))

    cooldown = FakeCooldown()
    state = AdmissionState(initial_target=2, max_per_instance=1)
    started: asyncio.Queue[str] = asyncio.Queue()
    gate = asyncio.Semaphore(0)

    async def handler(message: InvestigationMessage) -> None:
        await started.put(message.event.exception_id)
        await gate.acquire()
        await message.ack()

    supervisor = AdmissionSupervisor(
        consumer=consumer,
        cooldown=cooldown,
        state=state,
        handler=handler,
        fetch_timeout_seconds=0.05,
        interruption_recorder=FakeInterruptionRecorder(),
        worker_id="worker-verifier",
        shutdown_grace_seconds=2,
    )
    stop = asyncio.Event()
    loop = asyncio.create_task(supervisor.run(stop))

    await asyncio.sleep(0.15)
    assert started.empty()
    assert state.actual_inflight == 0

    cooldown.active = False
    await asyncio.wait_for(started.get(), timeout=2)
    await asyncio.sleep(0.15)
    assert started.empty()
    assert state.actual_inflight == 1
    assert state.limit == 1

    gate.release()
    await asyncio.wait_for(started.get(), timeout=2)
    assert state.actual_inflight == 1

    stop.set()
    gate.release()
    await asyncio.wait_for(loop, timeout=2)
    assert state.actual_inflight == 0
    await jetstream.delete_stream(stream)
    await client.drain()
