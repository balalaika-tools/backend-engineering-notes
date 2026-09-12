"""Tools owned by the bounded SQL-repair agent."""

from contextvars import ContextVar

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from worker.genai.shared.limits import consume_tool_call
from worker.ports.investigation.query_executor import (
    QueryExecutionError,
    QueryExecutor,
    QueryRejectedError,
    QueryUnavailableError,
)


class ExecuteSQLInput(BaseModel):
    query: str = Field(min_length=1, description="One repaired read-only SELECT or WITH query.")


def build_execute_sql_tool(
    *,
    query_executor: QueryExecutor,
    successful_results: ContextVar[list[str] | None],
) -> BaseTool:
    async def execute_sql(query: str) -> str:
        consume_tool_call()
        try:
            result = (await query_executor.execute(query)).render()
        except QueryUnavailableError:
            raise
        except (QueryExecutionError, QueryRejectedError) as exc:
            return f"SQL_ERROR: {exc}"
        current = successful_results.get()
        if current is not None:
            current.append(result)
        return result

    return StructuredTool.from_function(
        coroutine=execute_sql,
        name="execute_sql",
        description="Execute one repaired read-only query and return bounded JSON rows.",
        args_schema=ExecuteSQLInput,
    )
