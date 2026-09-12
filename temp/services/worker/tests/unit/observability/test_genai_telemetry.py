"""GenAI usage extraction and content-safe span attributes."""

from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from worker.observability.agent_counters import token_totals
from worker.observability.genai import (
    OTelModelCallback,
    observe_agent_invocation,
    trace_tool_call,
)


@pytest.mark.asyncio
async def test_model_callback_records_tokens_without_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("genai-test")
    monkeypatch.setattr(trace, "get_tracer", lambda *_args, **_kwargs: tracer)
    callback = OTelModelCallback()
    response = SimpleNamespace(
        generations=[
            [
                SimpleNamespace(
                    message=SimpleNamespace(
                        usage_metadata={
                            "input_tokens": 12,
                            "output_tokens": 7,
                        }
                    )
                )
            ]
        ]
    )

    with token_totals() as totals:
        await callback.on_chat_model_start(
            {"kwargs": {"model_id": "anthropic.test"}},
            [[SimpleNamespace(content="do not capture this")]],
            run_id="run-1",
        )
        await callback.on_llm_end(response, run_id="run-1")

    provider.shutdown()
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.attributes["gen_ai.request.model"] == "anthropic.test"
    assert span.attributes["gen_ai.usage.input_tokens"] == 12
    assert span.attributes["gen_ai.usage.output_tokens"] == 7
    assert totals.input_tokens == 12
    assert totals.output_tokens == 7
    assert totals.model_calls == 1
    assert all("content" not in key and "messages" not in key for key in span.attributes)
    assert not span.events


@pytest.mark.asyncio
async def test_model_callback_does_not_invent_missing_model_or_token_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("genai-missing-fields-test")
    monkeypatch.setattr(trace, "get_tracer", lambda *_args, **_kwargs: tracer)
    callback = OTelModelCallback()
    response = SimpleNamespace(
        generations=[[SimpleNamespace(message=SimpleNamespace(usage_metadata={}))]]
    )

    await callback.on_chat_model_start({}, [[]], run_id="run-2")
    await callback.on_llm_end(response, run_id="run-2")
    provider.shutdown()

    span = exporter.get_finished_spans()[0]
    assert span.name == "chat"
    assert "gen_ai.request.model" not in span.attributes
    assert "gen_ai.usage.input_tokens" not in span.attributes
    assert "gen_ai.usage.output_tokens" not in span.attributes


def test_agent_observation_owns_span_counters_and_parent_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("agent-observation-test")
    monkeypatch.setattr(trace, "get_tracer", lambda *_args, **_kwargs: tracer)

    with tracer.start_as_current_span("investigation"):
        with observe_agent_invocation("exception_analysis") as totals:
            totals.input_tokens = 12
            totals.output_tokens = 7
            totals.model_calls = 2
            totals.tool_calls = 1

    provider.shutdown()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    agent_span = spans["invoke_agent exception_analysis"]
    parent_span = spans["investigation"]
    assert agent_span.parent is not None
    assert agent_span.parent.span_id == parent_span.context.span_id
    assert agent_span.attributes["gen_ai.usage.input_tokens"] == 12
    assert agent_span.attributes["gen_ai.usage.output_tokens"] == 7
    assert agent_span.attributes["app.agent.inference_calls"] == 2
    assert agent_span.attributes["app.agent.tool_calls"] == 1
    assert parent_span.attributes["gen_ai.usage.input_tokens"] == 12
    assert parent_span.attributes["gen_ai.usage.output_tokens"] == 7


def test_agent_observation_marks_failures_without_span_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("failing-agent-observation-test")
    monkeypatch.setattr(trace, "get_tracer", lambda *_args, **_kwargs: tracer)

    with pytest.raises(RuntimeError, match="agent failed"):
        with observe_agent_invocation("exception_analysis"):
            raise RuntimeError("agent failed")

    provider.shutdown()
    span = exporter.get_finished_spans()[0]
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.status.status_code.name == "ERROR"
    assert not span.events


@pytest.mark.asyncio
async def test_tool_middleware_traces_physical_execution_and_counts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("tool-observation-test")
    monkeypatch.setattr(trace, "get_tracer", lambda *_args, **_kwargs: tracer)
    request = SimpleNamespace(tool_call={"name": "calculator"})

    async def handler(_request: object) -> str:
        return "42"

    with token_totals() as totals:
        result = await trace_tool_call.awrap_tool_call(request, handler)

    provider.shutdown()
    span = exporter.get_finished_spans()[0]
    assert result == "42"
    assert span.name == "execute_tool calculator"
    assert span.attributes["gen_ai.tool.name"] == "calculator"
    assert totals.tool_calls == 1
    assert not span.events


@pytest.mark.asyncio
async def test_tool_middleware_buckets_unknown_names_and_marks_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("failing-tool-observation-test")
    monkeypatch.setattr(trace, "get_tracer", lambda *_args, **_kwargs: tracer)
    request = SimpleNamespace(tool_call={"name": "untrusted-tool-name"})

    async def handler(_request: object) -> str:
        raise ValueError("bad tool input")

    with pytest.raises(ValueError, match="bad tool input"):
        await trace_tool_call.awrap_tool_call(request, handler)

    provider.shutdown()
    span = exporter.get_finished_spans()[0]
    assert span.name == "execute_tool unknown_tool"
    assert span.attributes["gen_ai.tool.name"] == "unknown_tool"
    assert span.attributes["error.type"] == "ValueError"
    assert span.status.status_code.name == "ERROR"
    assert not span.events
