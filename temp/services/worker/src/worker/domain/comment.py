"""Deterministic CTC record-comment formatting and length budgeting."""

from worker.domain.analysis import Confidence, ExceptionAnalysis, InvalidAnalysisOutputError

_CONFIDENCE_LABELS: dict[Confidence, str] = {
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


def confidence_prefix(confidence: Confidence) -> str:
    return f"Confidence: {_CONFIDENCE_LABELS[confidence]}\n"


MAX_CONFIDENCE_PREFIX_LENGTH = max(
    len(confidence_prefix(confidence)) for confidence in _CONFIDENCE_LABELS
)


def short_analysis_budget(record_comment_limit: int) -> int:
    """Reserve enough characters for every valid confidence prefix."""
    if record_comment_limit <= MAX_CONFIDENCE_PREFIX_LENGTH:
        raise ValueError("Record comment limit must exceed the confidence prefix length")
    return record_comment_limit - MAX_CONFIDENCE_PREFIX_LENGTH


def validate_comment_length(analysis: ExceptionAnalysis, *, record_comment_limit: int) -> None:
    budget = short_analysis_budget(record_comment_limit)
    if len(analysis.short_analysis) > budget:
        raise InvalidAnalysisOutputError(f"short_analysis exceeds the {budget}-character limit")


def truncate_text(text: str, *, max_length: int) -> str:
    if max_length < 0:
        raise ValueError("Maximum length must not be negative")
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return f"{text[: max_length - 3].rstrip()}..."


def render_comment(analysis: ExceptionAnalysis, *, record_comment_limit: int) -> str:
    """Render a bounded comment even when called before strict post-validation."""
    prefix = confidence_prefix(analysis.confidence)
    short_analysis = truncate_text(
        analysis.short_analysis,
        max_length=record_comment_limit - len(prefix),
    )
    return f"{prefix}{short_analysis}"
