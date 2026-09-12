"""Redis-backed shared control-context cache and rebuild lease."""

import math
import secrets
from urllib.parse import quote

from redis.asyncio import Redis
from redis.exceptions import RedisError
from worker.ports.control_context.control_context_cache import (
    ControlContextCacheKey,
    ControlContextCacheUnavailableError,
    ControlContextRebuildLease,
)

_RELEASE_IF_OWNER = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_PUBLISH_IF_OWNER = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    redis.call('set', KEYS[2], ARGV[2])
    return 1
end
return 0
"""


class RedisControlContextCache:
    def __init__(self, client: Redis, *, prefix: str = "control-context") -> None:
        normalized_prefix = prefix.strip(":")
        if not normalized_prefix:
            raise ValueError("The control-context cache prefix must not be empty")
        self._client = client
        self._prefix = normalized_prefix

    async def get(self, key: ControlContextCacheKey) -> bytes | None:
        try:
            value = await self._client.get(self._context_key(key))
        except RedisError as exc:
            raise ControlContextCacheUnavailableError(
                "Could not read the shared control context"
            ) from exc
        if value is None:
            return None
        if isinstance(value, str):
            return value.encode()
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        raise ControlContextCacheUnavailableError(
            "Redis returned an unsupported control-context value"
        )

    async def put(
        self,
        key: ControlContextCacheKey,
        payload: bytes,
        *,
        lease: ControlContextRebuildLease,
    ) -> None:
        if lease.key != key:
            raise ValueError("The rebuild lease does not belong to this context key")
        try:
            published = await self._client.eval(
                _PUBLISH_IF_OWNER,
                2,
                self._lock_key(key),
                self._context_key(key),
                lease.owner_token,
                payload,
            )
        except RedisError as exc:
            raise ControlContextCacheUnavailableError(
                "Could not write the shared control context"
            ) from exc
        if not published:
            raise ControlContextCacheUnavailableError(
                "The control-context rebuild lease expired before publication"
            )

    async def try_acquire_rebuild(
        self,
        key: ControlContextCacheKey,
        *,
        lease_seconds: float,
    ) -> ControlContextRebuildLease | None:
        owner_token = secrets.token_urlsafe(24)
        try:
            acquired = await self._client.set(
                self._lock_key(key),
                owner_token,
                nx=True,
                ex=max(1, math.ceil(lease_seconds)),
            )
        except RedisError as exc:
            raise ControlContextCacheUnavailableError(
                "Could not acquire the control-context rebuild lease"
            ) from exc
        if not acquired:
            return None
        return ControlContextRebuildLease(key=key, owner_token=owner_token)

    async def release_rebuild(self, lease: ControlContextRebuildLease) -> None:
        try:
            await self._client.eval(
                _RELEASE_IF_OWNER,
                1,
                self._lock_key(lease.key),
                lease.owner_token,
            )
        except RedisError as exc:
            raise ControlContextCacheUnavailableError(
                "Could not release the control-context rebuild lease"
            ) from exc

    def context_key(self, key: ControlContextCacheKey) -> str:
        """Return the concrete Redis key for diagnostics and targeted cleanup."""
        return self._context_key(key)

    def lock_key(self, key: ControlContextCacheKey) -> str:
        """Return the concrete lease key for diagnostics and targeted cleanup."""
        return self._lock_key(key)

    def _context_key(self, key: ControlContextCacheKey) -> str:
        tenant = quote(key.tenant_token, safe="")
        release = quote(key.deployment_release_id, safe="")
        return f"{self._prefix}:{tenant}:{release}:{key.generation}"

    def _lock_key(self, key: ControlContextCacheKey) -> str:
        return f"{self._context_key(key)}:rebuild-lock"
