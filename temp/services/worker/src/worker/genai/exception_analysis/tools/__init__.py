"""Analysis tool assembly from capability-local tool modules."""

from langchain_core.tools import BaseTool
from worker.genai.exception_analysis.tools.calculator import build_calculator
from worker.genai.exception_analysis.tools.run_sql_query import build_run_sql_query
from worker.genai.sql_fixer.fixer import SQLFixer
from worker.ports.investigation.query_executor import QueryExecutor


def build_tools(*, query_executor: QueryExecutor, sql_fixer: SQLFixer) -> tuple[BaseTool, ...]:
    return (
        build_run_sql_query(query_executor=query_executor, sql_fixer=sql_fixer),
        build_calculator(),
    )


__all__ = ["build_tools"]
