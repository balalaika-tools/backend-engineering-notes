"""Configuration-generation read boundary."""

from typing import Protocol


class ConfigGenerationUnavailableError(RuntimeError):
    """The authoritative generation may be readable when the call is retried."""

    error_code = "platform_database_unavailable"


class ConfigGenerationSource(Protocol):
    async def get_generation(self) -> int: ...
