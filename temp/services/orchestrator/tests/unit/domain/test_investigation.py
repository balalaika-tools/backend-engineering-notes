"""Investigation status mapping rules."""

import pytest
from orchestrator.domain.investigation import (
    ExternalInvestigationStatus,
    InvestigationStatus,
    external_status,
)


@pytest.mark.parametrize(
    ("internal", "external"),
    [
        (InvestigationStatus.QUEUED, ExternalInvestigationStatus.IN_PROGRESS),
        (InvestigationStatus.PROCESSING, ExternalInvestigationStatus.IN_PROGRESS),
        (InvestigationStatus.COMPLETED, ExternalInvestigationStatus.COMPLETED),
        (InvestigationStatus.FAILED, ExternalInvestigationStatus.FAILED),
    ],
)
def test_external_status_mapping(
    internal: InvestigationStatus,
    external: ExternalInvestigationStatus,
) -> None:
    assert external_status(internal) is external
