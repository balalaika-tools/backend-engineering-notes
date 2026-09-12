"""Setup-only factory for the short-lived SQL-fixer graph."""

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from worker.genai.shared.llm_client import RetryingLLMClient
from worker.genai.shared.middleware import RetryingModelMiddleware
from worker.genai.sql_fixer.schemas import SQLQueryResult


def build_agent(
    *,
    model: BaseChatModel,
    tools: tuple[BaseTool, ...],
    system_prompt: str,
    retry_client: RetryingLLMClient,
    max_attempts: int,
) -> object:
    return create_agent(  # type: ignore[misc]
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        response_format=ToolStrategy(SQLQueryResult),
        middleware=(
            RetryingModelMiddleware(retry_client),
            ModelCallLimitMiddleware(run_limit=max_attempts + 1, exit_behavior="end"),
            ToolCallLimitMiddleware(run_limit=max_attempts, exit_behavior="end"),
        ),
        name="sql_fixer",
    )
