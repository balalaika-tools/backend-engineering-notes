"""CTC comment and exception-code write-back boundary."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class WriteBackOutcome(StrEnum):
    WRITTEN = "written"
    SKIPPED_VERSION_CONFLICT = "skipped_version_conflict"
    SKIPPED_DISABLED = "skipped_disabled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class WriteBackResult:
    outcome: WriteBackOutcome
    detail: str | None = None


class CtcWriteBackUnavailableError(RuntimeError):
    """A CTC write may succeed when the investigation is retried."""

    error_code = "ctc_write_back_unavailable"


class CtcWriteBack(Protocol):
    async def write_comment(
        self,
        *,
        tenant_token: str,
        control_name: str,
        comment: str,
        record_pks: tuple[bytes, ...],
    ) -> WriteBackResult: ...

    async def write_codes(
        self,
        *,
        tenant_token: str,
        control_name: str,
        exception_name: str,
        exception_pk: bytes,
        exception_version: int,
        reason_code_feature_id: str,
        resolution_code_feature_id: str,
        reason_code: str,
        resolution_code: str,
    ) -> WriteBackResult: ...
