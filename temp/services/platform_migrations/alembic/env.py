"""Async Alembic environment owned by the platform migration deployable."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from platform_db import (
    ApiRequest,
    ApiRequestInvestigation,
    ConfigState,
    Investigation,
    OutboxEvent,
    SQLModel,
)
from platform_migrations.database_url import resolve_database_url
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

_REGISTERED_MODELS = (
    ApiRequest,
    ApiRequestInvestigation,
    ConfigState,
    Investigation,
    OutboxEvent,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = resolve_database_url(os.environ)
config.set_main_option(
    "sqlalchemy.url",
    database_url.render_as_string(hide_password=False).replace("%", "%%"),
)

target_metadata = SQLModel.metadata


def configure_context(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(configure_context)
    await connectable.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
