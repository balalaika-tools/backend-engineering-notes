"""S3 manual prefix deletion behavior."""

from typing import Any

import pytest
from orchestrator.adapters.s3_manual_store import S3ManualStore
from orchestrator.ports.manual_store import ManualStoreUnavailableError


class FakePaginator:
    def __init__(self, client: "FakeS3Client") -> None:
        self._client = client

    def paginate(self, **kwargs: object) -> list[dict[str, object]]:
        self._client.list_request = kwargs
        return [
            {"Contents": [{"Key": "manuals/one.md"}]},
            {"Contents": [{"Key": "manuals/two.md"}]},
        ]


class FakeS3Client:
    def __init__(self) -> None:
        self.list_request: dict[str, object] = {}
        self.delete_requests: list[dict[str, object]] = []
        self.closed = False

    def get_paginator(self, operation: str) -> FakePaginator:
        assert operation == "list_objects_v2"
        return FakePaginator(self)

    def delete_objects(self, **kwargs: object) -> dict[str, object]:
        self.delete_requests.append(kwargs)
        return {}

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_purge_deletes_every_object_under_manual_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()

    def fake_client(service: str, **kwargs: object) -> Any:
        assert service == "s3"
        assert kwargs == {"endpoint_url": "http://minio:9000"}
        return client

    monkeypatch.setattr("orchestrator.adapters.s3_manual_store.boto3.client", fake_client)
    store = S3ManualStore(
        bucket="investigations",
        prefix="/manuals/",
        endpoint_url="http://minio:9000",
    )

    deleted = await store.purge_manuals()

    assert deleted == 2
    assert client.list_request == {"Bucket": "investigations", "Prefix": "manuals/"}
    assert client.delete_requests == [
        {
            "Bucket": "investigations",
            "Delete": {"Objects": [{"Key": "manuals/one.md"}], "Quiet": True},
        },
        {
            "Bucket": "investigations",
            "Delete": {"Objects": [{"Key": "manuals/two.md"}], "Quiet": True},
        },
    ]
    assert client.closed is True


@pytest.mark.asyncio
async def test_purge_fails_when_s3_reports_a_per_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeS3Client()

    def failed_delete(**kwargs: object) -> dict[str, object]:
        client.delete_requests.append(kwargs)
        return {"Errors": [{"Key": "manuals/one.md", "Code": "AccessDenied"}]}

    client.delete_objects = failed_delete  # type: ignore[method-assign]
    monkeypatch.setattr(
        "orchestrator.adapters.s3_manual_store.boto3.client",
        lambda *_args, **_kwargs: client,
    )
    store = S3ManualStore(bucket="investigations", prefix="manuals", endpoint_url=None)

    with pytest.raises(ManualStoreUnavailableError, match="manuals/one.md"):
        await store.purge_manuals()

    assert client.closed is True
