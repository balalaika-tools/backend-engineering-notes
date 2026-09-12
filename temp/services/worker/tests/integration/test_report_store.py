"""Real S3-compatible storage contract against disposable MinIO."""

import os
import uuid
from datetime import UTC, datetime

import boto3  # type: ignore[import-untyped]
import pytest
from worker.adapters.aws.s3_report_store import S3ReportStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env(
        "TEST_S3_ENDPOINT",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ),
]


def _endpoint() -> str:
    value = os.environ.get("TEST_S3_ENDPOINT")
    if not value:
        pytest.skip("TEST_S3_ENDPOINT is required for the MinIO integration test")
    return value


@pytest.mark.asyncio
async def test_stores_report_under_date_partition_and_returns_uri() -> None:
    endpoint = _endpoint()
    bucket = f"integration-{uuid.uuid4().hex}"
    client = boto3.client("s3", endpoint_url=endpoint)
    client.create_bucket(Bucket=bucket)
    investigation_id = uuid.UUID("20e25b8c-6adf-4f32-a665-22acfa7ca49f")
    report = "# Exception Analysis Report\n\nBoth codes are present.\n"
    store = S3ReportStore(bucket=bucket, prefix="reports", endpoint_url=endpoint)

    key = f"reports/2026/09/07/{investigation_id}.md"
    try:
        uri = await store.store(
            investigation_id=investigation_id,
            generated_at=datetime(2026, 9, 7, 23, 30, tzinfo=UTC),
            report=report,
        )

        stored = client.get_object(Bucket=bucket, Key=key)
        assert stored["Body"].read().decode() == report
        assert stored["ContentType"] == "text/markdown; charset=utf-8"
        assert uri == f"s3://{bucket}/{key}"
    finally:
        client.delete_object(Bucket=bucket, Key=key)
        client.delete_bucket(Bucket=bucket)
        client.close()
