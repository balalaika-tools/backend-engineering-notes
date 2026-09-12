"""Upgrade backfills explicit requests and downgrade preserves ordered membership."""

import asyncio
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_env("INTEGRATION_PLATFORM_DATABASE_URL"),
]


@pytest.mark.asyncio
async def test_filtered_migration_round_trip_preserves_existing_request(monkeypatch) -> None:
    url = os.environ["INTEGRATION_PLATFORM_DATABASE_URL"]
    monkeypatch.delenv("PLATFORM_DATABASE_URL", raising=False)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    request_id = uuid.uuid4()
    engine = create_async_engine(url)
    try:
        await asyncio.to_thread(command.downgrade, config, "20260909_0004")
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO api_requests (id, client_id, exception_ids) VALUES (:id, 'migration-test', ARRAY['B', 'A'])"
                ),
                {"id": request_id},
            )
        await asyncio.to_thread(command.upgrade, config, "head")
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT request_kind, exception_ids, selection_max_exceptions FROM api_requests WHERE id=:id"
                    ),
                    {"id": request_id},
                )
            ).one()
            assert row.request_kind == "explicit"
            assert row.exception_ids == ["B", "A"]
            assert row.selection_max_exceptions is None
        await asyncio.to_thread(command.downgrade, config, "20260909_0004")
        async with engine.connect() as connection:
            assert (
                await connection.execute(
                    text("SELECT exception_ids FROM api_requests WHERE id=:id"), {"id": request_id}
                )
            ).scalar_one() == ["B", "A"]
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
        await engine.dispose()
