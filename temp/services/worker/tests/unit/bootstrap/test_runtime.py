"""Worker bootstrap resource-cleanup regressions."""

from typing import cast

import pytest
from worker.bootstrap import runtime as runtime_module
from worker.config.secrets import Secrets
from worker.config.settings import Settings


class FakeResources:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_build_runtime_closes_resources_when_composition_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resources = FakeResources()

    async def build_resources(_settings: Settings, _secrets: Secrets) -> FakeResources:
        return resources

    def fail_composition(**_kwargs: object) -> None:
        raise RuntimeError("composition failed")

    monkeypatch.setattr(runtime_module, "_build_resources", build_resources)
    monkeypatch.setattr(runtime_module, "_compose_runtime", fail_composition)

    with pytest.raises(RuntimeError, match="composition failed"):
        await runtime_module.build_runtime(
            settings=cast(Settings, object()),
            secrets=cast(Secrets, object()),
        )

    assert resources.closed is True
