"""Technology-neutral exception-analysis capability."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from worker.domain.analysis import ExceptionAnalysis
from worker.domain.control_context import ControlContext


class AnalysisUnavailableError(RuntimeError):
    """The analysis may succeed when the investigation is retried."""

    error_code = "analysis_unavailable"


class AnalysisFailedError(RuntimeError):
    """The provider rejected analysis in a way that retrying cannot repair."""

    error_code = "analysis_failed"


class RunLimitExceededError(RuntimeError):
    """The bounded agent ended before producing an analysis."""

    error_code = "run_limit_exceeded"


@dataclass(frozen=True, slots=True)
class ExceptionAnalysisInput:
    exception_id: str
    current_datetime: datetime
    exception: Mapping[str, object]
    linked_records: tuple[Mapping[str, object], ...]


class ExceptionAnalyst(Protocol):
    async def analyze(
        self,
        value: ExceptionAnalysisInput,
        *,
        context: ControlContext,
    ) -> ExceptionAnalysis: ...
