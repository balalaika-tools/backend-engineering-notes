"""Redis cooldown storage and fail-open reads."""

from typing import cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError
from worker.adapters.redis.cooldown import RedisCooldownSignal


class FakeRedis:
    def __init__(self) -> None:
        self.value: object = None
        self.set_calls: list[tuple[str, str, int]] = []
        self.error: Exception | None = None

    async def set(self, name: str, value: str, *, ex: int) -> None:
        if self.error:
            raise self.error
        self.value = value
        self.set_calls.append((name, value, ex))

    async def get(self, name: str) -> object:
        del name
        if self.error:
            raise self.error
        return self.value


@pytest.mark.asyncio
async def test_set_uses_supplied_ttl_and_read_observes_signal() -> None:
    redis = FakeRedis()
    signal = RedisCooldownSignal(cast(Redis, redis))

    stored = await signal.activate(ttl_seconds=17.2)
    active = await signal.is_active()

    assert stored is True
    assert active is True
    assert redis.set_calls == [("provider:cooldown", "1", 18)]


@pytest.mark.asyncio
async def test_redis_failure_is_fail_open() -> None:
    redis = FakeRedis()
    redis.error = ConnectionError("unavailable")
    signal = RedisCooldownSignal(cast(Redis, redis))

    stored = await signal.activate(ttl_seconds=60)
    active = await signal.is_active()

    assert stored is False
    assert active is False
