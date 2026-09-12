"""SQL-fixer tool behavior at the query port boundary."""

from collections.abc import Mapping
from contextvars import ContextVar

import pytest
from worker.genai.sql_fixer.tools import build_execute_sql_tool
from worker.ports.investigation.query_executor import (
    QueryExecutionError,
    QueryResult,
    QueryUnavailableError,
)


class FakeQueryExecutor:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def execute(self, query: str) -> QueryResult:
        if self._error is not None:
            raise self._error
        rows: tuple[Mapping[str, object], ...] = ({"query": query},)
        return QueryResult(rows=rows, truncated=False, row_limit=10)


@pytest.mark.asyncio
async def test_successful_query_result_is_available_to_the_fixer_wrapper() -> None:
    successful: ContextVar[list[str] | None] = ContextVar("successful", default=None)
    token = successful.set([])
    try:
        tool = build_execute_sql_tool(
            query_executor=FakeQueryExecutor(),
            successful_results=successful,
        )

        result = await tool.ainvoke({"query": "SELECT 1"})

        assert '"query": "SELECT 1"' in result
        assert successful.get() == [result]
    finally:
        successful.reset(token)


@pytest.mark.asyncio
async def test_database_rejection_is_returned_to_the_repair_agent() -> None:
    successful: ContextVar[list[str] | None] = ContextVar("successful", default=None)
    tool = build_execute_sql_tool(
        query_executor=FakeQueryExecutor(QueryExecutionError("unknown column")),
        successful_results=successful,
    )

    result = await tool.ainvoke({"query": "SELECT missing"})

    assert result == "SQL_ERROR: unknown column"
    assert successful.get() is None


@pytest.mark.asyncio
async def test_database_unavailability_propagates_out_of_the_fixer_tool() -> None:
    successful: ContextVar[list[str] | None] = ContextVar("successful", default=None)
    tool = build_execute_sql_tool(
        query_executor=FakeQueryExecutor(QueryUnavailableError("capacity exhausted")),
        successful_results=successful,
    )

    with pytest.raises(QueryUnavailableError, match="capacity exhausted"):
        await tool.ainvoke({"query": "SELECT 1"})

    assert successful.get() is None
