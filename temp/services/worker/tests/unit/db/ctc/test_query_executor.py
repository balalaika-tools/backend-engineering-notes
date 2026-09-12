"""Read-query allowlist and bounded rendering."""

import asyncio
from collections.abc import Mapping
from typing import Any, cast

import pytest
from asyncpg import exceptions as asyncpg_exceptions  # type: ignore[import-untyped]
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine
from worker.db.ctc.query_executor import CtcQueryExecutor, bounded_result, validate_read_query
from worker.ports.investigation.query_executor import (
    QueryExecutionError,
    QueryRejectedError,
    QueryUnavailableError,
)


class FakeResult:
    def mappings(self) -> "FakeResult":
        return self

    def all(self) -> list[Mapping[str, object]]:
        return [{"value": 42}]


class FakeConnection:
    def __init__(self, outcome: BaseException | None) -> None:
        self.outcome = outcome

    async def exec_driver_sql(self, statement: str) -> FakeResult | None:
        if statement.startswith("SELECT * FROM"):
            if self.outcome is not None:
                raise self.outcome
            return FakeResult()
        return None


class FakeTransaction:
    def __init__(self, engine: "FakeEngine", outcome: BaseException | None) -> None:
        self.engine = engine
        self.connection = FakeConnection(outcome)

    async def __aenter__(self) -> FakeConnection:
        self.engine.active_transactions += 1
        self.engine.max_active_transactions = max(
            self.engine.max_active_transactions, self.engine.active_transactions
        )
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        self.engine.active_transactions -= 1
        self.engine.completed_transactions += 1


class FakeEngine:
    def __init__(self, outcomes: list[BaseException | None]) -> None:
        self.outcomes = outcomes
        self.attempts = 0
        self.active_transactions = 0
        self.max_active_transactions = 0
        self.completed_transactions = 0

    def begin(self) -> FakeTransaction:
        outcome = self.outcomes[self.attempts]
        self.attempts += 1
        return FakeTransaction(self, outcome)


def _executor(
    engine: FakeEngine,
    sleeps: list[float],
) -> CtcQueryExecutor:
    async def record_sleep(delay: float) -> None:
        assert engine.active_transactions == 0
        sleeps.append(delay)

    return CtcQueryExecutor(
        cast(AsyncEngine, cast(Any, engine)),
        row_limit=10,
        statement_timeout_seconds=5,
        work_mem_bytes=1024 * 1024,
        sleep=record_sleep,
        jitter=lambda _minimum, _maximum: 1.0,
    )


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM positions.records",
        "WITH matched AS (SELECT * FROM positions.records) SELECT * FROM matched",
    ],
)
def test_select_and_read_only_with_are_allowed(query: str) -> None:
    assert validate_read_query(query) == query


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE positions.records SET quantity = 0",
        "WITH changed AS (DELETE FROM positions.records RETURNING *) SELECT * FROM changed",
        "SELECT 1; DELETE FROM positions.records",
    ],
)
def test_writes_and_multiple_statements_are_rejected(query: str) -> None:
    with pytest.raises(QueryRejectedError):
        validate_read_query(query)


def test_result_is_capped_with_a_truncation_notice() -> None:
    result = bounded_result(
        [{"id": 1}, {"id": 2}, {"id": 3}],
        row_limit=2,
    )

    assert result.rows == ({"id": 1}, {"id": 2})
    assert result.truncated is True
    assert "only the first 2" in result.render()


@pytest.mark.asyncio
async def test_transient_failure_retries_with_fresh_transactions_and_bounded_delays() -> None:
    engine = FakeEngine([ConnectionRefusedError(), PoolTimeoutError(), None])
    sleeps: list[float] = []

    result = await _executor(engine, sleeps).execute("SELECT 42 AS value")

    assert result.rows == ({"value": 42},)
    assert engine.attempts == 3
    assert engine.completed_transactions == 3
    assert engine.max_active_transactions == 1
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_exhausted_transient_failure_has_three_attempts_and_no_final_sleep() -> None:
    engine = FakeEngine([asyncpg_exceptions.TooManyConnectionsError("capacity") for _ in range(3)])
    sleeps: list[float] = []

    with pytest.raises(QueryUnavailableError):
        await _executor(engine, sleeps).execute("SELECT 1")

    assert engine.attempts == 3
    assert engine.completed_transactions == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    "failure",
    [
        asyncpg_exceptions.InsufficientPrivilegeError("permission denied"),
        asyncpg_exceptions.QueryCanceledError("statement timeout"),
        asyncpg_exceptions.DataError("invalid data"),
        OSError("unclassified operating-system failure"),
    ],
)
@pytest.mark.asyncio
async def test_permanent_timeout_and_unclassified_failures_are_not_retried(
    failure: BaseException,
) -> None:
    engine = FakeEngine([failure])
    sleeps: list[float] = []

    with pytest.raises(QueryExecutionError):
        await _executor(engine, sleeps).execute("SELECT 1")

    assert engine.attempts == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_cancellation_propagates_after_transaction_cleanup() -> None:
    engine = FakeEngine([asyncio.CancelledError()])
    sleeps: list[float] = []

    with pytest.raises(asyncio.CancelledError):
        await _executor(engine, sleeps).execute("SELECT 1")

    assert engine.completed_transactions == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_cancellation_during_backoff_stops_before_the_next_attempt() -> None:
    engine = FakeEngine([ConnectionResetError(), None])

    async def cancel_sleep(_delay: float) -> None:
        assert engine.active_transactions == 0
        raise asyncio.CancelledError

    executor = CtcQueryExecutor(
        cast(AsyncEngine, cast(Any, engine)),
        row_limit=10,
        statement_timeout_seconds=5,
        work_mem_bytes=1024 * 1024,
        sleep=cancel_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await executor.execute("SELECT 1")

    assert engine.attempts == 1
    assert engine.completed_transactions == 1
