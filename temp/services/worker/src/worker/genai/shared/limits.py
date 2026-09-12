"""Per-invocation tool budget shared with nested agents."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


class RunToolLimitExceeded(RuntimeError):
    """The current investigation consumed its complete tool budget."""


@dataclass(slots=True)
class ToolCallBudget:
    limit: int
    used: int = 0

    def consume(self) -> None:
        if self.used >= self.limit:
            raise RunToolLimitExceeded(f"Tool call limit of {self.limit} was exceeded")
        self.used += 1


_CURRENT_BUDGET: ContextVar[ToolCallBudget | None] = ContextVar("tool_call_budget", default=None)


@contextmanager
def bind_tool_budget(budget: ToolCallBudget) -> Iterator[None]:
    token: Token[ToolCallBudget | None] = _CURRENT_BUDGET.set(budget)
    try:
        yield
    finally:
        _CURRENT_BUDGET.reset(token)


def consume_tool_call() -> None:
    budget = _CURRENT_BUDGET.get()
    if budget is not None:
        budget.consume()
