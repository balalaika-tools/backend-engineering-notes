"""S3-backed cached-manual deletion."""

import asyncio
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from orchestrator.ports.manual_store import ManualStoreUnavailableError


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
        self._prefix = f"{normalized_prefix}/"
        self._endpoint_url = endpoint_url
        self._credentials = _credentials(access_key_id, secret_access_key)

    async def purge_manuals(self) -> int:
        return await asyncio.to_thread(self._purge_manuals)

    def _purge_manuals(self) -> int:
        client: Any | None = None
        deleted = 0
        try:
            client = boto3.client(
                "s3",
                endpoint_url=self._endpoint_url,
                **self._credentials,
            )
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
                keys = [{"Key": value["Key"]} for value in page.get("Contents", [])]
                if not keys:
                    continue
                response = client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": keys, "Quiet": True},
                )
                errors = response.get("Errors", [])
                if errors:
                    failed_keys = ", ".join(str(error.get("Key", "<unknown>")) for error in errors)
                    raise ManualStoreUnavailableError(
                        f"S3 failed to delete manual objects: {failed_keys}"
                    )
                deleted += len(keys)
        except ManualStoreUnavailableError:
            raise
        except (BotoCoreError, ClientError, OSError, KeyError, TypeError) as exc:
            raise ManualStoreUnavailableError("Could not purge cached control manuals") from exc
        finally:
            if client is not None:
                client.close()
        return deleted


def _credentials(access_key_id: str | None, secret_access_key: str | None) -> dict[str, str]:
    if (access_key_id is None) != (secret_access_key is None):
        raise ValueError("S3 access key ID and secret access key must be configured together")
    if access_key_id is None or secret_access_key is None:
        return {}
    return {
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
    }
