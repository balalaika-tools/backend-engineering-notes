# Design Ports Around Caller Needs and Failure Decisions

> **Who this is for**: Engineers deciding whether an external dependency deserves a port and what that contract should contain.

A port has a cost: another name, type, implementation, and test seam. Pay that cost when it isolates
behavior the application must reason about—not because every dependency “should have an interface.”

---

## 1. A port earns its cost when the boundary changes application behavior

A classifier is remote, nondeterministic, costly, and capable of transient or invalid-output
failures. The application may retry, defer, or request human review based on those outcomes. That is
a valuable port.

Use this practical admission test. Introduce a port when at least two are true:

1. The dependency is remote, nondeterministic, expensive, or externally controlled.
2. Its failures materially change business behavior and need deterministic tests.
3. Multiple implementations or small local fakes have concrete value.

A clock used to decide expiration, object store, message publisher, remote HTTP service, LLM, or
browser portal often qualifies. Parsing, formatting, validation, and in-memory calculation usually
do not.

> **Core:** a port represents a capability requested by the application, not every collaborator the
> application happens to call.

---

## 2. Name the capability without assuming its implementation

`ClassificationModel` assumes a model. `OpenAIClient` assumes a provider.
`TicketClassifier` states what the caller needs:

```python
@dataclass(frozen=True)
class ClassificationCandidate:
    category: str
    confidence: float


class TicketClassifier(Protocol):
    async def classify(self, body: str) -> ClassificationCandidate: ...
```

An LLM, rules engine, hybrid classifier, or remote API can implement this conversation. Avoid raw
`dict[str, Any]`, unvalidated JSON, SDK messages, and provider response objects when a stable typed
shape exists.

Keep the input narrow as well. Passing the entire FastAPI request or settings object makes the port
depend on the current edge instead of the caller's actual data need.

---

## 3. The port owns stable success and failure contracts

If application code catches an SDK exception, the provider already owns part of the use case.
Define failures in the port according to decisions the caller can make:

```python
class TicketClassifierError(RuntimeError):
    pass


class ClassificationUnavailable(TicketClassifierError):
    """A transient failure may be retried or deferred."""


class InvalidClassification(TicketClassifierError):
    """The provider answered, but no trusted candidate can be produced."""
```

The concrete adapter translates implementation failures:

```python
async def classify(self, body: str) -> ClassificationCandidate:
    try:
        output = await self._model.ainvoke(self._prompt(body))
    except ProviderTimeout as exc:
        raise ClassificationUnavailable("classifier timed out") from exc

    if output.confidence < 0.0 or output.confidence > 1.0:
        raise InvalidClassification("confidence is outside [0, 1]")

    return ClassificationCandidate(
        category=output.category,
        confidence=output.confidence,
    )
```

The port's error message contains no prompt, token, API key, or raw provider body. Exception chaining
preserves diagnostics without making the inner layer import the provider type.

---

## 4. Give the caller only distinctions it can act on

An exhaustive copy of provider errors is not a stable application contract. Suppose the action has
three meaningful outcomes:

| Port result | Application decision |
|-------------|----------------------|
| Candidate with adequate confidence | Accept and persist |
| `ClassificationUnavailable` | Defer according to action policy |
| `InvalidClassification` | Route to human review |

If rate limits and provider timeouts produce the same action decision, they may share one stable
failure even though the adapter records different telemetry. Split errors only when callers need
different behavior.

Conversely, do not return `None` for every failure. It erases whether the ticket was absent, the
classifier was unavailable, or output was rejected.

> **Key insight**: a port is complete only when its success types and failure types let the caller
> make every required decision without importing implementation details.

---

## 5. A repository contract preserves the same dependency rule

A repository that returns domain types shields its caller from database rows, but importing its
SQLAlchemy implementation into `application/` still creates an outward source dependency. Moving
construction to bootstrap does not remove that import, even if it appears only in an annotation.

For this collection's strict dependency rule, declare the application-facing persistence contract
in `ports/` and inject its implementation from bootstrap. Python's structural typing lets a class
satisfy a `Protocol` by supplying the required methods; explicit inheritance is unnecessary. One
cohesive contract can describe an action's persistence needs without creating an interface for
every query or helper.

A small layered application can deliberately inject and import a concrete repository. That is a
simpler alternative with a weaker isolation guarantee, not an exception to the import rule taught
in [part 3](03_dependencies_point_toward_business_policy.md). Choose it when that coupling is
acceptable rather than calling the two designs equivalent.

---

## 6. A Unit of Work makes several repositories one transaction

Accepting an investigation can require a request row, an investigation row, a link between them,
and a pending event. If each repository commits independently, a failure writing the event can
leave an accepted investigation that nobody will execute.

A **Unit of Work** is the application's contract for a group of persistence operations that commit
or roll back together. In the supplied orchestrator, `InvestigationUnitOfWork` exposes `requests`,
`investigations`, `outbox`, async context-manager methods, and `commit()`. The **outbox** is a table
of events owed to the broker, written in the same transaction as the business state.

Trace a new investigation with illustrative IDs:

```text
one Unit of Work / one database session
  requests.add(...)                         → request R-1
  investigations.insert_or_attach(...)     → investigation I-1, created=True
  outbox.add_investigation_requested(...)  → pending event E-1
  requests.attach_investigation(...)       → R-1 linked to I-1 at position 0
  commit()                                 → all four writes become durable together
```

The action decides which operations must form one business transaction. The concrete Unit of Work
supplies the session and implements commit, rollback, cleanup, and database-error translation.
Its repositories issue queries through that same session; they do not commit independently.

An explanatory excerpt from the concrete constructor shows why a single commit reaches them all:

```python
def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
    self._session = session_factory()
    self.requests = RequestRepository(self._session)
    self.investigations = InvestigationRepository(self._session)
    self.outbox = OutboxRepository(self._session)
```

`flush()` may send a repository's pending changes to the database before `commit()`. It does not
make them independently durable. If the final attachment fails before commit, rollback removes
the new request, investigation, and event together. If I-1 already existed, rollback leaves that
previously committed investigation intact and discards only this transaction's changes.

### A factory gives every execution a fresh session

Bootstrap may construct one long-lived action, but concurrent requests cannot share one mutable
transaction. The injected `Callable[[], InvestigationUnitOfWork]` means “a function that takes no
arguments and returns a fresh Unit of Work.” This explanatory excerpt shows its construction:

```python
def investigation_uow() -> SqlAlchemyInvestigationUnitOfWork:
    return SqlAlchemyInvestigationUnitOfWork(session_factory)


action = RequestInvestigations(
    unit_of_work_factory=investigation_uow,
    max_batch_size=100,
    subject="investigations.requested",
)
```

Inside `execute()`, `async with self._unit_of_work_factory() as unit_of_work` calls that factory,
awaits `__aenter__()`, and later awaits `__aexit__()` on either success or failure. In this sample,
commit is explicit: exiting the block successfully does not automatically commit. The concrete
implementation rolls back on a body exception and closes the session on either path.

**Success signal:** each execution receives a different session, all repositories within one
execution share it, and a failure before commit leaves no partially accepted batch. A unit fake
can verify sequencing, but only a real database test proves rollback and concurrent uniqueness.
The sample's acceptance fake mutates dictionaries immediately and has a no-op commit; it does
not simulate transactional rollback.

### Keep the transaction shorter than the external work

An LLM call or object-store upload cannot be rolled back by the platform database. Holding its
session open during those calls consumes a connection without making the external effects atomic.
The worker therefore loads state in one Unit of Work, closes it, performs external work, and opens
another to persist the result. A **checkpoint**, a durable record of completed progress, makes
that separation resumable; [the case study](12_trace_an_investigation_across_services.md) follows it.

⚠️ A lost connection during commit can leave the outcome unknown: the database may have committed
before the client lost the response. A translated “unavailable” error does not prove rollback.
Recovery needs a durable lookup or an idempotency contract, as explained in
[atomic transitions and outbox](../../background_work/reliability/01_atomic_transitions_and_outbox.md).

Do not add a multi-repository Unit of Work merely for a single read. A narrow reader port may be
enough. Introduce the grouping when the action needs atomic changes or a shared transaction view.

---

## 7. Contract tests and observability reveal translation mistakes

**Success signal:** an application unit test can drive each meaningful success and failure outcome
using a tiny fake, while the action imports no SDK exceptions. Separately, adapter tests prove each
provider outcome maps to the intended port outcome.

⚠️ The first failure is “leaky typing”: a port looks abstract but exposes `BaseMessage`,
`AsyncSession`, SQS receipt handles, or raw response dictionaries. The leak often appears later
when a fake must reconstruct provider objects just to test one business branch.

Do not create a port for a pure function or a single stable collaborator whose direct injection is
already clear. The interface adds indirection without isolating volatility or failure.

> **Production:** adapters should record provider-specific failure details through safe telemetry
> before translating them, with secrets and sensitive payloads excluded. The application receives
> only the stable contract.

---

**Next**: [Part 6 — Compose the Runtime at the Edge](06_compose_the_runtime_at_the_edge.md)
