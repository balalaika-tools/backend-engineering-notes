"""Shared cache boundary for resolved control contexts."""

from dataclasses import dataclass
from typing import Protocol


class ControlContextCacheUnavailableError(RuntimeError):
    """The shared context cache may become available when the work is retried."""

    error_code = "control_context_cache_unavailable"


@dataclass(frozen=True, slots=True)
class ControlContextCacheKey:
    tenant_token: str
    deployment_release_id: str
    generation: int


@dataclass(frozen=True, slots=True)
class ControlContextRebuildLease:
    key: ControlContextCacheKey
    owner_token: str


class ControlContextCache(Protocol):
    async def get(self, key: ControlContextCacheKey) -> bytes | None: ...

    async def put(
        self,
        key: ControlContextCacheKey,
        payload: bytes,
        *,
        lease: ControlContextRebuildLease,
    ) -> None: ...

    async def try_acquire_rebuild(
        self,
        key: ControlContextCacheKey,
        *,
        lease_seconds: float,
    ) -> ControlContextRebuildLease | None: ...

    async def release_rebuild(self, lease: ControlContextRebuildLease) -> None: ...
