"""Analysis invocation, repair, post-validation, and error mapping."""

from datetime import UTC, datetime

import pytest
from worker.domain.analysis import CodeVocabulary, InvalidAnalysisOutputError
from worker.genai.exception_analysis.analyst import LLMExceptionAnalyst
from worker.genai.exception_analysis.harness import HarnessRunLimitError, HarnessValidationError
from worker.genai.exception_analysis.schemas import AnalysisOutput
from worker.genai.shared.llm_client import PermanentLLMError, TransientLLMError
from worker.ports.investigation.exception_analyst import (
    AnalysisFailedError,
    AnalysisUnavailableError,
    ExceptionAnalysisInput,
    RunLimitExceededError,
)
from worker.ports.investigation.query_executor import QueryUnavailableError


class FakeHarness:
    def __init__(self, outcomes: list[AnalysisOutput | Exception]) -> None:
        self.outcomes = outcomes
        self.prompts: list[str] = []

    async def invoke(self, prompt: str) -> AnalysisOutput:
        self.prompts.append(prompt)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _output(**overrides: str) -> AnalysisOutput:
    values = {
        "explanation": "A corporate action matches the quantity change.",
        "reasoning": "The event ratio reconciles the difference.",
        "reason_code": "CorporateAction",
        "resolution_code": "Custodian",
        "short_analysis": "Corporate action evidence explains the break.",
        "confidence": "high",
    }
    values.update(overrides)
    return AnalysisOutput.model_validate(values)


def _input() -> ExceptionAnalysisInput:
    return ExceptionAnalysisInput(
        exception_id="37889927",
        current_datetime=datetime(2026, 9, 7, 12, 0, tzinfo=UTC),
        exception={"netquantity": "100"},
        linked_records=({"system": "Internal", "quantity": "900"},),
    )


def _analyst(harness: FakeHarness) -> LLMExceptionAnalyst:
    return LLMExceptionAnalyst(
        harness=harness,
        reason_codes=CodeVocabulary("ReasonCodes", ("CorporateAction", "Other")),
        resolution_codes=CodeVocabulary("ResolutionCodes", ("Custodian", "Internal")),
        record_comment_limit=200,
        tool_call_limit=50,
    )


@pytest.mark.asyncio
async def test_success_returns_domain_analysis_with_prefetched_input() -> None:
    harness = FakeHarness([_output()])

    result = await _analyst(harness).analyze(_input())

    assert result.reason_code == "CorporateAction"
    assert result.resolution_code == "Custodian"
    assert "&quot;netquantity&quot;: &quot;100&quot;" in harness.prompts[0]


@pytest.mark.asyncio
async def test_terminal_harness_validation_failure_is_permanent() -> None:
    harness = FakeHarness([HarnessValidationError("reason_code must be an enum"), _output()])

    with pytest.raises(InvalidAnalysisOutputError, match="reason_code must be an enum"):
        await _analyst(harness).analyze(_input())

    assert len(harness.prompts) == 1


@pytest.mark.asyncio
async def test_post_validation_bypass_is_rejected_without_a_fresh_agent_run() -> None:
    harness = FakeHarness([_output(reason_code="Fabricated"), _output()])

    with pytest.raises(InvalidAnalysisOutputError, match="Fabricated"):
        await _analyst(harness).analyze(_input())

    assert len(harness.prompts) == 1


@pytest.mark.asyncio
async def test_invalid_output_is_permanent_invalid_analysis_output() -> None:
    harness = FakeHarness(
        [
            HarnessValidationError("first invalid response"),
            HarnessValidationError("second invalid response"),
        ]
    )

    with pytest.raises(InvalidAnalysisOutputError) as error:
        await _analyst(harness).analyze(_input())

    assert error.value.error_code == "invalid_analysis_output"
    assert len(harness.prompts) == 1


@pytest.mark.asyncio
async def test_run_limit_is_translated_to_permanent_error() -> None:
    harness = FakeHarness([HarnessRunLimitError("model call limit reached")])

    with pytest.raises(RunLimitExceededError) as error:
        await _analyst(harness).analyze(_input())

    assert error.value.error_code == "run_limit_exceeded"


@pytest.mark.asyncio
async def test_exhausted_provider_retry_is_translated_to_transient_error() -> None:
    harness = FakeHarness([TransientLLMError("provider unavailable")])

    with pytest.raises(AnalysisUnavailableError):
        await _analyst(harness).analyze(_input())


@pytest.mark.asyncio
async def test_exhausted_database_retry_reaches_investigation_retry_handling() -> None:
    harness = FakeHarness([QueryUnavailableError("CTC capacity exhausted")])

    with pytest.raises(AnalysisUnavailableError, match="CTC capacity exhausted"):
        await _analyst(harness).analyze(_input())

    assert len(harness.prompts) == 1


@pytest.mark.asyncio
async def test_non_retryable_provider_error_does_not_escape_the_port_contract() -> None:
    harness = FakeHarness([PermanentLLMError("invalid provider request")])

    with pytest.raises(AnalysisFailedError) as error:
        await _analyst(harness).analyze(_input())

    assert isinstance(error.value.__cause__, PermanentLLMError)
