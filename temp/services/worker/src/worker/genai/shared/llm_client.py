"""One retry boundary for all provider-backed LLM attempts."""

import asyncio
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, TypeVar

from botocore.exceptions import (  # type: ignore[import-untyped]
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from worker.domain.admission import WindowCounters

ResultT = TypeVar("ResultT")


@dataclass(frozen=True, slots=True)
class LLMRetryPolicy:
    max_attempts: int
    backoff_base_seconds: float
    backoff_cap_seconds: float


@dataclass(frozen=True, slots=True)
class TransientProviderFailure:
    retry_after_seconds: float | None


class LLMInvocationError(RuntimeError):
    """Stable GenAI-boundary failure that hides provider exception types."""


class TransientLLMError(LLMInvocationError):
    """An LLM call may succeed when the investigation is retried later."""


class LLMRetriesExhausted(TransientLLMError):
    """All short provider retries failed; the investigation may be retried."""

    def __init__(
        self,
        *,
        attempts: int,
        last_error: Exception,
        retry_after_seconds: float | None,
    ) -> None:
        self.attempts = attempts
        self.last_error = last_error
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"LLM retries exhausted after {attempts} attempts: {last_error}")


class PermanentLLMError(LLMInvocationError):
    """A provider attempt failed in a way that should not be retried."""


class ErrorClassifier(Protocol):
    def __call__(self, error: Exception) -> TransientProviderFailure | None: ...


class RetryingLLMClient:
    def __init__(
        self,
        *,
        policy: LLMRetryPolicy,
        counters: WindowCounters,
        classify_error: ErrorClassifier,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._policy = policy
        self._counters = counters
        self._classify_error = classify_error
        self._sleep = sleep
        self._uniform = uniform

    async def call(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        last_retry_after: float | None = None
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                result = await operation()
            except Exception as exc:
                failure = self._classify_error(exc)
                self._counters.record(
                    failed=failure is not None,
                    retry_after_seconds=(failure.retry_after_seconds if failure else None),
                )
                if failure is None:
                    raise PermanentLLMError("LLM invocation failed permanently") from exc
                last_retry_after = failure.retry_after_seconds
                if attempt == self._policy.max_attempts:
                    raise LLMRetriesExhausted(
                        attempts=attempt,
                        last_error=exc,
                        retry_after_seconds=last_retry_after,
                    ) from exc
                await self._sleep(self._delay(attempt, last_retry_after))
            else:
                self._counters.record(failed=False)
                return result
        raise AssertionError("Retry loop exited without a result or error")

    def _delay(self, attempt: int, retry_after_seconds: float | None) -> float:
        if retry_after_seconds is not None:
            return min(retry_after_seconds, self._policy.backoff_cap_seconds)
        ceiling = min(
            self._policy.backoff_cap_seconds,
            self._policy.backoff_base_seconds * (2 ** (attempt - 1)),
        )
        return self._uniform(0.0, ceiling)


def classify_provider_error(error: Exception) -> TransientProviderFailure | None:
    if isinstance(
        error,
        (TimeoutError, ReadTimeoutError, ConnectTimeoutError, EndpointConnectionError),
    ):
        return TransientProviderFailure(retry_after_seconds=None)
    status_code, headers, error_code = _response_details(error)
    transient_codes = {
        "ModelNotReadyException",
        "ServiceUnavailableException",
        "ThrottlingException",
        "TooManyRequestsException",
    }
    if (
        error_code not in transient_codes
        and status_code != 429
        and (status_code is None or status_code < 500)
    ):
        return None
    return TransientProviderFailure(
        retry_after_seconds=_parse_retry_after(_header(headers, "retry-after")),
    )


def _response_details(
    error: Exception,
) -> tuple[int | None, Mapping[str, object], str | None]:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        metadata = response.get("ResponseMetadata", {})
        details = response.get("Error", {})
        metadata = metadata if isinstance(metadata, Mapping) else {}
        details = details if isinstance(details, Mapping) else {}
        status_code = metadata.get("HTTPStatusCode")
        headers = metadata.get("HTTPHeaders", {})
        error_code = details.get("Code")
        return (
            status_code if isinstance(status_code, int) else None,
            headers if isinstance(headers, Mapping) else {},
            error_code if isinstance(error_code, str) else None,
        )
    status_code = getattr(response, "status_code", None)
    headers = getattr(response, "headers", {})
    if status_code is None:
        status_code = getattr(error, "status_code", None)
        headers = getattr(error, "headers", headers)
    return (
        status_code if isinstance(status_code, int) else None,
        headers if isinstance(headers, Mapping) else {},
        None,
    )


def _header(headers: Mapping[str, object], name: str) -> object:
    normalized = name.casefold()
    return next(
        (value for key, value in headers.items() if str(key).casefold() == normalized),
        None,
    )


def _parse_retry_after(value: object) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(str(value)))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
