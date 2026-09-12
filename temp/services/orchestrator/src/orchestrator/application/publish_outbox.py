"""Publish a locked outbox batch and record broker acknowledgements."""

from collections.abc import Callable
from dataclasses import dataclass

from orchestrator.observability.metrics import outbox_pending, outbox_publish
from orchestrator.ports.event_publisher import EventPublisher, EventPublishError
from orchestrator.ports.outbox_store import OutboxUnitOfWork

_BACKOFF_SECONDS = (1.0, 5.0, 30.0, 60.0)


@dataclass(frozen=True, slots=True)
class PublishBatchResult:
    selected: int
    published: int
    failed: int


class PublishOutbox:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], OutboxUnitOfWork],
        publisher: EventPublisher,
        batch_size: int,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._publisher = publisher
        self._batch_size = batch_size

    async def execute(self) -> PublishBatchResult:
        published = 0
        failed = 0
        async with self._unit_of_work_factory() as unit_of_work:
            events = await unit_of_work.outbox.claim_pending(limit=self._batch_size)
            for event in events:
                try:
                    await self._publisher.publish(
                        subject=event.subject,
                        payload=event.payload,
                        message_id=event.event_id,
                    )
                except EventPublishError as exc:
                    await unit_of_work.outbox.mark_failed(
                        event.id,
                        error=str(exc),
                        retry_after_seconds=_retry_delay(event.attempt_count),
                    )
                    failed += 1
                else:
                    await unit_of_work.outbox.mark_published(event.id)
                    published += 1
            pending = await unit_of_work.outbox.count_pending()
            await unit_of_work.commit()
        if published:
            outbox_publish.add(published, {"result": "published"})
        if failed:
            outbox_publish.add(failed, {"result": "failed"})
        outbox_pending.set(pending)
        return PublishBatchResult(
            selected=len(events),
            published=published,
            failed=failed,
        )


def _retry_delay(attempt_count: int) -> float:
    return _BACKOFF_SECONDS[min(attempt_count, len(_BACKOFF_SECONDS) - 1)]
