"""Persistence operations for global configuration invalidation."""

from typing import cast

from platform_db import ConfigState
from sqlalchemy import Table, func, update
from sqlmodel.ext.asyncio.session import AsyncSession


class ConfigStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def increment_generation(self) -> int:
        table = cast(Table, ConfigState.__table__)  # type: ignore[attr-defined]
        statement = (
            update(table)
            .where(table.c.id == 1)
            .values(
                generation=table.c.generation + 1,
                updated_at=func.now(),
            )
            .returning(table.c.generation)
        )
        generation = (await self._session.exec(statement)).scalar_one_or_none()
        if generation is None:
            raise RuntimeError("The config_state singleton row is missing")
        return int(generation)
