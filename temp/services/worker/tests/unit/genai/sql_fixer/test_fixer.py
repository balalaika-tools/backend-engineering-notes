"""SQL fixer returns only database-authored query results."""

from contextvars import ContextVar

import pytest
from worker.genai.sql_fixer.fixer import SQLFixerAgent, SQLFixFailedError
from worker.genai.sql_fixer.schemas import SQLQueryResult


class FakeGraph:
    def __init__(
        self,
        successful_results: ContextVar[list[str] | None],
        *,
        tool_result: str | None,
    ) -> None:
        self._successful_results = successful_results
        self._tool_result = tool_result

    async def ainvoke(self, _input: object) -> dict[str, object]:
        if self._tool_result is not None:
            current = self._successful_results.get()
            assert current is not None
            current.append(self._tool_result)
        return {
            "structured_response": SQLQueryResult(
                final_sql="SELECT 1",
                result="model-authored plausible rows",
            )
        }


@pytest.mark.asyncio
async def test_fixer_returns_last_successful_tool_payload_not_model_text() -> None:
    results: ContextVar[list[str] | None] = ContextVar("test_results", default=None)
    fixer = SQLFixerAgent(FakeGraph(results, tool_result='[{"value": 1}]'), results)

    value = await fixer.fix_and_execute(intent="test", failing_sql="bad", error_message="bad")

    assert value == '[{"value": 1}]'


@pytest.mark.asyncio
async def test_fixer_rejects_structured_output_when_no_query_succeeded() -> None:
    results: ContextVar[list[str] | None] = ContextVar("test_results", default=None)
    fixer = SQLFixerAgent(FakeGraph(results, tool_result=None), results)

    with pytest.raises(SQLFixFailedError, match="without a successful query"):
        await fixer.fix_and_execute(intent="test", failing_sql="bad", error_message="bad")
