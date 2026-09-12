"""Validated business result of investigating one exception."""

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["high", "medium", "low"]


class InvalidAnalysisOutputError(ValueError):
    """The analysis is unsafe to persist or write back."""

    error_code = "invalid_analysis_output"


@dataclass(frozen=True, slots=True)
class ExceptionAnalysis:
    explanation: str
    reasoning: str
    reason_code: str
    resolution_code: str
    short_analysis: str
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class CodeVocabulary:
    name: str
    codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.codes:
            raise ValueError(f"{self.name} vocabulary must not be empty")
        if len(set(self.codes)) != len(self.codes):
            raise ValueError(f"{self.name} vocabulary contains duplicate codes")


def validate_codes(
    analysis: ExceptionAnalysis,
    *,
    reason_codes: CodeVocabulary,
    resolution_codes: CodeVocabulary,
) -> None:
    """Apply a deterministic guard after provider-side schema validation."""
    errors: list[str] = []
    if analysis.reason_code not in reason_codes.codes:
        errors.append(f"reason_code {analysis.reason_code!r} is not in {reason_codes.name}")
    if analysis.resolution_code not in resolution_codes.codes:
        errors.append(
            f"resolution_code {analysis.resolution_code!r} is not in {resolution_codes.name}"
        )
    if errors:
        raise InvalidAnalysisOutputError("; ".join(errors))
