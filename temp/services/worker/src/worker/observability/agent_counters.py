"""Context-local counters for one bounded GenAI agent invocation."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class TokenTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    tool_calls: int = 0


_totals: ContextVar[TokenTotals | None] = ContextVar("agent_token_totals", default=None)


@contextmanager
def token_totals() -> Iterator[TokenTotals]:
    totals = TokenTotals()
    token = _totals.set(totals)
    try:
        yield totals
    finally:
        _totals.reset(token)


def current_token_totals() -> TokenTotals | None:
    return _totals.get()
