"""Construction of the exception agent's bounded middleware stack."""

from typing import Any, cast

from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
)
from langchain_aws.middleware import BedrockPromptCachingMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from worker.genai.shared.llm_client import RetryingLLMClient
from worker.genai.shared.middleware import RetryingModelMiddleware


class RetryingSummarizationMiddleware(SummarizationMiddleware):
    """Route asynchronous summary calls through the shared provider retry policy."""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        retry_client: RetryingLLMClient,
        trigger_tokens: int,
        keep_messages: int,
    ) -> None:
        super().__init__(
            model=model,
            trigger=("tokens", trigger_tokens),
            keep=("messages", keep_messages),
        )
        self._summary_model = cast(Any, _RetryingSummaryModel(model, retry_client))


class _RetryingSummaryModel:
    def __init__(self, model: BaseChatModel, retry_client: RetryingLLMClient) -> None:
        self._model = model
        self._retry_client = retry_client

    def invoke(self, value: object, config: object = None) -> object:
        return self._model.invoke(value, config=config)  # type: ignore[arg-type]

    async def ainvoke(self, value: object, config: object = None) -> object:
        return await self._retry_client.call(
            lambda: self._model.ainvoke(value, config=config)  # type: ignore[arg-type]
        )


def build_middleware(
    *,
    summarizer_model: BaseChatModel,
    retry_client: RetryingLLMClient,
    summarization_trigger_tokens: int,
    summarization_keep_messages: int,
    model_call_limit: int,
) -> tuple[Any, ...]:
    """Order middleware so every physical retry consumes the run limits."""
    return (
        RetryingSummarizationMiddleware(
            model=summarizer_model,
            retry_client=retry_client,
            trigger_tokens=summarization_trigger_tokens,
            keep_messages=summarization_keep_messages,
        ),
        RetryingModelMiddleware(retry_client),
        ModelCallLimitMiddleware(run_limit=model_call_limit, exit_behavior="end"),
        BedrockPromptCachingMiddleware(
            ttl="5m",
            unsupported_model_behavior="ignore",
        ),
    )
