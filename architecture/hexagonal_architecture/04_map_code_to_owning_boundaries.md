# Map Code to the Boundary That Owns Its Decisions

> **Who this is for**: Engineers deciding where files belong in a deployable Python API, worker, consumer, or hybrid service.

A folder name is useful only when it predicts what changes there and what it may import. Start with
the smallest folders the current service needs; use this chapter's full tree as a placement policy,
not as permission to scaffold empty packages.

---

## 1. One mixed module contains several owners

A module named `ticket_service.py` often validates HTTP input, runs policy, queries SQLAlchemy,
calls a model, and publishes a message. “Service” says none of those responsibilities aloud, so each
new function makes ownership harder to infer.

For the running example, classify each operation before moving it:

| Operation | Why it changes | Owner |
|-----------|----------------|-------|
| Parse `POST /tickets/{id}/classify` | HTTP contract changes | `api/` |
| Decide whether a closed ticket is eligible | Business policy changes | `application/` or `domain/` |
| Describe the required classification capability | Caller need changes | `ports/` |
| Build prompts and invoke a model | AI implementation changes | `genai/` |
| Execute ticket queries | Persistence changes | `db/` |
| Parse and acknowledge an SQS delivery | Broker behavior changes | `adapters/aws/` |
| Construct and dispose all of the above | Process lifecycle changes | `bootstrap/` |

> **Core:** place code by the decision it owns, not by the library it happens to import or the order
> in which it runs.

---

## 2. A representative hybrid AI service uses specialized outer boundaries

```text
src/ticket_triage/
├── __init__.py
├── main.py
├── bootstrap/
│   ├── app.py
│   ├── runtime.py
│   └── supervisor.py
├── config/
│   ├── settings.py
│   └── secrets.py
├── api/
│   ├── dependencies.py
│   ├── exception_handlers.py
│   ├── routers/
│   │   └── tickets.py
│   └── schemas/
│       └── tickets.py
├── application/
│   ├── classify_ticket.py
│   └── replay_ticket.py
├── domain/
│   ├── ticket.py
│   └── classification.py
├── ports/
│   ├── ticket_classifier.py
│   ├── ticket_repository.py
│   └── classification_publisher.py
├── db/
│   ├── models.py
│   ├── repositories.py
│   └── session.py
├── adapters/
│   └── aws/
│       ├── sqs_consumer.py
│       ├── sqs_publisher.py
│       └── sqs_serialization.py
├── genai/
│   └── ticket_classification/
│       ├── llm.py
│       ├── schemas.py
│       ├── prompts.py
│       └── classifier.py
└── observability/
    └── telemetry.py
```

Logically, `api/`, `db/`, and `genai/` are adapters. Physically, they receive specialized roots
because HTTP transport, persistence, and generative AI each develop recognizable ownership and
testing needs. Ordinary external integrations remain under `adapters/<provider-or-technology>/`.

The supplied orchestrator and worker use this general shape. Follow
[one investigation across their files](12_trace_an_investigation_across_services.md) to see how
the owners cooperate. Shared `libs/` packages are a separate packaging choice: a database library
remains an outer implementation dependency, as explained in
[Shared Libraries](13_share_libraries_without_service_layers.md).

Do not add root `messaging/`; broker consumers, publishers, delivery envelopes, acknowledgements,
and visibility mechanics are provider adapters. Do not place model code in general `adapters/`;
this repository standardizes every prompt, model, agent, AI schema, tool, and graph under `genai/`.

---

## 3. Eight questions place most ambiguous code

Ask these in order:

1. Does it deliver an understandable business action or outcome? Put it in **`application/`**.
2. Is it a reusable business noun, value object, invariant, or pure rule? Put it in **`domain/`**.
3. Does it describe an external or nondeterministic capability an action needs? Put it in **`ports/`**.
4. Does it expose HTTP request, response, middleware, or routing concerns? Put it in **`api/`**.
5. Does it execute queries or own sessions and repositories? Put it in **`db/`**.
6. Does it contain any LLM, prompt, agent, AI tool, graph, or model binding? Put it in **`genai/`**.
7. Does it translate another external technology? Put it in **`adapters/`**.
8. Does it construct or dispose the runtime graph? Put it in **`bootstrap/`**.

For example, a Pydantic request containing `callback_url` belongs to `api/schemas/` when it exists
only for HTTP validation. A `ClassificationCandidate` shared by the action and classifier port is
an application-facing contract, not an HTTP schema.

---

## 4. Application and domain are related but not synonyms

`application/` owns executable business actions and effect sequencing. It answers “what does this
service do?” Examples include `ClassifyTicket`, `ReplayTicket`, and `SubmitBatch`.

`domain/` owns reusable business meaning that remains useful across actions: `Ticket`,
`Classification`, eligibility rules, value objects, and invariant failures. It is optional. If an
action has two small private rules and no reused domain model, keeping them in the action is clearer
than inventing an anemic domain layer.

```python
# domain/ticket.py
@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    status: TicketStatus
    body: str

    def ensure_classifiable(self) -> None:
        if self.status is TicketStatus.CLOSED:
            raise ClosedTicket(self.ticket_id)
```

The action calls this rule while still owning retrieval, classification, persistence, and the
resulting handoff.

---

## 5. Errors, configuration, and helpers stay with their meaning

A global `utils/`, `common/`, `constants.py`, or `core/errors.py` hides ownership. Prefer:

```text
Business invariant             -> domain/ticket.py or domain/errors.py
Use-case failure               -> application/classify_ticket.py
Stable external failure        -> ports/ticket_classifier.py
Private provider failure       -> genai/ticket_classification/
AWS envelope constant          -> adapters/aws/
Deployment-varying timeout     -> config/settings.py
```

A value is configuration when an operator may change it by environment. A constant represents
stable code or domain semantics. Retry behavior stays near the boundary that can classify the
failure; correlation logic stays with the business rule that defines correlation.

> **Key insight**: a good package tree is an ownership map—given a change request, an engineer can
> predict the small set of files that should change and the dependencies they are allowed to know.

---

## 6. The tree is successful when omissions are intentional

**Success signal:** choose five current files and explain each location using one placement question.
Then inspect imports and confirm no inner package reaches into its concrete outer implementation.

⚠️ The first failure is cosmetic symmetry: a repository with empty `domain/`, one protocol for each
function, and one-class packages looks architectural while preserving no meaningful boundary.
Remove empty and pass-through layers.

Do not use the service tree for a non-deployable internal library. A library normally has a flat,
cohesive import package, a deliberate public API, and its own tests; it should not acquire
`bootstrap/`, `api/`, or deployment configuration merely to resemble services.

> **Production:** once several actions form one cohesive capability, promote only that slice—for
> example `application/tickets/classify.py` and `application/tickets/replay.py`. The
> [flat-first guide](10_grow_without_package_ceremony.md) owns the growth criteria.

---

**Next**: [Part 5 — Design Ports and Adapter Contracts](05_design_ports_and_adapter_contracts.md)
