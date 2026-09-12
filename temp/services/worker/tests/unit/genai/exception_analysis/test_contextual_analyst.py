"""Context-specific exception-analysis harness caching."""

from dataclasses import replace
from typing import Any, cast

from worker.domain.control_context import ControlContext, ControlManual, DecodeValue
from worker.genai.exception_analysis.contextual_analyst import ContextualLLMExceptionAnalyst
from worker.genai.exception_analysis.harness import LangChainAnalysisHarness


def _context() -> ControlContext:
    return ControlContext(
        generation=1,
        positions=ControlManual("Positions", "positions-md5", "positions manual"),
        reference=ControlManual("Reference", "reference-md5", "reference manual"),
        reason_codes=(DecodeValue("Reason", "Reason", None),),
        resolution_codes=(DecodeValue("Resolution", "Resolution", None),),
        positions_rec_schema="positionsRec",
        positions_tenant_schema="tenant",
        reference_rec_schema="referenceRec",
        reference_tenant_schema="tenant",
        reason_code_feature_id="reason-feature-id",
        resolution_code_feature_id="resolution-feature-id",
    )


def _analyst(factory: Any) -> ContextualLLMExceptionAnalyst:
    return ContextualLLMExceptionAnalyst(
        harness_factory=factory,
        record_comment_limit=200,
        tool_call_limit=10,
    )


def test_harness_cache_discriminates_context_and_uses_injected_factory() -> None:
    calls: list[dict[str, Any]] = []
    harness = cast(LangChainAnalysisHarness, object())

    def fake_build_agent(**kwargs: Any) -> LangChainAnalysisHarness:
        calls.append(kwargs)
        return harness

    analyst = _analyst(fake_build_agent)
    context = _context()

    cached = analyst._analyst_for(context)
    assert analyst._analyst_for(context) is cached
    for changed in (
        replace(context, generation=2),
        replace(
            context,
            positions=replace(context.positions, config_md5="changed-manual"),
        ),
        replace(context, reason_codes=(DecodeValue("Other", "Other", None),)),
        replace(context, positions_rec_schema="changedSchema"),
    ):
        analyst._analyst_for(changed)

    assert len(calls) == 5
    assert len(analyst._cache) == 3
    assert {key[0] for key in analyst._cache} == {1}
    assert "positions manual" in cast(str, calls[0]["system_prompt"])
    assert '"changedSchema"' in cast(str, calls[-1]["schema_context"])
