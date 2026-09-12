"""ElastiCache IAM credential-provider tests."""

import pytest
from botocore.credentials import Credentials
from worker.adapters.redis.iam_credentials import (
    ElastiCacheCredentialsUnavailableError,
    ElastiCacheIAMCredentialProvider,
    build_redis_client,
)


class FakeSession:
    def __init__(self, credentials: Credentials | None) -> None:
        self._credentials = credentials

    def get_credentials(self) -> Credentials | None:
        return self._credentials


def test_generates_short_lived_node_cache_token() -> None:
    provider = ElastiCacheIAMCredentialProvider(
        user_id="ai-exception-dev-platform",
        cache_name="ai-exception-dev-redis",
        region="eu-west-1",
        session=FakeSession(Credentials("access-key", "secret-key", "session-token")),
    )

    username, token = provider.get_credentials()

    assert username == "ai-exception-dev-platform"
    assert token.startswith("ai-exception-dev-redis/?Action=connect&User=")
    assert "X-Amz-Expires=900" in token
    assert "X-Amz-Security-Token=" in token


def test_missing_runtime_identity_is_explicit() -> None:
    provider = ElastiCacheIAMCredentialProvider(
        user_id="platform",
        cache_name="cache",
        region="eu-west-1",
        session=FakeSession(None),
    )

    with pytest.raises(ElastiCacheCredentialsUnavailableError, match="AWS credentials"):
        provider.get_credentials()


def test_local_client_does_not_install_iam_provider() -> None:
    client = build_redis_client(
        url="redis://127.0.0.1:6379/0",
        iam_auth_enabled=False,
        iam_user_id=None,
        cache_name=None,
        region=None,
    )

    assert client.connection_pool.connection_kwargs["host"] == "127.0.0.1"
    assert "credential_provider" not in client.connection_pool.connection_kwargs


def test_iam_client_uses_credentials_provider() -> None:
    client = build_redis_client(
        url="rediss://cache.example.test:6379/0",
        iam_auth_enabled=True,
        iam_user_id="platform",
        cache_name="cache",
        region="eu-west-1",
    )

    provider = client.connection_pool.connection_kwargs["credential_provider"]
    assert isinstance(provider, ElastiCacheIAMCredentialProvider)
