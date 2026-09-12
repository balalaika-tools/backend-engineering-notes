"""Task-local AWS Bedrock model construction for exception analysis."""

from collections.abc import Callable
from typing import Any, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

ChatModelFactory = Callable[..., BaseChatModel]


def build_model(
    *,
    model_id: str,
    region_name: str,
    temperature: float = 0.0,
    callbacks: list[Any] | None = None,
    factory: ChatModelFactory | None = None,
) -> BaseChatModel:
    constructor: Callable[..., Any] = factory or init_chat_model
    options: dict[str, Any] = {
        "model_provider": "bedrock_converse",
        "region_name": region_name,
        "temperature": temperature,
        "max_retries": 0,
    }
    if callbacks is not None:
        options["callbacks"] = callbacks
    return cast(BaseChatModel, constructor(model_id, **options))


def build_summarizer_model(
    *,
    model_id: str,
    region_name: str,
    callbacks: list[Any] | None = None,
    factory: ChatModelFactory | None = None,
) -> BaseChatModel:
    """Build the analysis middleware's summarization model."""
    return build_model(
        model_id=model_id,
        region_name=region_name,
        callbacks=callbacks,
        factory=factory,
    )
