"""Cluster-wide provider cooldown boundary."""

from typing import Protocol


class CooldownSignal(Protocol):
    async def activate(self, *, ttl_seconds: float) -> bool:
        """Attempt to publish a cooldown; return whether it was stored."""
        ...

    async def is_active(self) -> bool:
        """Read cooldown state, returning false when the backing store fails."""
        ...
