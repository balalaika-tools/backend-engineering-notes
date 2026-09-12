"""NATS-private investigation delivery contracts."""

from typing import Protocol

from worker.domain.investigation import InvestigationRequested


class InvestigationMessage(Protocol):
    @property
    def event(self) -> InvestigationRequested: ...

    @property
    def trace_carrier(self) -> dict[str, str]: ...

    async def ack(self) -> None: ...

    async def nak(self, *, delay_seconds: float | None = None) -> None: ...

    async def term(self) -> None: ...

    async def in_progress(self) -> None: ...


class InvestigationConsumer(Protocol):
    async def ensure(self) -> None: ...

    async def fetch_one(self, *, timeout_seconds: float) -> InvestigationMessage | None: ...
