"""Authoritative CTC exception and linked-record read boundary."""

from collections.abc import Mapping
from dataclasses import dataclass


class ExceptionNotFoundError(LookupError):
    """No CTC exception has the requested external identifier."""


class NoLinkedRecordsError(LookupError):
    """The exception exists but has no linked source records."""


class ExceptionSourceUnavailableError(RuntimeError):
    """The CTC read database could not satisfy the request."""

    error_code = "ctc_database_unavailable"


@dataclass(frozen=True, slots=True)
class LinkedRecord:
    pk: bytes
    agent_view: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ExceptionData:
    exception_pk: bytes
    exception_version: int
    exception_view: Mapping[str, object]
    records: tuple[LinkedRecord, ...]
