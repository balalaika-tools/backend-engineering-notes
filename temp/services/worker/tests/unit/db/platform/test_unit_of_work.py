"""Regression tests for transaction cleanup when rollback itself fails."""

from typing import Any, cast

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession
from worker.db.platform.reconciliation_uow import SqlAlchemyReconciliationUnitOfWork
from worker.db.platform.unit_of_work import SqlAlchemyInvestigationUnitOfWork
from worker.ports.investigation.investigation_store import InvestigationStoreUnavailableError
from worker.ports.reconciliation_store import ReconciliationStoreUnavailableError


class FailingRollbackSession:
    def __init__(self) -> None:
        self.closed = False

    async def rollback(self) -> None:
        raise SQLAlchemyError("rollback failed")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("unit_of_work_type", "expected_error"),
    [
        (SqlAlchemyInvestigationUnitOfWork, InvestigationStoreUnavailableError),
        (SqlAlchemyReconciliationUnitOfWork, ReconciliationStoreUnavailableError),
    ],
)
async def test_session_is_closed_when_rollback_fails(
    unit_of_work_type: type[Any],
    expected_error: type[Exception],
) -> None:
    session = FailingRollbackSession()
    factory = cast(async_sessionmaker[AsyncSession], lambda: session)
    unit_of_work = unit_of_work_type(factory)

    with pytest.raises(expected_error, match="rollback failed"):
        await unit_of_work.__aexit__(SQLAlchemyError, SQLAlchemyError("original"), None)

    assert session.closed is True
