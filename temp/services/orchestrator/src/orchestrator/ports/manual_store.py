"""Object-storage boundary for cached control manuals."""

from typing import Protocol


class ManualStoreUnavailableError(RuntimeError):
    """Cached manuals could not be changed in object storage."""


class ManualStore(Protocol):
    async def purge_manuals(self) -> int:
        """Delete every cached manual and return the number removed."""
        ...
