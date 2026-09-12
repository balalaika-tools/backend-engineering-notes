"""LLM-backed implementation of the control manual capability."""

from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from worker.genai.config_summarization.prompts import SYSTEM_PROMPT, build_user_prompt
from worker.genai.shared.llm_client import PermanentLLMError, RetryingLLMClient, TransientLLMError
from worker.ports.control_context.config_summarizer import (
    ConfigSummarizationFailedError,
    ConfigSummarizationUnavailableError,
    InvalidConfigSummaryError,
)


class ConfigSummaryGenerator(Protocol):
    async def generate(self, *, system_prompt: str, user_prompt: str) -> str: ...


class ChatModelConfigSummaryGenerator:
    """Invoke a configured chat model without exposing LangChain to the port."""

    def __init__(self, *, model: BaseChatModel, retry_client: RetryingLLMClient) -> None:
        self._model = model
        self._retry_client = retry_client

    async def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        response = await self._retry_client.call(
            lambda: self._model.ainvoke(
                [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
            )
        )
        return _text_content(response.content)


class LLMConfigSummarizer:
    def __init__(self, generator: ConfigSummaryGenerator) -> None:
        self._generator = generator

    async def summarize(self, *, control_name: str, xml: bytes) -> str:
        try:
            decoded_xml = xml.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidConfigSummaryError("Control XML must be UTF-8") from exc

        try:
            manual = await self._generator.generate(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(control_name=control_name, xml=decoded_xml),
            )
        except TransientLLMError as exc:
            raise ConfigSummarizationUnavailableError("Control summarization failed") from exc
        except PermanentLLMError as exc:
            raise ConfigSummarizationFailedError(
                "Control summarization failed permanently"
            ) from exc
        if not manual.strip():
            raise InvalidConfigSummaryError("Control summarizer returned an empty manual")
        return manual.strip()


def _text_content(content: str | list[str | dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)
