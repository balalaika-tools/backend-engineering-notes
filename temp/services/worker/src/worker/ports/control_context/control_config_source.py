"""Control configuration and decode-set boundary."""

from dataclasses import dataclass
from typing import Protocol

from worker.domain.control_context import DecodeSet


class ControlConfigUnavailableError(RuntimeError):
    """CTC configuration may become available when the investigation is retried."""

    error_code = "control_configuration_unavailable"


class InvalidControlConfigError(RuntimeError):
    """CTC returned a successful response with an unusable payload."""


@dataclass(frozen=True, slots=True)
class ControlConfiguration:
    xml: bytes
    md5: str
    rec_schema_name: str
    tenant_schema_name: str
    editable_feature_ids: tuple[tuple[str, str], ...] = ()


class ControlConfigSource(Protocol):
    async def fetch_configuration(
        self,
        *,
        tenant_token: str,
        control_name: str,
    ) -> ControlConfiguration: ...

    async def fetch_decode_sets(self, *, tenant_token: str) -> tuple[DecodeSet, ...]: ...
