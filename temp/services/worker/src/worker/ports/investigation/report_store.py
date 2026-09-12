"""Completed investigation report storage boundary."""

import uuid
from datetime import datetime
from typing import Protocol


class ReportStoreUnavailableError(RuntimeError):
    """The report could not be durably stored."""

    error_code = "report_store_unavailable"


class ReportStore(Protocol):
    async def store(
        self,
        *,
        investigation_id: uuid.UUID,
        generated_at: datetime,
        report: str,
    ) -> str: ...
