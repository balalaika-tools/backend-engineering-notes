"""Invalidate worker configuration context and optionally cached manuals."""

from collections.abc import Callable
from dataclasses import dataclass

from orchestrator.ports.config_store import ConfigUnitOfWork
from orchestrator.ports.manual_store import ManualStore


@dataclass(frozen=True, slots=True)
class ConfigurationReset:
    generation: int
    manuals_deleted: int


class ResetConfiguration:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], ConfigUnitOfWork],
        manual_store: ManualStore,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._manual_store = manual_store

    async def execute(self, *, purge_manuals: bool) -> ConfigurationReset:
        manuals_deleted = 0
        if purge_manuals:
            manuals_deleted = await self._manual_store.purge_manuals()
        async with self._unit_of_work_factory() as unit_of_work:
            generation = await unit_of_work.config_state.increment_generation()
            await unit_of_work.commit()
        return ConfigurationReset(
            generation=generation,
            manuals_deleted=manuals_deleted,
        )
