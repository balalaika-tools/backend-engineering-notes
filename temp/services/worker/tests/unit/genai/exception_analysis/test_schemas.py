"""Runtime output-schema constraints."""

import pytest
from pydantic import ValidationError
from worker.domain.analysis import CodeVocabulary
from worker.genai.exception_analysis.schemas import AnalysisOutput, build_output_schema


def _schema() -> type[AnalysisOutput]:
    return build_output_schema(
        reason_codes=CodeVocabulary("ReasonCodes", ("CorporateAction", "Other")),
        resolution_codes=CodeVocabulary("ResolutionCodes", ("Custodian", "Internal")),
        record_comment_limit=100,
    )


def _payload(**overrides: str) -> dict[str, str]:
    payload = {
        "explanation": "The evidence supports a corporate action.",
        "reasoning": "The quantity ratio matches the event.",
        "reason_code": "CorporateAction",
        "resolution_code": "Custodian",
        "short_analysis": "A corporate action caused the quantity break.",
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


def test_out_of_vocabulary_code_fails_provider_facing_validation() -> None:
    with pytest.raises(ValidationError) as error:
        _schema().model_validate(_payload(reason_code="Fabricated"))

    assert error.value.errors()[0]["loc"] == ("reason_code",)


def test_json_schema_lists_both_runtime_code_enums_and_comment_limit() -> None:
    properties = _schema().model_json_schema()["properties"]

    assert properties["reason_code"]["enum"] == ["CorporateAction", "Other"]
    assert properties["resolution_code"]["enum"] == ["Custodian", "Internal"]
    assert properties["short_analysis"]["maxLength"] == 81


def test_schema_is_cached_for_the_same_vocabulary_hash_and_limit() -> None:
    assert _schema() is _schema()


def test_different_vocabulary_builds_a_different_schema() -> None:
    changed = build_output_schema(
        reason_codes=CodeVocabulary("ReasonCodes", ("Unknown",)),
        resolution_codes=CodeVocabulary("ResolutionCodes", ("Custodian", "Internal")),
        record_comment_limit=100,
    )

    assert changed is not _schema()
