"""Transactional persistence contracts for investigation commands and queries."""

import uuid
from types import TracebackType
from typing import Protocol, Self

from orchestrator.domain.investigation import (
    InvestigationInsertResult,
    InvestigationRecord,
    InvestigationReportRecord,
)
from orchestrator.ports.exception_candidates import FilteredSelection


class InvestigationStoreUnavailableError(RuntimeError):
    """Platform persistence may succeed when the request is retried."""


class RequestWriter(Protocol):
    async def add(
        self,
        *,
        client_id: str,
        exception_ids: list[str],
        selection: FilteredSelection | None = None,
    ) -> uuid.UUID: ...
    async def set_selected_ids(
        self, *, request_id: uuid.UUID, exception_ids: list[str]
    ) -> None: ...
    async def attach_investigation(
        self, *, request_id: uuid.UUID, investigation_id: uuid.UUID, position: int
    ) -> None: ...


class InvestigationWriter(Protocol):
    async def completed_exception_ids(self, exception_ids: list[str]) -> set[str]: ...
    async def insert_or_attach_filtered(
        self, *, request_id: uuid.UUID, client_id: str, exception_id: str
    ) -> InvestigationInsertResult | None: ...

    async def insert_or_attach(
        self,
        *,
        request_id: uuid.UUID,
        client_id: str,
        exception_id: str,
        investigation_id: uuid.UUID | None = None,
        event_id: uuid.UUID | None = None,
    ) -> InvestigationInsertResult: ...


class InvestigationReader(Protocol):
    async def by_ids(
        self, investigation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, InvestigationRecord]: ...
    async def most_recent_by_exception_ids(
        self, exception_ids: list[str]
    ) -> dict[str, InvestigationRecord]: ...
    async def by_api_request_id(self, request_id: uuid.UUID) -> list[InvestigationRecord]: ...
    async def reports_by_ids(
        self, investigation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, InvestigationReportRecord]: ...
    async def most_recent_reports_by_exception_ids(
        self, exception_ids: list[str]
    ) -> dict[str, InvestigationReportRecord]: ...


class RequestReader(Protocol):
    async def exists_for_client(self, *, request_id: uuid.UUID, client_id: str) -> bool: ...


class OutboxWriter(Protocol):
    async def add_investigation_requested(
        self,
        *,
        investigation: InvestigationRecord,
        traceparent: str,
        subject: str,
    ) -> uuid.UUID: ...


class InvestigationUnitOfWork(Protocol):
    @property
    def requests(self) -> RequestWriter: ...
    @property
    def investigations(self) -> InvestigationWriter: ...
    @property
    def outbox(self) -> OutboxWriter: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class StatusQueryUnitOfWork(Protocol):
    @property
    def investigations(self) -> InvestigationReader: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class RequestStatusQueryUnitOfWork(Protocol):
    @property
    def requests(self) -> RequestReader: ...
    @property
    def investigations(self) -> InvestigationReader: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class ReportQueryUnitOfWork(Protocol):
    @property
    def investigations(self) -> InvestigationReader: ...
    async def __aenter__(self) -> Self: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
