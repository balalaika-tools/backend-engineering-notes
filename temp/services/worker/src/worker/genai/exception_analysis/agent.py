"""Setup-only factory for the exception-analysis agent graph."""

from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from worker.genai.exception_analysis.harness import LangChainAnalysisHarness
from worker.genai.exception_analysis.schemas import AnalysisOutput


def build_agent(
    *,
    model: BaseChatModel,
    tools: tuple[BaseTool, ...],
    middleware: tuple[Any, ...],
    system_prompt: str,
    output_schema: type[AnalysisOutput],
) -> LangChainAnalysisHarness:
    """Build the complete harness solely from explicit resolved dependencies."""
    graph = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        response_format=ToolStrategy(output_schema),
        middleware=middleware,
        name="exception_analysis",
    )
    return LangChainAnalysisHarness(graph)
