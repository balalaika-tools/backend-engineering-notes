"""Persistence operations for API request audit records."""

import uuid

from orchestrator.ports.exception_candidates import FilteredSelection
from platform_db import ApiRequest, ApiRequestInvestigation
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession


class RequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        client_id: str,
        exception_ids: list[str],
        request_id: uuid.UUID | None = None,
        selection: FilteredSelection | None = None,
    ) -> uuid.UUID:
        request = ApiRequest(
            id=request_id or uuid.uuid4(),
            client_id=client_id,
            exception_ids=exception_ids,
        )
        if selection is not None:
            request.request_kind = "filtered"
            request.selection_created_at_gte = selection.created_at_gte
            request.selection_max_exceptions = selection.max_exceptions
            request.selection_schema = selection.schema
            request.selection_exception_name = selection.exception_name
        self._session.add(request)
        await self._session.flush()
        return request.id

    async def attach_investigation(
        self,
        *,
        request_id: uuid.UUID,
        investigation_id: uuid.UUID,
        position: int,
    ) -> None:
        self._session.add(
            ApiRequestInvestigation(
                request_id=request_id,
                investigation_id=investigation_id,
                position=position,
            )
        )
        await self._session.flush()

    async def exists_for_client(self, *, request_id: uuid.UUID, client_id: str) -> bool:
        statement = select(ApiRequest.id).where(
            ApiRequest.id == request_id,
            ApiRequest.client_id == client_id,
        )
        return (await self._session.exec(statement)).first() is not None

    async def set_selected_ids(self, *, request_id: uuid.UUID, exception_ids: list[str]) -> None:
        request = await self._session.get(ApiRequest, request_id)
        if request is None:
            raise LookupError("Request disappeared during admission")
        request.exception_ids = exception_ids
        await self._session.flush()
