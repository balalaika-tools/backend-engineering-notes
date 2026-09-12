"""Runtime-constrained structured output for exception analysis."""

import hashlib
import json
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from worker.domain.analysis import CodeVocabulary
from worker.domain.comment import short_analysis_budget


class AnalysisOutput(BaseModel):
    """Provider-facing base contract; runtime schemas narrow both code fields."""

    model_config = ConfigDict(extra="forbid")

    explanation: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
    reason_code: str
    resolution_code: str
    short_analysis: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]


def build_output_schema(
    *,
    reason_codes: CodeVocabulary,
    resolution_codes: CodeVocabulary,
    record_comment_limit: int,
) -> type[AnalysisOutput]:
    """Build and cache a schema whose JSON contract carries both live vocabularies."""
    vocabulary_hash = _vocabulary_hash(reason_codes.codes, resolution_codes.codes)
    return _cached_output_schema(
        vocabulary_hash,
        reason_codes.codes,
        resolution_codes.codes,
        short_analysis_budget(record_comment_limit),
    )


@lru_cache(maxsize=32)
def _cached_output_schema(
    vocabulary_hash: str,
    reason_codes: tuple[str, ...],
    resolution_codes: tuple[str, ...],
    short_analysis_max_length: int,
) -> type[AnalysisOutput]:
    # Python accepts unpacked runtime values here; static typing cannot model a dynamic Literal.
    reason_literal = Literal[*reason_codes]  # type: ignore[valid-type]
    resolution_literal = Literal[*resolution_codes]  # type: ignore[valid-type]
    model = create_model(
        f"ExceptionAnalysis_{vocabulary_hash}_{short_analysis_max_length}",
        __base__=AnalysisOutput,
        __module__=__name__,
        reason_code=(
            reason_literal,
            Field(description="One code from the runtime ReasonCodes vocabulary."),
        ),
        resolution_code=(
            resolution_literal,
            Field(description="One code from the runtime ResolutionCodes vocabulary."),
        ),
        short_analysis=(
            str,
            Field(
                min_length=1,
                max_length=short_analysis_max_length,
                description=(
                    "Plain-text CTC comment body without the confidence prefix; "
                    f"at most {short_analysis_max_length} characters."
                ),
            ),
        ),
    )
    return model


def _vocabulary_hash(
    reason_codes: tuple[str, ...],
    resolution_codes: tuple[str, ...],
) -> str:
    content = json.dumps(
        {"reason": reason_codes, "resolution": resolution_codes},
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(content).hexdigest()[:16]
