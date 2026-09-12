"""Context-aware analyst adapter with generation-scoped harness caching."""

from typing import Protocol

from worker.domain.analysis import CodeVocabulary, ExceptionAnalysis
from worker.domain.control_context import ControlContext
from worker.genai.exception_analysis.analyst import LLMExceptionAnalyst
from worker.genai.exception_analysis.harness import LangChainAnalysisHarness
from worker.genai.exception_analysis.prompts import build_system_prompt
from worker.genai.exception_analysis.schemas import AnalysisOutput, build_output_schema
from worker.ports.investigation.exception_analyst import ExceptionAnalysisInput


class AnalysisHarnessFactory(Protocol):
    def __call__(
        self,
        *,
        system_prompt: str,
        output_schema: type[AnalysisOutput],
        schema_context: str,
    ) -> LangChainAnalysisHarness: ...


class ContextualLLMExceptionAnalyst:
    def __init__(
        self,
        *,
        harness_factory: AnalysisHarnessFactory,
        record_comment_limit: int,
        tool_call_limit: int,
    ) -> None:
        self._harness_factory = harness_factory
        self._record_comment_limit = record_comment_limit
        self._tool_call_limit = tool_call_limit
        self._cache: dict[tuple[object, ...], LLMExceptionAnalyst] = {}
        self._generation: int | None = None

    async def analyze(
        self,
        value: ExceptionAnalysisInput,
        *,
        context: ControlContext,
    ) -> ExceptionAnalysis:
        return await self._analyst_for(context).analyze(value)

    def _analyst_for(self, context: ControlContext) -> LLMExceptionAnalyst:
        if context.generation != self._generation:
            self._cache.clear()
            self._generation = context.generation
        key = _cache_key(context)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        reason_codes = CodeVocabulary(
            "ReasonCodes", tuple(value.code for value in context.reason_codes)
        )
        resolution_codes = CodeVocabulary(
            "ResolutionCodes", tuple(value.code for value in context.resolution_codes)
        )
        harness = self._harness_factory(
            system_prompt=build_system_prompt(
                positions_manual=context.positions.content,
                reference_manual=context.reference.content,
                reason_codes=context.reason_codes,
                resolution_codes=context.resolution_codes,
                record_comment_limit=self._record_comment_limit,
            ),
            output_schema=build_output_schema(
                reason_codes=reason_codes,
                resolution_codes=resolution_codes,
                record_comment_limit=self._record_comment_limit,
            ),
            schema_context=_schema_context(context),
        )
        analyst = LLMExceptionAnalyst(
            harness=harness,
            reason_codes=reason_codes,
            resolution_codes=resolution_codes,
            record_comment_limit=self._record_comment_limit,
            tool_call_limit=self._tool_call_limit,
        )
        self._cache[key] = analyst
        return analyst


def _cache_key(context: ControlContext) -> tuple[object, ...]:
    return (
        context.generation,
        context.positions.config_md5,
        context.reference.config_md5,
        tuple(value.code for value in context.reason_codes),
        tuple(value.code for value in context.resolution_codes),
        context.positions_rec_schema,
        context.positions_tenant_schema,
        context.reference_rec_schema,
        context.reference_tenant_schema,
    )


def _schema_context(context: ControlContext) -> str:
    return (
        "CTC PostgreSQL schema names are API-discovered and normalized to PostgreSQL identifier case. "
        f'Positions reconciliation schema: "{context.positions_rec_schema}"; '
        f'positions tenant schema: "{context.positions_tenant_schema}"; '
        f'reference reconciliation schema: "{context.reference_rec_schema}"; '
        f'reference tenant schema: "{context.reference_tenant_schema}". '
        "Double-quote these schema names in SQL."
    )
