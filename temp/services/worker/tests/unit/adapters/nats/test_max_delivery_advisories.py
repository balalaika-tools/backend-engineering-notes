"""Max-delivery advisories remain isolated across replicas and malformed data."""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from nats.aio.client import Client
from nats.aio.msg import Msg
from nats.js.client import JetStreamContext
from nats.js.errors import NotFoundError
from worker.adapters.nats.max_delivery_advisories import (
    MaxDeliveryCoordinator,
    NatsMaxDeliveryAdvisories,
)
from worker.application.reconcile import (
    MaxDeliveryDecision,
    MaxDeliveryOutcome,
    ReconciliationResult,
)
from worker.ports.reconciliation_store import RetentionDeletes


class FakeJetStream:
    def __init__(self, outcomes: dict[int, object], *, delete_failures: int = 0) -> None:
        self.outcomes = outcomes
        self.deleted: list[int] = []
        self.delete_failures = delete_failures

    async def get_msg(self, _stream: str, *, seq: int) -> Any:
        outcome = self.outcomes[seq]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def delete_msg(self, _stream: str, sequence: int) -> None:
        if self.delete_failures:
            self.delete_failures -= 1
            raise RuntimeError("delete unavailable")
        self.deleted.append(sequence)


class RawMessage:
    def __init__(self, data: bytes) -> None:
        self.data = data


def _adapter(jetstream: FakeJetStream) -> NatsMaxDeliveryAdvisories:
    return NatsMaxDeliveryAdvisories(
        cast(Client, cast(object, None)),
        cast(JetStreamContext, cast(object, jetstream)),
        stream="investigations",
        consumer="workers",
    )


async def _capture(adapter: NatsMaxDeliveryAdvisories, sequence: int) -> None:
    advisory = RawMessage(json.dumps({"stream_seq": sequence}).encode())
    await adapter._capture(cast(Msg, cast(object, advisory)))


@pytest.mark.asyncio
async def test_missing_message_is_discarded_as_already_handled() -> None:
    adapter = _adapter(FakeJetStream({7: NotFoundError()}))
    await _capture(adapter, 7)

    assert await adapter.receive() == ()
    assert await adapter.receive() == ()


@pytest.mark.asyncio
async def test_malformed_message_is_deleted_without_blocking_other_notices() -> None:
    malformed = RawMessage(b"not-json")
    jetstream = FakeJetStream({7: malformed})
    adapter = _adapter(jetstream)
    await _capture(adapter, 7)

    assert await adapter.receive() == ()
    assert jetstream.deleted == [7]


def _raw_event(investigation_id: uuid.UUID) -> RawMessage:
    return RawMessage(
        json.dumps(
            {
                "event_id": str(uuid.uuid4()),
                "event_type": "investigation.requested",
                "occurred_at": "2026-09-07T12:00:00Z",
                "investigation_id": str(investigation_id),
                "request_id": str(uuid.uuid4()),
                "client_id": "client-a",
                "exception_id": "EX-1",
            }
        ).encode()
    )


def _result(*decisions: MaxDeliveryDecision) -> ReconciliationResult:
    return ReconciliationResult(
        stale_processing=0,
        oldest_unpublished_age_seconds=None,
        max_deliver_exhausted=sum(
            decision.outcome is MaxDeliveryOutcome.FAILED for decision in decisions
        ),
        retention_deletes=RetentionDeletes(),
        investigations_by_status={},
        investigation_results=decisions,
    )


@dataclass
class FakeReconcile:
    outcomes: dict[uuid.UUID, MaxDeliveryOutcome]
    error: Exception | None = None
    calls: list[tuple[uuid.UUID, ...]] = field(default_factory=list)

    async def __call__(self, ids: tuple[uuid.UUID, ...]) -> ReconciliationResult:
        self.calls.append(ids)
        if self.error is not None:
            raise self.error
        return _result(*(MaxDeliveryDecision(value, self.outcomes[value]) for value in ids))


@pytest.mark.asyncio
async def test_coordinator_reconciles_duplicate_investigation_once_and_cleans_each_sequence() -> (
    None
):
    investigation_id = uuid.uuid4()
    jetstream = FakeJetStream({7: _raw_event(investigation_id), 8: _raw_event(investigation_id)})
    advisories = _adapter(jetstream)
    reconcile = FakeReconcile({investigation_id: MaxDeliveryOutcome.FAILED})
    coordinator = MaxDeliveryCoordinator(advisories, reconcile)
    await _capture(advisories, 7)
    await _capture(advisories, 8)

    result = await coordinator.run_once()

    assert reconcile.calls == [(investigation_id,)]
    assert result.max_deliver_exhausted == 1
    assert jetstream.deleted == [7, 8]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [MaxDeliveryOutcome.ALREADY_TERMINAL, MaxDeliveryOutcome.MISSING],
)
async def test_terminal_and_missing_outcomes_are_safe_for_cleanup(
    outcome: MaxDeliveryOutcome,
) -> None:
    investigation_id = uuid.uuid4()
    jetstream = FakeJetStream({7: _raw_event(investigation_id)})
    advisories = _adapter(jetstream)
    await _capture(advisories, 7)

    await MaxDeliveryCoordinator(advisories, FakeReconcile({investigation_id: outcome})).run_once()

    assert jetstream.deleted == [7]


@pytest.mark.asyncio
async def test_live_owner_and_failed_commit_preserve_pending_notice() -> None:
    investigation_id = uuid.uuid4()
    jetstream = FakeJetStream({7: _raw_event(investigation_id)})
    advisories = _adapter(jetstream)
    await _capture(advisories, 7)

    await MaxDeliveryCoordinator(
        advisories,
        FakeReconcile({investigation_id: MaxDeliveryOutcome.DEFERRED}),
    ).run_once()
    assert jetstream.deleted == []

    with pytest.raises(RuntimeError, match="commit failed"):
        await MaxDeliveryCoordinator(
            advisories,
            FakeReconcile({}, error=RuntimeError("commit failed")),
        ).run_once()
    assert jetstream.deleted == []


@pytest.mark.asyncio
async def test_failed_delete_retries_cleanup_without_recounting_failure() -> None:
    investigation_id = uuid.uuid4()
    jetstream = FakeJetStream(
        {7: _raw_event(investigation_id)},
        delete_failures=1,
    )
    advisories = _adapter(jetstream)
    reconcile = FakeReconcile(
        {
            investigation_id: MaxDeliveryOutcome.FAILED,
        }
    )
    coordinator = MaxDeliveryCoordinator(advisories, reconcile)
    await _capture(advisories, 7)

    first = await coordinator.run_once()
    reconcile.outcomes[investigation_id] = MaxDeliveryOutcome.ALREADY_TERMINAL
    second = await coordinator.run_once()

    assert first.max_deliver_exhausted == 1
    assert second.max_deliver_exhausted == 0
    assert jetstream.deleted == [7]
