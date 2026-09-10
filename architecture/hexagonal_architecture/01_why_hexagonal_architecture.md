# Why Hexagonal Architecture Exists

> **Who this is for**: Python backend engineers who can build a route or worker but want a clearer rule for separating business behavior from frameworks and providers.

A ticket-classification endpoint starts small: load a row, call a model, update the row, publish an
event, and return JSON. Then a queue worker needs the same classification. Some tenants need a
rule-based classifier. Tests become a patchwork of SQLAlchemy, AWS, and model SDK mocks.

**Hexagonal Architecture**, also called **Ports and Adapters**, responds by giving the business
action its own boundary and making external technologies plug into contracts that action owns.

---

## 1. A reasonable route accumulates unrelated reasons to change

This explanatory excerpt is deliberately coupled so the pressure is visible:

```python
@router.post("/tickets/{ticket_id}/classify")
async def classify_ticket(ticket_id: UUID, session: AsyncSession):
    ticket = await session.get(TicketModel, ticket_id)
    response = await openai_client.responses.create(
        model="classification-model",
        input=ticket.body,
    )
    category = parse_category(response)
    ticket.category = category
    await session.commit()
    await sqs_client.send_message(MessageBody=ticket.model_dump_json())
    return {"ticket_id": str(ticket.id), "category": category}
```

For a prototype, this may be the fastest correct move. The architectural problem appears when the
function gains several independent owners:

```text
HTTP route
├── HTTP validation and response mapping
├── database access and transaction behavior
├── ticket eligibility and classification policy
├── model-provider invocation and output parsing
└── SQS publication and delivery errors
```

A provider upgrade, business-rule change, schema migration, and HTTP contract change all edit the
same function for different reasons. Reusing it from a worker either duplicates the logic or forces
the worker to pretend it is an HTTP client.

> **The near-miss**: the problem looks like function length, so extracting private helpers seems
> sufficient. Helpers shorten the route, but the route still chooses every concrete technology and
> remains the only owner of the workflow.

---

## 2. Change pressure reveals the boundaries

Imagine the next six requirements arrive over two releases:

1. A queue consumer must classify the same ticket after bulk imports.
2. Unit tests must run without PostgreSQL, AWS credentials, or paid model calls.
3. Low-risk tenants use a deterministic classifier instead of an LLM.
4. Invalid model output goes to human review; provider timeouts may be retried.
5. Shutdown must stop message intake before closing shared clients.
6. The HTTP response changes without changing worker output.

Each pressure suggests a separation:

| Pressure                                                   | Boundary that answers it                          |
| ---------------------------------------------------------- | ------------------------------------------------- |
| API and worker need the same operation                     | A public action in`application/`                |
| Classification rules must ignore transport                 | Business policy in`application/` or `domain/` |
| The classifier is remote and nondeterministic              | A caller-owned contract in`ports/`              |
| Model SDK details must not leak inward                     | A concrete implementation in`genai/`            |
| SQLAlchemy queries need one owner                          | Persistence code in`db/`                        |
| Receipt handles and acknowledgements are transport details | A broker consumer in`adapters/`                 |
| Concrete construction and disposal need one owner          | A composition root in`bootstrap/`               |

The names are secondary. The reasoning is the architecture: code that changes for business reasons
should not depend on code that changes because a provider, protocol, or process changes.

> **Core:** start by naming the business action and its external effects. Do not begin by creating
> every folder in a template.

---

## 3. The resulting action describes business execution

After separating responsibilities, both inbound paths call the same understandable operation:

```text
HTTP route ──────┐
                 ├──► ClassifyTicket ──► TicketRepository
Queue consumer ──┘          │
                            ├───────────► TicketClassifier
                            └───────────► ClassificationPublisher
```

The **application action** is the callable business operation. A **port** is a typed capability it
needs from outside its deterministic core. An **adapter** translates a specific technology into or
out of those application-facing shapes.

```python
class ClassifyTicket:
    def __init__(self, tickets: TicketRepository, classifier: TicketClassifier):
        self._tickets = tickets
        self._classifier = classifier

    async def execute(self, ticket_id: UUID) -> Classification:
        ticket = await self._tickets.get(ticket_id)
        candidate = await self._classifier.classify(ticket.body)
        return ticket.accept(candidate)
```

The excerpt says what the action requires but not whether the repository is SQLAlchemy-backed or
the classifier uses a particular provider or rules. The [vertical-slice tutorial](02_build_one_vertical_slice.md)
turns this shape into a complete runnable example.

---

## 4. The gains are concrete change options

“Maintainability” is too vague to justify architectural cost. The useful gains are specific:

- FastAPI, a queue consumer, a scheduled command, or a CLI can call the same action.
- Application tests replace external capabilities with typed fakes instead of patching SDK internals.
- Provider exceptions and wire formats stop at the adapter that understands them.
- Framework upgrades affect transport code rather than business decisions.
- Startup, shared-resource lifetime, and shutdown have one discoverable owner.
- AI-generated changes have clearer placement constraints and smaller review surfaces.
- A reviewer can ask “who owns this decision?” and find one package responsible for it.

Consider a tenant policy change: confidence below `0.75` now requires human review. In the coupled
route, testing the rule may require database and model scaffolding. In the separated design, the
application test supplies a candidate directly through a fake classifier and observes the result.

```python
classifier = StubClassifier(category="billing", confidence=0.61)
result = await ClassifyTicket(tickets, classifier).execute(ticket_id)
assert result.requires_human_review is True
```

That short test is not merely convenient. It proves the policy does not depend on a provider SDK.

> **Key insight**: a boundary pays for itself when it turns a likely external change into a local
> adapter change while leaving the business action and its tests intact.

---

## 5. The pattern does not solve distributed-systems problems

Hexagonal structure changes dependency ownership; it does not make multiple external effects
atomic. Saving a classification and publishing an event can still split if the process crashes
between them. Retries can still duplicate effects. Concurrent workers can still race.

Those problems need mechanisms such as transactions, an outbox, idempotency keys, leases, or
reconciliation. See the [Background Work reliability path](../../background_work/reliability/README.md)
when the action crosses durable asynchronous boundaries.

It also does not automatically provide a rich domain model, good API contracts, sensible retry
policy, integration confidence, useful observability, or fewer total lines of code. The gain is
controlled coupling, not the disappearance of complexity.

---

## 6. Small services may not need the full shape

Do not apply the complete directory tree to a health probe, a short-lived internal script, or a
small CRUD service whose behavior is mostly validated persistence. Direct code may be easier to
understand until independent change pressure appears.

A boundary becomes more valuable when:

- two inbound mechanisms need the same business action;
- an external dependency is costly, remote, nondeterministic, or failure-prone;
- business rules deserve fast tests without infrastructure;
- concrete resources have nontrivial startup and shutdown;
- providers or implementations genuinely vary.

⚠️ The first failure of an over-architected service is navigation: a one-line rule requires opening
an interface, implementation, factory, mapper, and empty domain object. If engineers cannot state
which real change each boundary isolates, collapse it.

**Success signal:** a new queue entry point can invoke the existing action without importing the
API package or copying its business branches. If the worker must construct an HTTP request or patch
route dependencies, the action boundary has not actually been extracted.

> **Production:** use the later [migration guide](11_migrate_and_review_an_existing_service.md) to
> introduce boundaries one coherent action at a time; a repository-wide folder rewrite creates
> churn without proving better dependency direction.

---

**Next**: [Part 2 — Build One Vertical Slice](02_build_one_vertical_slice.md)
