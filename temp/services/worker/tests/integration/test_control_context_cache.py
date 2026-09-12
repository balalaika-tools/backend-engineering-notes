"""Shared control-context behavior against a real disposable Redis namespace."""

import os
import uuid

import pytest
from redis.asyncio import Redis
from worker.adapters.redis.control_context_cache import RedisControlContextCache
from worker.ports.control_context.control_context_cache import ControlContextCacheKey

pytestmark = [pytest.mark.integration, pytest.mark.requires_env("INTEGRATION_REDIS_URL")]


def _redis_url() -> str:
    value = os.environ.get("INTEGRATION_REDIS_URL")
    if not value:
        pytest.skip("INTEGRATION_REDIS_URL is required")
    return value


@pytest.mark.asyncio
async def test_two_clients_share_context_and_contend_for_one_rebuild_lease() -> None:
    first_client = Redis.from_url(_redis_url())
    second_client = Redis.from_url(_redis_url())
    prefix = f"integration-control-context-{uuid.uuid4().hex}"
    first = RedisControlContextCache(first_client, prefix=prefix)
    second = RedisControlContextCache(second_client, prefix=prefix)
    key = ControlContextCacheKey("WEBUI", "release-integration", 3)

    try:
        lease = await first.try_acquire_rebuild(key, lease_seconds=10)
        contender = await second.try_acquire_rebuild(key, lease_seconds=10)
        assert lease is not None
        assert contender is None

        await first.put(key, b'{"generation":3}', lease=lease)
        assert await second.get(key) == b'{"generation":3}'
        assert await first_client.ttl(first.context_key(key)) == -1

        await first.release_rebuild(lease)
        assert await second.try_acquire_rebuild(key, lease_seconds=10) is not None
    finally:
        await first_client.delete(first.context_key(key), first.lock_key(key))
        await first_client.aclose()
        await second_client.aclose()
