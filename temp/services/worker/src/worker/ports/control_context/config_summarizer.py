"""Technology-neutral control manual generation boundary."""

from typing import Protocol


class ConfigSummarizationUnavailableError(RuntimeError):
    """Manual generation may succeed when the investigation is retried."""

    error_code = "config_summarization_unavailable"


class ConfigSummarizationFailedError(RuntimeError):
    """The provider rejected summarization in a way that retrying cannot repair."""

    error_code = "config_summarization_failed"


class InvalidConfigSummaryError(RuntimeError):
    """The configured model produced no usable control manual."""


class ConfigSummarizer(Protocol):
    async def summarize(self, *, control_name: str, xml: bytes) -> str: ...
