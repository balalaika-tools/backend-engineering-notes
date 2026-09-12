"""LangChain model, tool, and agent telemetry for the worker."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langchain.agents.middleware import wrap_tool_call
from langchain_core.callbacks import AsyncCallbackHandler
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from worker.observability.agent_counters import (
    TokenTotals,
    current_token_totals,
    token_totals,
)
from worker.observability.metrics import (
    agent_duration,
    llm_attempts,
    model_duration,
    token_usage,
    tool_duration,
)

KNOWN_AGENTS = {"exception_analysis"}
KNOWN_TOOLS = {"calculator", "run_sql_query"}


@contextmanager
def observe_agent_invocation(agent_name: str) -> Iterator[TokenTotals]:
    """Trace and meter one bounded agent invocation."""
    name = agent_name if agent_name in KNOWN_AGENTS else "unknown_agent"
    started = time.perf_counter()
    error_type: str | None = None
    parent_span = trace.get_current_span()
    with (
        trace.get_tracer(__name__).start_as_current_span(
            f"invoke_agent {name}",
            record_exception=False,
            attributes={
                "gen_ai.operation.name": "invoke_agent",
                "gen_ai.agent.name": name,
                "app.telemetry.category": "genai",
            },
        ) as span,
        token_totals() as totals,
    ):
        try:
            yield totals
        except Exception as exc:
            error_type = type(exc).__name__
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", error_type)
            raise
        else:
            span.set_attribute("gen_ai.usage.input_tokens", totals.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", totals.output_tokens)
            span.set_attribute("app.agent.inference_calls", totals.model_calls)
            span.set_attribute("app.agent.tool_calls", totals.tool_calls)
            parent_span.set_attribute("gen_ai.usage.input_tokens", totals.input_tokens)
            parent_span.set_attribute("gen_ai.usage.output_tokens", totals.output_tokens)
        finally:
            attributes = {"gen_ai.agent.name": name}
            if error_type:
                attributes["error.type"] = error_type
            agent_duration.record(time.perf_counter() - started, attributes)


@wrap_tool_call
async def trace_tool_call(request: Any, handler: Any) -> Any:
    """Trace one physical execution of an allowlisted agent tool."""
    raw_name = str(request.tool_call.get("name", "unknown_tool"))
    name = raw_name if raw_name in KNOWN_TOOLS else "unknown_tool"
    started = time.perf_counter()
    error_type: str | None = None
    totals = current_token_totals()
    if totals:
        totals.tool_calls += 1
    try:
        with trace.get_tracer(__name__).start_as_current_span(
            f"execute_tool {name}",
            record_exception=False,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
                "gen_ai.tool.type": "function",
                "app.telemetry.category": "genai",
            },
        ) as span:
            try:
                return await handler(request)
            except Exception as exc:
                error_type = type(exc).__name__
                span.set_status(Status(StatusCode.ERROR))
                span.set_attribute("error.type", error_type)
                raise
    finally:
        attributes = {"gen_ai.tool.name": name, "gen_ai.tool.type": "function"}
        if error_type:
            attributes["error.type"] = error_type
        tool_duration.record(time.perf_counter() - started, attributes)


class OTelModelCallback(AsyncCallbackHandler):
    """Create one content-safe span for each physical model attempt."""

    def __init__(self) -> None:
        self._runs: dict[Any, tuple[trace.Span, float, str | None, str]] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del messages
        model = _request_model(serialized, metadata, kwargs)
        provider = "aws.bedrock"
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
            "app.telemetry.category": "genai",
        }
        if model is not None:
            attributes["gen_ai.request.model"] = model
        span = trace.get_tracer(__name__).start_span(
            f"chat {model}" if model is not None else "chat",
            kind=trace.SpanKind.CLIENT,
            attributes=attributes,
        )
        self._runs[run_id] = (span, time.perf_counter(), model, provider)
        totals = current_token_totals()
        if totals is not None:
            totals.model_calls += 1

    async def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        span, started, model, provider = run
        usage = _usage(response)
        _record_usage(span, usage, model=model, provider=provider)
        model_duration.record(time.perf_counter() - started, _model_attributes(model, provider))
        llm_attempts.add(1, {"result": "success"})
        span.end()

    async def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        span, started, model, provider = run
        span.set_status(Status(StatusCode.ERROR))
        span.set_attribute("error.type", type(error).__name__)
        attributes = _model_attributes(model, provider)
        attributes["error.type"] = type(error).__name__
        model_duration.record(time.perf_counter() - started, attributes)
        llm_attempts.add(1, {"result": "error"})
        span.end()


def _request_model(
    serialized: dict[str, Any], metadata: dict[str, Any] | None, kwargs: dict[str, Any]
) -> str | None:
    sources = [
        metadata or {},
        kwargs.get("invocation_params") or {},
        serialized.get("kwargs") or {},
    ]
    for source in sources:
        for key in ("ls_model_name", "model_id", "model", "model_name"):
            value = source.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _usage(response: Any) -> dict[str, int]:
    for generations in getattr(response, "generations", None) or []:
        for generation in generations:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if usage:
                return {
                    key: int(value)
                    for key in ("input_tokens", "output_tokens")
                    if (value := usage.get(key)) is not None
                }
    return {}


def _record_usage(
    span: trace.Span, usage: dict[str, int], *, model: str | None, provider: str
) -> None:
    for token_type, key, attribute in (
        ("input", "input_tokens", "gen_ai.usage.input_tokens"),
        ("output", "output_tokens", "gen_ai.usage.output_tokens"),
    ):
        value = usage.get(key)
        if value is None:
            continue
        span.set_attribute(attribute, value)
        attributes = _model_attributes(model, provider)
        attributes["gen_ai.token.type"] = token_type
        token_usage.record(value, attributes)
        totals = current_token_totals()
        if totals is not None:
            setattr(totals, key, getattr(totals, key) + value)


def _model_attributes(model: str | None, provider: str) -> dict[str, str]:
    attributes = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": provider,
    }
    if model is not None:
        attributes["gen_ai.request.model"] = model
    return attributes
