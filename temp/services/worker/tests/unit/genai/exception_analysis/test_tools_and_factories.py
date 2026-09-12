"""Agent tool behavior and dependency-explicit factories."""

from collections.abc import Mapping
from typing import Any, cast

import pytest
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from worker.domain.admission import WindowCounters
from worker.genai.config_summarization.llm import build_model as build_config_model
from worker.genai.exception_analysis import agent as agent_module
from worker.genai.exception_analysis.llm import build_model, build_summarizer_model
from worker.genai.exception_analysis.middleware import (
    RetryingSummarizationMiddleware,
    build_middleware,
)
from worker.genai.exception_analysis.schemas import AnalysisOutput
from worker.genai.exception_analysis.tools import build_tools
from worker.genai.shared.limits import (
    RunToolLimitExceeded,
    ToolCallBudget,
    bind_tool_budget,
)
from worker.genai.shared.llm_client import LLMRetryPolicy, RetryingLLMClient
from worker.genai.shared.middleware import RetryingModelMiddleware
from worker.genai.sql_fixer.fixer import SQLFixer
from worker.genai.sql_fixer.llm import build_model as build_sql_fixer_model
from worker.ports.investigation.query_executor import (
    QueryExecutionError,
    QueryResult,
    QueryUnavailableError,
)


class FakeQueryExecutor:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    async def execute(self, query: str) -> QueryResult:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        rows: tuple[Mapping[str, object], ...] = ({"value": 42},)
        return QueryResult(rows=rows, truncated=False, row_limit=50)


class FakeSQLFixer(SQLFixer):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def fix_and_execute(
        self,
        *,
        intent: str,
        failing_sql: str,
        error_message: str,
    ) -> str:
        self.calls.append((intent, failing_sql, error_message))
        return '[{"value": 42}]'


def _retry_client() -> RetryingLLMClient:
    return RetryingLLMClient(
        policy=LLMRetryPolicy(max_attempts=1, backoff_base_seconds=1, backoff_cap_seconds=1),
        counters=WindowCounters(),
        classify_error=lambda _error: None,
    )


@pytest.mark.asyncio
async def test_tools_execute_safe_math_and_read_queries() -> None:
    executor = FakeQueryExecutor()
    tools = {
        tool.name: tool for tool in build_tools(query_executor=executor, sql_fixer=FakeSQLFixer())
    }

    calculation = await tools["calculator"].ainvoke({"expression": "sqrt(81) + 3 * 2"})
    query_result = await tools["run_sql_query"].ainvoke(
        {"query": "SELECT 42 AS value", "intent": "verify value"}
    )

    assert calculation == "15.0"
    assert '"value": 42' in query_result
    assert executor.queries == ["SELECT 42 AS value"]


@pytest.mark.asyncio
async def test_sql_database_error_invokes_the_fixer() -> None:
    fixer = FakeSQLFixer()
    tools = {
        tool.name: tool
        for tool in build_tools(
            query_executor=FakeQueryExecutor(QueryExecutionError("unknown column")),
            sql_fixer=fixer,
        )
    }

    result = await tools["run_sql_query"].ainvoke(
        {"query": "SELECT missing FROM ctc.records", "intent": "find records"}
    )

    assert result == '[{"value": 42}]'
    assert fixer.calls == [("find records", "SELECT missing FROM ctc.records", "unknown column")]


@pytest.mark.asyncio
async def test_sql_unavailability_propagates_without_invoking_the_fixer() -> None:
    fixer = FakeSQLFixer()
    tools = {
        tool.name: tool
        for tool in build_tools(
            query_executor=FakeQueryExecutor(QueryUnavailableError("capacity exhausted")),
            sql_fixer=fixer,
        )
    }

    with pytest.raises(QueryUnavailableError, match="capacity exhausted"):
        await tools["run_sql_query"].ainvoke({"query": "SELECT 1", "intent": "check capacity"})

    assert fixer.calls == []


@pytest.mark.asyncio
async def test_calculator_rejects_code_execution_and_obeys_shared_budget() -> None:
    tools = {
        tool.name: tool
        for tool in build_tools(query_executor=FakeQueryExecutor(), sql_fixer=FakeSQLFixer())
    }

    rejected = await tools["calculator"].ainvoke({"expression": "__import__('os').getcwd()"})
    with bind_tool_budget(ToolCallBudget(limit=1)):
        assert await tools["calculator"].ainvoke({"expression": "1 + 1"}) == "2"
        with pytest.raises(RunToolLimitExceeded):
            await tools["calculator"].ainvoke({"expression": "2 + 2"})

    assert rejected.startswith("Error:")


@pytest.mark.asyncio
async def test_calculator_rejects_huge_function_style_power() -> None:
    tools = {
        tool.name: tool
        for tool in build_tools(query_executor=FakeQueryExecutor(), sql_fixer=FakeSQLFixer())
    }

    result = await tools["calculator"].ainvoke({"expression": "pow(9, 9**9)"})

    assert result == "Error: Exponent is too large"


def test_model_factory_uses_only_explicit_bedrock_values() -> None:
    captured: dict[str, object] = {}
    sentinel = cast(BaseChatModel, object())

    def factory(model_id: str, **kwargs: object) -> BaseChatModel:
        captured["model_id"] = model_id
        captured.update(kwargs)
        return sentinel

    result = build_model(
        model_id="anthropic.test-model",
        region_name="eu-west-1",
        temperature=0.1,
        factory=factory,
    )

    assert result is sentinel
    assert captured == {
        "model_id": "anthropic.test-model",
        "model_provider": "bedrock_converse",
        "region_name": "eu-west-1",
        "temperature": 0.1,
        "max_retries": 0,
    }


@pytest.mark.parametrize(
    "builder",
    [build_config_model, build_summarizer_model, build_sql_fixer_model],
)
def test_each_model_role_retains_a_task_local_binding_factory(builder: Any) -> None:
    captured: dict[str, object] = {}
    sentinel = cast(BaseChatModel, object())

    def factory(model_id: str, **kwargs: object) -> BaseChatModel:
        captured["model_id"] = model_id
        captured.update(kwargs)
        return sentinel

    result = builder(
        model_id="task-model",
        region_name="eu-west-1",
        callbacks=[],
        factory=factory,
    )

    assert result is sentinel
    assert captured["model_provider"] == "bedrock_converse"
    assert builder.__module__.rsplit(".", maxsplit=1)[-1] == "llm"


def test_middleware_factory_includes_summary_retry_limits_and_prompt_cache() -> None:
    middleware = build_middleware(
        summarizer_model=FakeListChatModel(responses=["summary"]),
        retry_client=_retry_client(),
        summarization_trigger_tokens=1000,
        summarization_keep_messages=2,
        model_call_limit=10,
    )

    assert [type(value) for value in middleware[:3]] == [
        RetryingSummarizationMiddleware,
        RetryingModelMiddleware,
        ModelCallLimitMiddleware,
    ]
    assert type(middleware[3]).__name__ == "BedrockPromptCachingMiddleware"


def test_agent_factory_builds_from_resolved_dependencies_without_global_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}
    model = cast(BaseChatModel, object())
    graph = object()
    monkeypatch.setattr(
        agent_module,
        "create_agent",
        lambda **kwargs: calls.update({"agent": kwargs}) or graph,
    )

    harness = agent_module.build_agent(
        model=model,
        tools=(),
        middleware=(),
        system_prompt="system",
        output_schema=AnalysisOutput,
    )

    assert harness._graph is graph
    assert calls["agent"]["system_prompt"] == "system"
