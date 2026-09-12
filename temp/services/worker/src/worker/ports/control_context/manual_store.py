"""Cached control-manual object storage boundary."""

from dataclasses import dataclass
from typing import Protocol


class ManualStoreUnavailableError(RuntimeError):
    """The object store could not read or persist a cached manual."""

    error_code = "manual_store_unavailable"


@dataclass(frozen=True, slots=True)
class ManualKey:
    tenant_token: str
    control_name: str
    config_md5: str
    prompt_version: str


class ManualStore(Protocol):
    async def get(self, key: ManualKey) -> str | None: ...

    async def put(self, key: ManualKey, content: str) -> None: ...
