"""SQL-fixer agent factory wiring."""

from typing import Any, cast

import pytest
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from worker.domain.admission import WindowCounters
from worker.genai.shared.llm_client import LLMRetryPolicy, RetryingLLMClient
from worker.genai.sql_fixer import agent as agent_module
from worker.genai.sql_fixer.schemas import SQLQueryResult


def _retry_client() -> RetryingLLMClient:
    return RetryingLLMClient(
        policy=LLMRetryPolicy(max_attempts=1, backoff_base_seconds=1, backoff_cap_seconds=1),
        counters=WindowCounters(),
        classify_error=lambda _error: None,
    )


def test_factory_builds_the_graph_from_explicit_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    graph = object()
    monkeypatch.setattr(
        agent_module,
        "create_agent",
        lambda **kwargs: captured.update(kwargs) or graph,
    )
    model = cast(BaseChatModel, object())
    tool = cast(BaseTool, object())

    result = agent_module.build_agent(
        model=model,
        tools=(tool,),
        system_prompt="repair safely",
        retry_client=_retry_client(),
        max_attempts=3,
    )

    assert result is graph
    assert captured["model"] is model
    assert captured["tools"] == (tool,)
    assert captured["system_prompt"] == "repair safely"
    response_format = captured["response_format"]
    assert isinstance(response_format, ToolStrategy)
    assert response_format.schema is SQLQueryResult
