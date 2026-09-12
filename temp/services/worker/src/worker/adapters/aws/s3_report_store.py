"""S3-backed durable investigation report storage."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from worker.ports.investigation.report_store import ReportStoreUnavailableError


class S3ReportStore:
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
            raise ValueError("The report key prefix must not target the whole bucket")
        self._bucket = bucket
        self._prefix = normalized_prefix
        self._endpoint_url = endpoint_url
        self._credentials = _credentials(access_key_id, secret_access_key)

    async def store(
        self,
        *,
        investigation_id: uuid.UUID,
        generated_at: datetime,
        report: str,
    ) -> str:
        object_key = self._object_key(investigation_id, generated_at)
        await asyncio.to_thread(self._put, object_key, report)
        return f"s3://{self._bucket}/{object_key}"

    def _put(self, object_key: str, report: str) -> None:
        client: Any = boto3.client("s3", endpoint_url=self._endpoint_url, **self._credentials)
        try:
            client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=report.encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            raise ReportStoreUnavailableError("Could not store investigation report") from exc
        finally:
            client.close()

    def _object_key(self, investigation_id: uuid.UUID, generated_at: datetime) -> str:
        if generated_at.tzinfo is None:
            raise ValueError("Report timestamp must be timezone-aware")
        date = generated_at.astimezone(UTC)
        return f"{self._prefix}/{date.year}/{date.month:02d}/{date.day:02d}/{investigation_id}.md"


def _credentials(access_key_id: str | None, secret_access_key: str | None) -> dict[str, str]:
    if (access_key_id is None) != (secret_access_key is None):
        raise ValueError("S3 access key ID and secret access key must be configured together")
    if access_key_id is None or secret_access_key is None:
        return {}
    return {
        "aws_access_key_id": access_key_id,
        "aws_secret_access_key": secret_access_key,
    }
