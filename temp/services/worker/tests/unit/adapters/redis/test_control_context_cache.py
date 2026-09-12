"""Redis control-context storage and lease semantics."""

from typing import cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError
from worker.adapters.redis.control_context_cache import RedisControlContextCache
from worker.ports.control_context.control_context_cache import (
    ControlContextCacheKey,
    ControlContextCacheUnavailableError,
    ControlContextRebuildLease,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}
        self.set_calls: list[tuple[str, object, bool, int | None]] = []
        self.error: Exception | None = None

    async def get(self, name: str) -> object:
        self._raise_if_unavailable()
        return self.values.get(name)

    async def set(
        self,
        name: str,
        value: object,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        self._raise_if_unavailable()
        self.set_calls.append((name, value, nx, ex))
        if nx and name in self.values:
            return None
        self.values[name] = value
        return True

    async def eval(self, script: str, number_of_keys: int, *arguments: object) -> int:
        del script
        self._raise_if_unavailable()
        lock_key = cast(str, arguments[0])
        if number_of_keys == 2:
            context_key = cast(str, arguments[1])
            token = arguments[2]
            payload = arguments[3]
            if self.values.get(lock_key) != token:
                return 0
            self.values[context_key] = payload
            return 1
        assert number_of_keys == 1
        token = arguments[1]
        if self.values.get(lock_key) != token:
            return 0
        del self.values[lock_key]
        return 1

    def _raise_if_unavailable(self) -> None:
        if self.error is not None:
            raise self.error


def _key() -> ControlContextCacheKey:
    return ControlContextCacheKey(
        tenant_token="WEB UI",
        deployment_release_id="release/42",
        generation=7,
    )


@pytest.mark.asyncio
async def test_context_is_shared_without_expiry_under_a_versioned_key() -> None:
    redis = FakeRedis()
    cache = RedisControlContextCache(cast(Redis, redis), prefix="app:context")
    key = _key()

    lease = await cache.try_acquire_rebuild(key, lease_seconds=10)
    assert lease is not None
    await cache.put(key, b'{"generation":7}', lease=lease)

    assert await cache.get(key) == b'{"generation":7}'
    assert redis.set_calls == [
        (
            "app:context:WEB%20UI:release%2F42:7:rebuild-lock",
            lease.owner_token,
            True,
            10,
        )
    ]


@pytest.mark.asyncio
async def test_rebuild_lease_is_atomic_and_only_its_owner_can_release_it() -> None:
    redis = FakeRedis()
    cache = RedisControlContextCache(cast(Redis, redis))
    key = _key()

    lease = await cache.try_acquire_rebuild(key, lease_seconds=1.2)
    contender = await cache.try_acquire_rebuild(key, lease_seconds=1.2)

    assert lease is not None
    assert contender is None
    assert redis.set_calls[0][2:] == (True, 2)

    await cache.release_rebuild(ControlContextRebuildLease(key=key, owner_token="not-the-owner"))
    assert await cache.try_acquire_rebuild(key, lease_seconds=1.2) is None

    await cache.release_rebuild(lease)
    assert await cache.try_acquire_rebuild(key, lease_seconds=1.2) is not None


@pytest.mark.asyncio
async def test_expired_builder_cannot_publish_over_a_new_owner() -> None:
    redis = FakeRedis()
    cache = RedisControlContextCache(cast(Redis, redis))
    key = _key()
    expired = await cache.try_acquire_rebuild(key, lease_seconds=10)
    assert expired is not None
    redis.values[cache.lock_key(key)] = "new-owner"

    with pytest.raises(ControlContextCacheUnavailableError, match="expired"):
        await cache.put(key, b"stale", lease=expired)

    assert await cache.get(key) is None


@pytest.mark.asyncio
async def test_redis_failure_is_a_transient_cache_error() -> None:
    redis = FakeRedis()
    redis.error = ConnectionError("unavailable")
    cache = RedisControlContextCache(cast(Redis, redis))

    with pytest.raises(ControlContextCacheUnavailableError) as error:
        await cache.get(_key())

    assert error.value.error_code == "control_context_cache_unavailable"
