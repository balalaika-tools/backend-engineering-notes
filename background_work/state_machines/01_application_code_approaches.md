# Transition Code Should Decide, Not Execute

> **Who this is for**: Python engineers choosing how application code will express a workflow transition graph.

Before reading this, separate transition modeling from persistence and execution in **[State-Machine Design](../03_state_machine_design.md)**.

---

## 1. Arbitrary target states bypass the workflow

An approval endpoint accepts `{"to_state": "COMPLETED"}`. The database update succeeds, but no generation happened and no final artifact exists. The problem is not missing validation on one endpoint; the caller was allowed to choose the result of a domain decision.

Accept a **named event** and its evidence. Application code loads the persisted state, finds the one legal transition, evaluates guards, and derives both the target state and durable commands.

```text
input:  event=approve, reviewer_id=editor-7, reason="ready"
state:  WAITING_FOR_HUMAN_REVIEW
output: state=GENERATION_QUEUED
        command=ScheduleGeneration(operation_key=run-42:generate:v3)
```

No transition function calls a provider or publishes directly. It returns a decision that the persistence layer commits atomically.

> **The near-miss**: putting every allowed target in an enum prevents typos, not illegal transitions. `COMPLETED` can be a valid enum member and still be illegal from `WAITING_FOR_HUMAN_REVIEW`.

---

## 2. `match` is a good baseline for a genuinely small graph

This complete example handles the editorial `approve` event and produces visible output:

```python
from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    NEW = "NEW"
    WAITING_FOR_HUMAN_REVIEW = "WAITING_FOR_HUMAN_REVIEW"
    GENERATION_QUEUED = "GENERATION_QUEUED"
    CANCELLED = "CANCELLED"


class TransitionRejected(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    run_id: str
    workflow_definition_version: int
    reviewer_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Decision:
    to_state: State
    command: dict[str, str] | None


def decide_with_match(state: State, event: str, context: Context) -> Decision:
    match state, event:
        case State.WAITING_FOR_HUMAN_REVIEW, "approve":
            if not context.reviewer_id or not context.reason:
                raise TransitionRejected("approve requires reviewer_id and reason")
            return Decision(
                to_state=State.GENERATION_QUEUED,
                command={
                    "type": "schedule_generation",
                    "operation_key": (
                        f"{context.run_id}:generate:"
                        f"v{context.workflow_definition_version}"
                    ),
                },
            )
        case _, "cancel" if state not in {State.CANCELLED}:
            return Decision(to_state=State.CANCELLED, command=None)
        case _:
            raise TransitionRejected(f"{event!r} is illegal from {state}")


if __name__ == "__main__":
    decision = decide_with_match(
        State.WAITING_FOR_HUMAN_REVIEW,
        "approve",
        Context("run-42", 3, reviewer_id="editor-7", reason="ready"),
    )
    print(decision.to_state)
    print(decision.command)
```

The output contains `GENERATION_QUEUED` and the stable operation key `run-42:generate:v3`. An illegal event raises before any durable write.

Use this form while the complete graph fits in one function and every transition remains easy to scan. Once several handlers each start their own `match`, move the rules behind one registry rather than copying branches.

---

## 3. A registry is the default service-workflow model

The hardened version makes `(from_state, event)` the lookup key and keeps guards in normal functions. The registry is declarative; command creation remains executable code.

```python
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    NEW = "NEW"
    WAITING_FOR_HUMAN_REVIEW = "WAITING_FOR_HUMAN_REVIEW"
    GENERATION_QUEUED = "GENERATION_QUEUED"
    CANCELLED = "CANCELLED"


class TransitionRejected(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    run_id: str
    definition_version: int
    actor_id: str
    reason: str


@dataclass(frozen=True)
class Decision:
    to_state: State
    command: dict[str, str] | None


Transition = Callable[[Context], Decision]


def approve(context: Context) -> Decision:
    if not context.actor_id or not context.reason.strip():
        raise TransitionRejected("approve requires reviewer identity and reason")
    return Decision(
        State.GENERATION_QUEUED,
        {
            "type": "schedule_generation",
            "operation_key": f"{context.run_id}:generate:v{context.definition_version}",
        },
    )


def cancel(context: Context) -> Decision:
    if not context.actor_id or not context.reason.strip():
        raise TransitionRejected("cancel requires actor identity and reason")
    return Decision(State.CANCELLED, None)


TRANSITIONS: dict[tuple[State, str], Transition] = {
    (State.WAITING_FOR_HUMAN_REVIEW, "approve"): approve,
    (State.NEW, "cancel"): cancel,
    (State.WAITING_FOR_HUMAN_REVIEW, "cancel"): cancel,
    (State.GENERATION_QUEUED, "cancel"): cancel,
}


def decide(state: State, event: str, context: Context) -> Decision:
    transition = TRANSITIONS.get((state, event))
    if transition is None:
        raise TransitionRejected(f"{event!r} is illegal from {state}")
    return transition(context)


def test_approve_derives_target_and_command() -> None:
    decision = decide(
        State.WAITING_FOR_HUMAN_REVIEW,
        "approve",
        Context("run-42", 3, "editor-7", "ready"),
    )
    assert decision.to_state is State.GENERATION_QUEUED
    assert decision.command == {
        "type": "schedule_generation",
        "operation_key": "run-42:generate:v3",
    }


def test_approve_from_new_is_rejected() -> None:
    try:
        decide(State.NEW, "approve", Context("run-42", 3, "editor-7", "ready"))
    except TransitionRejected as exc:
        assert "illegal from NEW" in str(exc)
    else:
        raise AssertionError("illegal transition was accepted")


if __name__ == "__main__":
    test_approve_derives_target_and_command()
    test_approve_from_new_is_rejected()
    print("transition tests passed")
```

**How you know it is working**: enumerating `TRANSITIONS` produces the complete flat graph, and the illegal-event test fails before the repository is called. A silent failure shows up when an endpoint or worker updates `state` without calling `decide`; enforce the command service as the only write path in code review and database permissions where practical.

> **Key insight**: A transition is a pure decision plus durable commands. Keeping execution out of the decision makes every modeling style testable and lets one persistence mechanism enforce them all.

---

## 4. State objects help only when behavior truly varies by state

The **State pattern** moves event behavior into an object for the current state:

```python
from dataclasses import dataclass


class TransitionRejected(ValueError):
    pass


@dataclass(frozen=True)
class Context:
    run_id: str
    actor_id: str


class WorkflowState:
    name = "UNKNOWN"

    def approve(self, context: Context) -> tuple[str, dict[str, str]]:
        raise TransitionRejected(f"approve is illegal from {self.name}")


class WaitingForHumanReview(WorkflowState):
    name = "WAITING_FOR_HUMAN_REVIEW"

    def approve(self, context: Context) -> tuple[str, dict[str, str]]:
        if not context.actor_id:
            raise TransitionRejected("approve requires reviewer identity")
        return "GENERATION_QUEUED", {
            "type": "schedule_generation",
            "operation_key": f"{context.run_id}:generate",
        }


if __name__ == "__main__":
    target, command = WaitingForHumanReview().approve(Context("run-42", "editor-7"))
    assert target == "GENERATION_QUEUED"
    print(target, command)
```

This is useful when each state owns substantial state-specific behavior. It becomes expensive when most classes only reject most methods; the graph is then harder to inspect than the registry it replaced. Persistence still stores a stable state identifier, not a serialized Python object.

---

## 5. Hierarchy removes duplicated parent behavior

Suppose `RESEARCH_QUEUED`, `RESEARCH_RUNNING`, and `WAITING_FOR_HUMAN_REVIEW` all belong to `ACTIVE.RESEARCH`, while every `ACTIVE.*` state accepts `cancel`. A flat registry repeats `cancel` for every leaf. A hierarchical statechart searches the leaf first, then its parent:

```python
PARENT = {
    "ACTIVE.RESEARCH.WAITING_FOR_HUMAN_REVIEW": "ACTIVE.RESEARCH",
    "ACTIVE.RESEARCH": "ACTIVE",
}

TRANSITIONS = {
    ("ACTIVE.RESEARCH.WAITING_FOR_HUMAN_REVIEW", "approve"): "ACTIVE.GENERATION.QUEUED",
    ("ACTIVE", "cancel"): "CANCELLED",
}


def hierarchical_target(state: str, event: str) -> str:
    cursor: str | None = state
    while cursor is not None:
        target = TRANSITIONS.get((cursor, event))
        if target is not None:
            return target
        cursor = PARENT.get(cursor)
    raise ValueError(f"{event!r} is illegal from {state}")


if __name__ == "__main__":
    leaf = "ACTIVE.RESEARCH.WAITING_FOR_HUMAN_REVIEW"
    assert hierarchical_target(leaf, "approve") == "ACTIVE.GENERATION.QUEUED"
    assert hierarchical_target(leaf, "cancel") == "CANCELLED"
    print("leaf and inherited transitions passed")
```

A production statechart library also defines entry/exit actions, history states, parallel regions, and event ordering. Adopt those semantics deliberately and test them; do not grow this compact lookup into a home-made statechart runtime.

⚠️ An entry action that performs external I/O may run again during recovery. Return a durable command and execute it through an idempotent worker instead.

Do not use state objects for a data-only graph, or hierarchy for a flat lifecycle. Once guards, transitions, and commands no longer fit on one inspectable page, prefer a mature statechart library or a workflow engine over adding implicit conventions.

---

**Next**: [Database-Backed State Machines](02_database_backed_state_machine.md)
