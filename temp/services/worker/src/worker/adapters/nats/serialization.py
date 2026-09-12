"""Wire contract for investigation request events."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from worker.domain.investigation import InvestigationRequested


class InvestigationRequestedPayload(BaseModel):
    event_id: uuid.UUID
    event_type: Literal["investigation.requested"]
    occurred_at: datetime
    investigation_id: uuid.UUID
    request_id: uuid.UUID
    client_id: str
    exception_id: str
    traceparent: str | None = None

    def to_domain(self) -> InvestigationRequested:
        return InvestigationRequested(
            event_id=self.event_id,
            event_type=self.event_type,
            occurred_at=self.occurred_at,
            investigation_id=self.investigation_id,
            request_id=self.request_id,
            client_id=self.client_id,
            exception_id=self.exception_id,
            traceparent=self.traceparent,
        )
