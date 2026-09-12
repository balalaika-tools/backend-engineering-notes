"""Pure Markdown rendering for completed exception analyses."""

from datetime import UTC, datetime

from worker.domain.analysis import ExceptionAnalysis


def render_report(
    *,
    exception_id: str,
    analysis: ExceptionAnalysis,
    generated_at: datetime,
) -> str:
    timestamp = _utc_timestamp(generated_at)
    confidence = analysis.confidence.capitalize()
    return f"""\
# Exception Analysis Report

| Field | Value |
|---|---|
| Exception ID | `{exception_id}` |
| Analysis timestamp | {timestamp} |
| Reason code | `{analysis.reason_code}` |
| Resolution code | `{analysis.resolution_code}` |
| Confidence | {confidence} |

## Short analysis

{analysis.short_analysis}

## Reasoning

{analysis.reasoning}

## Explanation

{analysis.explanation}
"""


def _utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Report timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
