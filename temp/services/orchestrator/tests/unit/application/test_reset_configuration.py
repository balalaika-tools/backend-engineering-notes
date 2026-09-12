"""Configuration reset behavior with deterministic stores."""

from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import pytest
from orchestrator.application.reset_configuration import ResetConfiguration


@dataclass
class State:
    generation: int = 0
    objects: set[str] = field(
        default_factory=lambda: {
            "manuals/tenant/control/one.md",
            "manuals/tenant/control/two.md",
            "reports/one.md",
        }
    )


@dataclass
class FakeConfigState:
    state: State

    async def increment_generation(self) -> int:
        self.state.generation += 1
        return self.state.generation


@dataclass
class FakeUnitOfWork:
    state: State

    def __post_init__(self) -> None:
        self.config_state = FakeConfigState(self.state)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback

    async def commit(self) -> None:
        return None


@dataclass
class FakeManualStore:
    state: State

    async def purge_manuals(self) -> int:
        manual_keys = {key for key in self.state.objects if key.startswith("manuals/")}
        self.state.objects.difference_update(manual_keys)
        return len(manual_keys)


def _action(state: State) -> ResetConfiguration:
    return ResetConfiguration(
        unit_of_work_factory=lambda: FakeUnitOfWork(state),
        manual_store=FakeManualStore(state),
    )


@pytest.mark.asyncio
async def test_reset_increments_generation_without_purging_manuals() -> None:
    state = State()

    result = await _action(state).execute(purge_manuals=False)

    assert result.generation == 1
    assert result.manuals_deleted == 0
    assert "manuals/tenant/control/one.md" in state.objects


@pytest.mark.asyncio
async def test_reset_with_purge_deletes_only_manual_keys() -> None:
    state = State(generation=4)

    result = await _action(state).execute(purge_manuals=True)

    assert result.generation == 5
    assert result.manuals_deleted == 2
    assert state.objects == {"reports/one.md"}
