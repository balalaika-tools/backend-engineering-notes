"""Invocation boundary for a configured exception-analysis graph."""

from typing import Any

from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain.agents.structured_output import StructuredOutputValidationError
from worker.genai.exception_analysis.schemas import AnalysisOutput


class AnalysisOutputMissingError(RuntimeError):
    """The agent ended without satisfying its structured-output contract."""


class HarnessValidationError(ValueError):
    """The provider response failed the runtime structured-output schema."""


class HarnessRunLimitError(RuntimeError):
    """LangChain stopped the run at a configured model or tool limit."""


class LangChainAnalysisHarness:
    def __init__(self, graph: Any) -> None:
        self._graph = graph

    async def invoke(self, prompt: str) -> AnalysisOutput:
        try:
            result = await self._graph.ainvoke({"messages": [{"role": "user", "content": prompt}]})
        except StructuredOutputValidationError as exc:
            raise HarnessValidationError(str(exc.source)) from exc
        except (ModelCallLimitExceededError, ToolCallLimitExceededError) as exc:
            raise HarnessRunLimitError(str(exc)) from exc
        structured = result.get("structured_response")
        if not isinstance(structured, AnalysisOutput):
            raise AnalysisOutputMissingError("Analysis agent ended without structured output")
        return structured
