"""Serialization and interpretation of durable investigation checkpoints."""

from collections.abc import Mapping
from typing import cast

from worker.domain.analysis import ExceptionAnalysis
from worker.domain.investigation import InvestigationState, PermanentInvestigationError
from worker.ports.investigation.ctc_write_back import WriteBackOutcome
from worker.ports.investigation.exception_source import ExceptionData

COMMENT_DONE = frozenset({WriteBackOutcome.WRITTEN, WriteBackOutcome.SKIPPED_DISABLED})
CODES_DONE = frozenset(
    {
        WriteBackOutcome.WRITTEN,
        WriteBackOutcome.SKIPPED_VERSION_CONFLICT,
        WriteBackOutcome.SKIPPED_DISABLED,
    }
)


def analysis_from_checkpoint(value: Mapping[str, object] | None) -> ExceptionAnalysis:
    if value is None:
        raise _invalid_analysis("Persisted analysis checkpoint has no analysis payload")
    required = ("explanation", "reasoning", "reason_code", "resolution_code", "short_analysis")
    if any(not isinstance(value.get(key), str) for key in required):
        raise _invalid_analysis("Persisted analysis checkpoint has an invalid payload")
    confidence = value.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        raise _invalid_analysis("Persisted analysis checkpoint has an invalid confidence")
    return ExceptionAnalysis(
        explanation=cast(str, value["explanation"]),
        reasoning=cast(str, value["reasoning"]),
        reason_code=cast(str, value["reason_code"]),
        resolution_code=cast(str, value["resolution_code"]),
        short_analysis=cast(str, value["short_analysis"]),
        confidence=confidence,
    )


def checkpoint_exception_version(
    state: InvestigationState,
    source_data: ExceptionData,
) -> int:
    if state.analysis_persisted_at is not None and state.analysis is not None:
        version = state.analysis.get("_exception_version")
        if isinstance(version, int) and not isinstance(version, bool):
            return version
    return source_data.exception_version


def completed_checkpoint(
    value: str | None,
    *,
    allowed: frozenset[WriteBackOutcome],
) -> WriteBackOutcome | None:
    outcome = checkpoint_outcome(value)
    if outcome is None:
        return None
    return require_successful_outcome(outcome, allowed=allowed)


def checkpoint_outcome(value: str | None) -> WriteBackOutcome | None:
    if value is None:
        return None
    try:
        return WriteBackOutcome(value)
    except ValueError as exc:
        raise PermanentInvestigationError(
            f"Unknown write-back checkpoint outcome {value!r}",
            error_code="invalid_write_back_checkpoint",
        ) from exc


def require_successful_outcome(
    outcome: WriteBackOutcome,
    *,
    allowed: frozenset[WriteBackOutcome],
) -> WriteBackOutcome:
    if outcome not in allowed:
        raise PermanentInvestigationError(
            f"Write-back checkpoint has terminal outcome {outcome.value}",
            error_code="write_back_rejected",
        )
    return outcome


def write_back_complete(state: InvestigationState) -> bool:
    return (
        completed_checkpoint(state.comment_outcome, allowed=COMMENT_DONE) is not None
        and completed_checkpoint(state.codes_outcome, allowed=CODES_DONE) is not None
    )


def _invalid_analysis(message: str) -> PermanentInvestigationError:
    return PermanentInvestigationError(message, error_code="invalid_analysis_checkpoint")
