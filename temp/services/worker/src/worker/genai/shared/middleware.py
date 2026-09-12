"""LangChain middleware that applies the one shared per-attempt retry boundary."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from worker.genai.shared.llm_client import RetryingLLMClient


class RetryingModelMiddleware(AgentMiddleware):
    """Retry each model request through the fleet-wide LLM attempt counter."""

    def __init__(self, client: RetryingLLMClient) -> None:
        self._client = client

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await self._client.call(lambda: handler(request))
