"""Analysis validation, comments, and report rendering."""

from datetime import UTC, datetime

import pytest
from worker.domain.analysis import (
    CodeVocabulary,
    ExceptionAnalysis,
    InvalidAnalysisOutputError,
    validate_codes,
)
from worker.domain.comment import (
    MAX_CONFIDENCE_PREFIX_LENGTH,
    confidence_prefix,
    render_comment,
    short_analysis_budget,
    truncate_text,
    validate_comment_length,
)
from worker.domain.report import render_report


def _analysis(**overrides: str) -> ExceptionAnalysis:
    values = {
        "explanation": "The position quantity differs after the corporate action.",
        "reasoning": "The dated event explains the quantity ratio.",
        "reason_code": "CorporateAction",
        "resolution_code": "Custodian",
        "short_analysis": "A corporate action caused the break.",
        "confidence": "high",
    }
    values.update(overrides)
    return ExceptionAnalysis(**values)  # type: ignore[arg-type]


def test_validate_codes_accepts_both_runtime_vocabularies() -> None:
    validate_codes(
        _analysis(),
        reason_codes=CodeVocabulary("ReasonCodes", ("CorporateAction", "Other")),
        resolution_codes=CodeVocabulary("ResolutionCodes", ("Custodian", "Internal")),
    )


@pytest.mark.parametrize(
    ("overrides", "invalid_field"),
    [
        ({"reason_code": "Fabricated"}, "reason_code"),
        ({"resolution_code": "Fabricated"}, "resolution_code"),
    ],
)
def test_validate_codes_rejects_values_outside_either_vocabulary(
    overrides: dict[str, str],
    invalid_field: str,
) -> None:
    with pytest.raises(InvalidAnalysisOutputError, match=invalid_field):
        validate_codes(
            _analysis(**overrides),
            reason_codes=CodeVocabulary("ReasonCodes", ("CorporateAction",)),
            resolution_codes=CodeVocabulary("ResolutionCodes", ("Custodian",)),
        )


def test_comment_budget_reserves_the_longest_confidence_prefix() -> None:
    assert confidence_prefix("medium") == "Confidence: Medium\n"
    assert MAX_CONFIDENCE_PREFIX_LENGTH == len("Confidence: Medium\n")
    assert short_analysis_budget(100) == 81


def test_comment_validation_rejects_short_analysis_over_the_reserved_budget() -> None:
    with pytest.raises(InvalidAnalysisOutputError, match="81-character"):
        validate_comment_length(_analysis(short_analysis="x" * 82), record_comment_limit=100)


def test_truncation_preserves_the_hard_limit_and_ellipsis() -> None:
    assert truncate_text("alpha beta gamma", max_length=10) == "alpha b..."
    assert len(render_comment(_analysis(short_analysis="x" * 200), record_comment_limit=40)) == 40


def test_report_contains_both_codes_and_all_required_analysis_sections() -> None:
    report = render_report(
        exception_id="37889927",
        analysis=_analysis(),
        generated_at=datetime(2026, 9, 7, 12, 30, tzinfo=UTC),
    )

    assert "`CorporateAction`" in report
    assert "`Custodian`" in report
    assert "2026-09-07T12:30:00Z" in report
    assert "## Short analysis" in report
    assert "## Reasoning" in report
    assert "## Explanation" in report
