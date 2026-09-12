"""Invocation boundary for a configured SQL-fixer graph."""

from contextvars import ContextVar
from typing import Any, Protocol

from worker.genai.sql_fixer.prompts import build_user_prompt
from worker.genai.sql_fixer.schemas import SQLQueryResult


class SQLFixFailedError(RuntimeError):
    """The repair agent did not produce a successful bounded query result."""


class SQLFixer(Protocol):
    async def fix_and_execute(
        self,
        *,
        intent: str,
        failing_sql: str,
        error_message: str,
    ) -> str: ...


class SQLFixerAgent:
    def __init__(self, graph: Any, successful_results: ContextVar[list[str] | None]) -> None:
        self._graph = graph
        self._successful_results = successful_results

    async def fix_and_execute(
        self,
        *,
        intent: str,
        failing_sql: str,
        error_message: str,
    ) -> str:
        token = self._successful_results.set([])
        try:
            result = await self._graph.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": build_user_prompt(
                                intent=intent,
                                failing_sql=failing_sql,
                                error_message=error_message,
                            ),
                        }
                    ]
                }
            )
            structured = result.get("structured_response")
            successful = self._successful_results.get() or []
            if isinstance(structured, SQLQueryResult) and successful:
                return successful[-1]
            raise SQLFixFailedError("SQL fixer ended without a successful query execution")
        finally:
            self._successful_results.reset(token)
