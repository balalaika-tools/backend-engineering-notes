"""Task-local model construction for control-configuration summarization."""

from collections.abc import Callable
from typing import Any, cast

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

ChatModelFactory = Callable[..., BaseChatModel]


def build_model(
    *,
    model_id: str,
    region_name: str,
    callbacks: list[Any] | None = None,
    factory: ChatModelFactory | None = None,
) -> BaseChatModel:
    """Build the configuration-summary model from resolved values."""
    constructor: Callable[..., Any] = factory or init_chat_model
    options: dict[str, Any] = {
        "model_provider": "bedrock_converse",
        "region_name": region_name,
        "temperature": 0.0,
        "max_retries": 0,
    }
    if callbacks is not None:
        options["callbacks"] = callbacks
    return cast(BaseChatModel, constructor(model_id, **options))
