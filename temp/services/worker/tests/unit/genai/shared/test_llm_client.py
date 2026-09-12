"""The shared per-call LLM retry policy."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from worker.domain.admission import WindowCounters, WindowObservation
from worker.genai.shared.llm_client import (
    LLMRetriesExhausted,
    LLMRetryPolicy,
    PermanentLLMError,
    RetryingLLMClient,
    classify_provider_error,
)


@dataclass
class FakeResponse:
    status_code: int
    headers: dict[str, str]


class ProviderError(Exception):
    def __init__(self, status_code: int, *, retry_after: str | None = None) -> None:
        headers = {"retry-after": retry_after} if retry_after else {}
        self.response = FakeResponse(status_code=status_code, headers=headers)
        super().__init__(f"provider returned {status_code}")


def _client(
    counters: WindowCounters,
    delays: list[float],
    *,
    max_attempts: int,
) -> RetryingLLMClient:
    async def sleep(delay: float) -> None:
        delays.append(delay)

    return RetryingLLMClient(
        policy=LLMRetryPolicy(
            max_attempts=max_attempts,
            backoff_base_seconds=1,
            backoff_cap_seconds=30,
        ),
        counters=counters,
        classify_error=classify_provider_error,
        sleep=sleep,
        uniform=lambda _low, high: high,
    )


def _operation(
    outcomes: list[Exception | str],
) -> Callable[[], Awaitable[str]]:
    async def call() -> str:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return call


@pytest.mark.asyncio
async def test_transient_failures_retry_with_full_backoff_and_then_succeed() -> None:
    counters = WindowCounters()
    delays: list[float] = []
    client = _client(counters, delays, max_attempts=3)

    result = await client.call(_operation([ProviderError(500), TimeoutError("timeout"), "ok"]))

    assert result == "ok"
    assert delays == [1, 2]
    assert counters.snapshot_and_reset() == WindowObservation(total=3, failures=2)


@pytest.mark.asyncio
async def test_retry_after_replaces_jitter_delay() -> None:
    counters = WindowCounters()
    delays: list[float] = []
    client = _client(counters, delays, max_attempts=2)

    await client.call(_operation([ProviderError(429, retry_after="12"), "ok"]))

    assert delays == [12]
    assert counters.snapshot_and_reset().retry_after_seconds == 12


@pytest.mark.asyncio
async def test_bedrock_client_error_is_retried_with_provider_retry_after() -> None:
    counters = WindowCounters()
    delays: list[float] = []
    client = _client(counters, delays, max_attempts=2)
    error = ClientError(
        {
            "Error": {"Code": "ThrottlingException", "Message": "slow down"},
            "ResponseMetadata": {
                "HTTPStatusCode": 400,
                "HTTPHeaders": {"Retry-After": "7"},
            },
        },
        "Converse",
    )

    result = await client.call(_operation([error, "ok"]))

    assert result == "ok"
    assert delays == [7]
    assert counters.snapshot_and_reset() == WindowObservation(
        total=2,
        failures=1,
        retry_after_seconds=7,
    )


@pytest.mark.asyncio
async def test_eight_429_attempts_raise_transient_retries_exhausted() -> None:
    counters = WindowCounters()
    delays: list[float] = []
    client = _client(counters, delays, max_attempts=8)
    failures: list[Exception | str] = [ProviderError(429, retry_after="3") for _ in range(8)]

    with pytest.raises(LLMRetriesExhausted) as error:
        await client.call(_operation(failures))

    assert error.value.attempts == 8
    assert error.value.retry_after_seconds == 3
    assert delays == [3] * 7
    assert counters.snapshot_and_reset() == WindowObservation(
        total=8,
        failures=8,
        retry_after_seconds=3,
    )


@pytest.mark.asyncio
async def test_non_transient_error_is_not_retried_or_counted_as_failure() -> None:
    counters = WindowCounters()
    delays: list[float] = []
    client = _client(counters, delays, max_attempts=8)

    with pytest.raises(PermanentLLMError) as error:
        await client.call(_operation([ProviderError(400)]))

    assert isinstance(error.value.__cause__, ProviderError)
    assert delays == []
    assert counters.snapshot_and_reset() == WindowObservation(total=1, failures=0)
