"""Transactional outbox persistence contract."""

from types import TracebackType
from typing import Protocol, Self

from orchestrator.domain.outbox import OutboxRecord


class OutboxStoreUnavailableError(RuntimeError):
    """Outbox persistence may succeed when retried."""


class OutboxWriter(Protocol):
    async def claim_pending(self, *, limit: int) -> list[OutboxRecord]: ...
    async def count_pending(self) -> int: ...
    async def mark_published(self, outbox_id: int) -> None: ...
    async def mark_failed(
        self, outbox_id: int, *, error: str, retry_after_seconds: float
    ) -> None: ...


class OutboxUnitOfWork(Protocol):
    @property
    def outbox(self) -> OutboxWriter: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
