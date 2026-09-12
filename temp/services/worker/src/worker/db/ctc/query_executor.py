"""Prevalidated, row-bounded execution on the read-only CTC pool."""

import asyncio
import math
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import cast

import sqlparse
from asyncpg import exceptions as asyncpg_exceptions  # type: ignore[import-untyped]
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlparse.tokens import DML
from worker.ports.investigation.query_executor import (
    QueryExecutionError,
    QueryRejectedError,
    QueryResult,
    QueryUnavailableError,
)


class CtcQueryExecutor:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        row_limit: int,
        statement_timeout_seconds: float,
        work_mem_bytes: int,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._engine = engine
        self._row_limit = row_limit
        self._statement_timeout_ms = max(1, math.ceil(statement_timeout_seconds * 1000))
        self._work_mem_kib = max(64, math.ceil(work_mem_bytes / 1024))
        self._sleep = sleep
        self._jitter = jitter

    async def execute(self, query: str) -> QueryResult:
        validated = validate_read_query(query)
        bounded = f"SELECT * FROM ({validated}) AS bounded_agent_query LIMIT {self._row_limit + 1}"
        for attempt in range(3):
            try:
                rows = await self._execute_once(bounded)
                return bounded_result([dict(row) for row in rows], row_limit=self._row_limit)
            except (OSError, SQLAlchemyError, asyncpg_exceptions.PostgresError) as exc:
                if not is_retryable_connection_error(exc):
                    raise QueryExecutionError(str(exc)) from exc
                if attempt == 2:
                    raise QueryUnavailableError(str(exc)) from exc
                delay = (2**attempt) * self._jitter(0.75, 1.25)
                await self._sleep(delay)
        raise AssertionError("SQL retry loop did not return or raise")

    async def _execute_once(self, bounded_query: str) -> Sequence[Mapping[str, object]]:
        async with self._engine.begin() as connection:
            await connection.exec_driver_sql("SET LOCAL TRANSACTION READ ONLY")
            await connection.exec_driver_sql(
                f"SET LOCAL statement_timeout = '{self._statement_timeout_ms}ms'"
            )
            await connection.exec_driver_sql(f"SET LOCAL work_mem = '{self._work_mem_kib}kB'")
            rows = (await connection.exec_driver_sql(bounded_query)).mappings().all()
            return cast(Sequence[Mapping[str, object]], rows)


_RETRYABLE_DRIVER_ERRORS = (
    asyncpg_exceptions.PostgresConnectionError,
    asyncpg_exceptions.CannotConnectNowError,
    asyncpg_exceptions.TooManyConnectionsError,
)
_RETRYABLE_OS_ERRORS = (ConnectionRefusedError, ConnectionResetError, BrokenPipeError)


def is_retryable_connection_error(error: BaseException) -> bool:
    """Return whether structured diagnostics identify transient connectivity."""
    if isinstance(error, PoolTimeoutError):
        return True
    if isinstance(error, _RETRYABLE_OS_ERRORS):
        return True
    if isinstance(error, DBAPIError):
        return error.connection_invalidated or isinstance(
            error.orig,
            (*_RETRYABLE_DRIVER_ERRORS, *_RETRYABLE_OS_ERRORS),
        )
    return isinstance(error, _RETRYABLE_DRIVER_ERRORS)


def validate_read_query(query: str) -> str:
    statements = [statement for statement in sqlparse.parse(query) if str(statement).strip()]
    if len(statements) != 1 or statements[0].get_type() != "SELECT":  # type: ignore[no-untyped-call]
        raise QueryRejectedError("Only one read-only SELECT or WITH statement is allowed")
    tokens = statements[0].flatten()  # type: ignore[no-untyped-call]
    dml_tokens = [token.normalized for token in tokens if token.ttype is DML]
    if not dml_tokens or any(token != "SELECT" for token in dml_tokens):
        raise QueryRejectedError("Writable CTEs and locking statements are not allowed")
    return str(statements[0]).strip().removesuffix(";").rstrip()


def bounded_result(
    rows: Sequence[Mapping[str, object]],
    *,
    row_limit: int,
) -> QueryResult:
    return QueryResult(
        rows=tuple(dict(row) for row in rows[:row_limit]),
        truncated=len(rows) > row_limit,
        row_limit=row_limit,
    )
