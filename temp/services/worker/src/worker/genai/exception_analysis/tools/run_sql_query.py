"""Bounded CTC query tool with delegated SQL repair."""

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field
from worker.genai.shared.limits import consume_tool_call
from worker.genai.sql_fixer.fixer import SQLFixer, SQLFixFailedError
from worker.ports.investigation.query_executor import (
    QueryExecutionError,
    QueryExecutor,
    QueryRejectedError,
    QueryUnavailableError,
)


class RunSQLQueryInput(BaseModel):
    query: str = Field(
        min_length=1,
        description="One read-only SELECT or WITH query against the described CTC schema.",
    )
    intent: str = Field(
        default="",
        description="What the query should find; used to guide SQL repair after an error.",
    )


def build_run_sql_query(*, query_executor: QueryExecutor, sql_fixer: SQLFixer) -> BaseTool:
    async def run_sql_query(query: str, intent: str = "") -> str:
        consume_tool_call()
        try:
            return (await query_executor.execute(query)).render()
        except QueryRejectedError as exc:
            return f"SQL query rejected: {exc}"
        except QueryUnavailableError:
            raise
        except QueryExecutionError as exc:
            try:
                return await sql_fixer.fix_and_execute(
                    intent=intent or query,
                    failing_sql=query,
                    error_message=str(exc),
                )
            except SQLFixFailedError as fixer_error:
                return f"SQL query failed after repair: {fixer_error}"

    return StructuredTool.from_function(
        coroutine=run_sql_query,
        name="run_sql_query",
        description="Execute bounded read-only SQL, repairing database errors when possible.",
        args_schema=RunSQLQueryInput,
    )
