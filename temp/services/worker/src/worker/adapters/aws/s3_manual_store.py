"""S3-backed cache for generated control manuals."""

import asyncio
from typing import Any, cast
from urllib.parse import quote

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from worker.ports.control_context.manual_store import ManualKey, ManualStoreUnavailableError


class S3ManualStore:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str,
        endpoint_url: str | None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        normalized_prefix = prefix.strip("/")
        if not normalized_prefix:
            raise ValueError("The manual key prefix must not target the whole bucket")
        self._bucket = bucket
        self._prefix = normalized_prefix
        self._endpoint_url = endpoint_url
        self._credentials = _credentials(access_key_id, secret_access_key)

    async def get(self, key: ManualKey) -> str | None:
        return await asyncio.to_thread(self._get, self._object_key(key))

    async def put(self, key: ManualKey, content: str) -> None:
        await asyncio.to_thread(self._put, self._object_key(key), content)

    def _get(self, object_key: str) -> str | None:
        client: Any = boto3.client("s3", endpoint_url=self._endpoint_url, **self._credentials)
        try:
            response = client.get_object(Bucket=self._bucket, Key=object_key)
            return cast(bytes, response["Body"].read()).decode("utf-8")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise ManualStoreUnavailableError("Could not read cached control manual") from exc
        except (BotoCoreError, OSError, UnicodeDecodeError) as exc:
            raise ManualStoreUnavailableError("Could not read cached control manual") from exc
        finally:
            client.close()

    def _put(self, object_key: str, content: str) -> None:
        client: Any = boto3.client("s3", endpoint_url=self._endpoint_url, **self._credentials)
        try:
            client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=content.encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise ManualStoreUnavailableError("Could not cache control manual") from exc
        finally:
            client.close()

    def _object_key(self, key: ManualKey) -> str:
        segments = (
            key.tenant_token,
            key.control_name,
            key.config_md5,
            key.prompt_version,
        )
        suffix = "/".join(quote(value, safe="") for value in segments)
        return f"{self._prefix}/{suffix}.md"


def _credentials(access_key_id: str | None, secret_access_key: str | None) -> dict[str, str]:
    if (access_key_id is None) != (secret_access_key is None):
        raise ValueError("S3 access key ID and secret access key must be configured together")
    if access_key_id is None or secret_access_key is None:
        return {}
    return {
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
    }
