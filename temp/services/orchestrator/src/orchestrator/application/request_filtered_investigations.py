"""Select a bounded CTC backlog and admit durable work through platform persistence."""

from collections.abc import Callable
from datetime import UTC, datetime

from orchestrator.application.request_investigations import (
    InvestigationBatch,
    RequestedInvestigation,
    request_one,
)
from orchestrator.observability.filtered_requests import SelectionTelemetry, selection_operation
from orchestrator.ports.exception_candidates import (
    CandidateCursor,
    CandidateSourceUnavailableError,
    ExceptionCandidateSource,
    FilteredSelection,
)
from orchestrator.ports.investigation_store import InvestigationUnitOfWork


class BulkLimitExceededError(ValueError):
    error_code = "bulk_limit_exceeded"


class InvalidFilteredSelectionError(ValueError):
    pass


class RequestFilteredInvestigations:
    def __init__(
        self,
        *,
        candidates: ExceptionCandidateSource,
        unit_of_work_factory: Callable[[], InvestigationUnitOfWork],
        schema: str,
        exception_name: str,
        max_exceptions: int,
        page_size: int,
        subject: str,
    ) -> None:
        self._candidates = candidates
        self._unit_of_work_factory = unit_of_work_factory
        self._schema = schema
        self._exception_name = exception_name
        self._maximum = max_exceptions
        self._page_size = page_size
        self._subject = subject

    async def execute(
        self,
        *,
        client_id: str,
        traceparent: str,
        created_at_gte: datetime | None = None,
        max_exceptions: int = 1,
    ) -> InvestigationBatch:
        selection = self._normalize(created_at_gte, max_exceptions)
        with selection_operation(selection) as stats:
            return await self._accept(selection, client_id, traceparent, stats)

    async def _accept(
        self,
        selection: FilteredSelection,
        client_id: str,
        traceparent: str,
        stats: SelectionTelemetry,
    ) -> InvestigationBatch:
        selected = await self._select(selection, stats)
        stats.stage = "platform_admission"
        async with self._unit_of_work_factory() as uow:
            request_id = await uow.requests.add(
                client_id=client_id, exception_ids=[], selection=selection
            )
            results: list[RequestedInvestigation] = []
            for exception_id in selected:
                result = await request_one(
                    unit_of_work=uow,
                    request_id=request_id,
                    client_id=client_id,
                    exception_id=exception_id,
                    traceparent=traceparent,
                    subject=self._subject,
                    filtered=True,
                )
                if result is None:
                    stats.history_exclusions += 1
                    continue
                await uow.requests.attach_investigation(
                    request_id=request_id,
                    investigation_id=result.investigation_id,
                    position=len(results),
                )
                results.append(result)
            await uow.requests.set_selected_ids(
                request_id=request_id, exception_ids=[row.exception_id for row in results]
            )
            await uow.commit()
        stats.request_id = str(request_id)
        stats.selected_count = len(results)
        stats.created_count = sum(row.created for row in results)
        stats.attached_count = len(results) - stats.created_count
        return InvestigationBatch(request_id=request_id, investigations=tuple(results))

    def _normalize(self, created_at_gte: datetime | None, maximum: int) -> FilteredSelection:
        if type(maximum) is not int or maximum <= 0:
            raise InvalidFilteredSelectionError("max_exceptions must be a positive integer")
        if maximum > self._maximum:
            raise BulkLimitExceededError(
                f"Bulk request exceeds the configured limit of {self._maximum}"
            )
        if created_at_gte is not None:
            if created_at_gte.tzinfo is None or created_at_gte.utcoffset() is None:
                raise InvalidFilteredSelectionError("created_at_gte must include a timezone")
            created_at_gte = created_at_gte.astimezone(UTC)
        return FilteredSelection(created_at_gte, maximum, self._schema, self._exception_name)

    async def _select(self, selection: FilteredSelection, stats: SelectionTelemetry) -> list[str]:
        selected: dict[str, None] = {}
        cursor = None
        while len(selected) < selection.max_exceptions:
            stats.stage = "ctc_selection"
            page = await self._candidates.read_page(
                selection=selection, limit=self._page_size, after=cursor
            )
            stats.pages_read += 1
            stats.scanned_rows += len(page)
            if not page:
                break
            self._validate_page(page, cursor)
            stats.stage = "platform_history"
            async with self._unit_of_work_factory() as uow:
                completed = await uow.investigations.completed_exception_ids(
                    list(dict.fromkeys(row.external_id for row in page))
                )
            stats.history_exclusions += sum(row.external_id in completed for row in page)
            for row in page:
                if row.external_id not in completed:
                    selected[row.external_id] = None
                if len(selected) == selection.max_exceptions:
                    break
            cursor = page[-1]
            if len(page) < self._page_size:
                break
        return list(selected)

    def _validate_page(
        self, page: tuple[CandidateCursor, ...], after: CandidateCursor | None
    ) -> None:
        if len(page) > self._page_size:
            raise CandidateSourceUnavailableError("Candidate page exceeded its bound")
        previous = (after.created_at, after.external_id, after.pk) if after else None
        for row in page:
            key = (row.created_at, row.external_id, row.pk)
            if previous is not None and key <= previous:
                raise CandidateSourceUnavailableError("Candidate cursor did not advance")
            previous = key
