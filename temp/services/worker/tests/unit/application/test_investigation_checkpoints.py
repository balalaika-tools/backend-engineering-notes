"""Durable analysis and write-back checkpoint contracts."""

import pytest
from worker.application.investigation_checkpoints import (
    CODES_DONE,
    analysis_from_checkpoint,
    checkpoint_outcome,
    require_successful_outcome,
)
from worker.domain.investigation import PermanentInvestigationError
from worker.ports.investigation.ctc_write_back import WriteBackOutcome


def test_analysis_checkpoint_restores_the_domain_value() -> None:
    value = analysis_from_checkpoint(
        {
            "explanation": "explanation",
            "reasoning": "reasoning",
            "reason_code": "Reason",
            "resolution_code": "Resolution",
            "short_analysis": "short",
            "confidence": "medium",
            "_exception_version": 7,
        }
    )

    assert value.reason_code == "Reason"
    assert value.confidence == "medium"


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"explanation": "only one field"}, {"confidence": "certain"}],
)
def test_invalid_analysis_checkpoint_is_explicit(payload: dict[str, object] | None) -> None:
    with pytest.raises(PermanentInvestigationError) as error:
        analysis_from_checkpoint(payload)

    assert error.value.error_code == "invalid_analysis_checkpoint"


def test_unknown_write_back_outcome_is_explicit() -> None:
    with pytest.raises(PermanentInvestigationError) as error:
        checkpoint_outcome("new-provider-state")

    assert error.value.error_code == "invalid_write_back_checkpoint"


def test_terminal_failure_cannot_be_treated_as_completed() -> None:
    with pytest.raises(PermanentInvestigationError) as error:
        require_successful_outcome(WriteBackOutcome.FAILED, allowed=CODES_DONE)

    assert error.value.error_code == "write_back_rejected"
