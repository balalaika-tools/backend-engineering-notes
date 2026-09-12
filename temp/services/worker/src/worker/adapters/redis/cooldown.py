"""Fail-open Redis implementation of the shared cooldown signal."""

import math

from redis.asyncio import Redis
from redis.exceptions import RedisError


class RedisCooldownSignal:
    def __init__(self, client: Redis, *, key: str = "provider:cooldown") -> None:
        self._client = client
        self._key = key

    async def activate(self, *, ttl_seconds: float) -> bool:
        ttl = max(1, math.ceil(ttl_seconds))
        try:
            await self._client.set(self._key, "1", ex=ttl)
        except RedisError:
            return False
        return True

    async def is_active(self) -> bool:
        try:
            value = await self._client.get(self._key)
        except RedisError:
            return False
        return value is not None
