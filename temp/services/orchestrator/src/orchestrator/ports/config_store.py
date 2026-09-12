"""Transactional configuration-state persistence contract."""

from types import TracebackType
from typing import Protocol, Self


class ConfigStoreUnavailableError(RuntimeError):
    """Configuration persistence may succeed when retried."""


class ConfigStateWriter(Protocol):
    async def increment_generation(self) -> int: ...


class ConfigUnitOfWork(Protocol):
    @property
    def config_state(self) -> ConfigStateWriter: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
