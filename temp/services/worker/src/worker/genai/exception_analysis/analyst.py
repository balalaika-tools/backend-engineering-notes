"""Application-facing adapter for the bounded exception-analysis harness."""

import json
from typing import Protocol

from worker.domain.analysis import (
    CodeVocabulary,
    ExceptionAnalysis,
    InvalidAnalysisOutputError,
    validate_codes,
)
from worker.domain.comment import validate_comment_length
from worker.genai.exception_analysis.harness import (
    AnalysisOutputMissingError,
    HarnessRunLimitError,
    HarnessValidationError,
)
from worker.genai.exception_analysis.prompts import build_user_prompt
from worker.genai.exception_analysis.schemas import AnalysisOutput
from worker.genai.shared.limits import RunToolLimitExceeded, ToolCallBudget, bind_tool_budget
from worker.genai.shared.llm_client import PermanentLLMError, TransientLLMError
from worker.observability.genai import observe_agent_invocation
from worker.ports.investigation.exception_analyst import (
    AnalysisFailedError,
    AnalysisUnavailableError,
    ExceptionAnalysisInput,
    RunLimitExceededError,
)
from worker.ports.investigation.query_executor import QueryUnavailableError


class AnalysisHarness(Protocol):
    async def invoke(self, prompt: str) -> AnalysisOutput: ...


class LLMExceptionAnalyst:
    def __init__(
        self,
        *,
        harness: AnalysisHarness,
        reason_codes: CodeVocabulary,
        resolution_codes: CodeVocabulary,
        record_comment_limit: int,
        tool_call_limit: int,
    ) -> None:
        self._harness = harness
        self._reason_codes = reason_codes
        self._resolution_codes = resolution_codes
        self._record_comment_limit = record_comment_limit
        self._tool_call_limit = tool_call_limit

    async def analyze(self, value: ExceptionAnalysisInput) -> ExceptionAnalysis:
        with (
            observe_agent_invocation("exception_analysis"),
            bind_tool_budget(ToolCallBudget(limit=self._tool_call_limit)),
        ):
            prompt = _input_prompt(value)
            try:
                output = await self._harness.invoke(prompt)
                analysis = _to_domain(output)
                self._post_validate(analysis)
            except (HarnessValidationError, InvalidAnalysisOutputError) as exc:
                raise InvalidAnalysisOutputError(str(exc)) from exc
            except (
                AnalysisOutputMissingError,
                HarnessRunLimitError,
                RunToolLimitExceeded,
            ) as exc:
                raise RunLimitExceededError(str(exc)) from exc
            except (TransientLLMError, QueryUnavailableError, TimeoutError) as exc:
                raise AnalysisUnavailableError(str(exc)) from exc
            except PermanentLLMError as exc:
                raise AnalysisFailedError(str(exc)) from exc
            return analysis

    def _post_validate(self, analysis: ExceptionAnalysis) -> None:
        validate_codes(
            analysis,
            reason_codes=self._reason_codes,
            resolution_codes=self._resolution_codes,
        )
        validate_comment_length(
            analysis,
            record_comment_limit=self._record_comment_limit,
        )


def _input_prompt(value: ExceptionAnalysisInput) -> str:
    context = json.dumps(
        {
            "exception": value.exception,
            "linked_records": value.linked_records,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    return build_user_prompt(
        exception_id=value.exception_id,
        current_datetime=value.current_datetime.isoformat(),
        exception_context=context,
    )


def _to_domain(output: AnalysisOutput) -> ExceptionAnalysis:
    return ExceptionAnalysis(
        explanation=output.explanation,
        reasoning=output.reasoning,
        reason_code=output.reason_code,
        resolution_code=output.resolution_code,
        short_analysis=output.short_analysis,
        confidence=output.confidence,
    )
