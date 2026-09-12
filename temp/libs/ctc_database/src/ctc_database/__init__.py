"""Read-only CTC database access; consumers own configuration and engine disposal."""

from ctc_database.acceptance import (
    CtcAcceptanceError,
    CtcAcceptanceReport,
    verify_candidate_contract,
)
from ctc_database.candidates import CandidateCursor, CtcCandidateReader
from ctc_database.engine import authenticated_database_url, build_engine
from ctc_database.exceptions import CtcExceptionRepository, ExceptionTableNames
from ctc_database.models import (
    ExceptionData,
    ExceptionNotFoundError,
    ExceptionSourceUnavailableError,
    LinkedRecord,
    NoLinkedRecordsError,
)

__all__ = [
    "CtcAcceptanceError",
    "CtcAcceptanceReport",
    "verify_candidate_contract",
    "CandidateCursor",
    "CtcCandidateReader",
    "CtcExceptionRepository",
    "ExceptionData",
    "ExceptionNotFoundError",
    "ExceptionSourceUnavailableError",
    "ExceptionTableNames",
    "LinkedRecord",
    "NoLinkedRecordsError",
    "authenticated_database_url",
    "build_engine",
]
