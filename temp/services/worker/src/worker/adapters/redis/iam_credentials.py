"""Redis client construction for local and ElastiCache IAM authentication."""

import asyncio
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.auth import SigV4QueryAuth  # type: ignore[import-untyped]
from botocore.awsrequest import AWSRequest  # type: ignore[import-untyped]
from redis.asyncio import Redis
from redis.credentials import CredentialProvider


class ElastiCacheCredentialsUnavailableError(RuntimeError):
    """The runtime AWS identity could not issue an ElastiCache token."""


class ElastiCacheIAMCredentialProvider(CredentialProvider):
    """Generate fresh SigV4 credentials whenever redis-py opens a connection."""

    def __init__(
        self,
        *,
        user_id: str,
        cache_name: str,
        region: str,
        session: Any | None = None,
    ) -> None:
        self._user_id = user_id
        self._cache_name = cache_name
        self._region = region
        self._session = session or boto3.Session()

    def get_credentials(self) -> tuple[str, str]:
        credentials = self._session.get_credentials()
        if credentials is None:
            raise ElastiCacheCredentialsUnavailableError(
                "AWS credentials are unavailable for ElastiCache IAM authentication"
            )
        request = AWSRequest(
            method="GET",
            url=f"http://{self._cache_name}/",
            params={"Action": "connect", "User": self._user_id},
        )
        SigV4QueryAuth(
            credentials.get_frozen_credentials(),
            "elasticache",
            self._region,
            expires=900,
        ).add_auth(request)
        if request.url is None:
            raise ElastiCacheCredentialsUnavailableError(
                "Could not generate an ElastiCache IAM authentication token"
            )
        return self._user_id, request.url.removeprefix("http://")

    async def get_credentials_async(self) -> tuple[str, str]:
        return await asyncio.to_thread(self.get_credentials)


def build_redis_client(
    *,
    url: str,
    iam_auth_enabled: bool,
    iam_user_id: str | None,
    cache_name: str | None,
    region: str | None,
) -> Redis:
    """Build a local Redis client or an IAM-authenticated ElastiCache client."""
    if not iam_auth_enabled:
        return Redis.from_url(url, decode_responses=False, driver_info=None)
    if iam_user_id is None or cache_name is None or region is None:
        raise ValueError("ElastiCache IAM authentication coordinates are required")
    provider = ElastiCacheIAMCredentialProvider(
        user_id=iam_user_id,
        cache_name=cache_name,
        region=region,
    )
    return Redis.from_url(
        url,
        credential_provider=provider,
        decode_responses=False,
        driver_info=None,
    )
