# Grow Boundaries Without Package Ceremony

> **Who this is for**: Engineers choosing when a module should become a package, when code should become shared, and when an abstraction should be removed.

Hexagonal Architecture can reduce coupling while increasing navigation cost. The answer is
**flat-first growth**: keep the required root ownership boundaries, but use the fewest cohesive
modules inside each one until demonstrated pressure justifies another level.

---

## 1. Start with the smallest honest service shape

A small API with one classification action might begin here:

```text
src/ticket_triage/
├── main.py
├── bootstrap.py
├── api/
│   └── routers/
│       └── tickets.py
├── application/
│   └── classify_ticket.py
├── ports/
│   └── ticket_classifier.py
└── genai/
    └── ticket_classification/
        ├── llm.py
        ├── schemas.py
        └── classifier.py
```

The specialized `genai/` boundary is explicit because AI implementation exists. There is no
`domain/`, `db/`, general `adapters/`, supervisor, middleware, prompt module, or diagnostics
package because the current service does not own those responsibilities.

> **Core:** create a boundary when its responsibility exists; create a nested package only when
> independent ownership, change, setup, or naming pressure exists inside that boundary.

---

## 2. Promote only the slice that outgrows a flat owner

Suppose `application/` grows from three actions to ticket and alert capabilities:

```text
application/
├── tickets/
│   ├── classify.py
│   ├── replay.py
│   └── close.py
└── alerts/
    └── send.py
```

Only the ticket slice earned a package. Do not create symmetrical one-file packages for alerts,
billing, users, and reporting.

Roughly three to five related modules is a useful review signal, not a quota. Promote a slice when
its modules change together for a distinct reason, share narrower test setup, create naming
collisions, or expose a coherent sub-API. A 350-line module should trigger a responsibility review,
not an automatic split.

---

## 3. Keep definitions with their owner until they gain weight

Do not create one module for every class, exception, schema, constant group, or private helper:

```text
ticket_classifier/
├── interface.py
├── errors.py
├── result.py
└── types.py
```

For one small contract, this is clearer:

```text
ports/
└── ticket_classifier.py   # Protocol, result type, stable errors
```

Extract `schemas/`, `errors.py`, or a same-named package only after multiple cohesive definitions
need independent ownership. GenAI task responsibilities are a deliberate exception when they
exist: keep model construction, provider schemas, prompts, agent assembly, and capability
invocation separate because they change and test differently.

---

## 4. Reject catch-alls by asking what change they predict

A module named `utils.py` predicts no owner. Place behavior by meaning:

| Behavior | Better owner |
|----------|--------------|
| Ticket subject normalization | `domain/ticket_normalization.py` |
| SQS envelope serialization | `adapters/aws/sqs_serialization.py` |
| Prompt rendering | `genai/ticket_classification/prompts.py` |
| Trace-context propagation | `observability/propagation.py` |
| Classification retry mapping | Classifier adapter or application action, depending on the decision |

The same rule rejects global `common/`, `shared/`, root `constants.py`, and centralized error
collections. “Used twice” is not enough; shared code needs shared meaning and a stable owner.

> **Key insight**: good modularity minimizes the number of reasons each module changes, not the
> number of lines in each file or the visual symmetry of the tree.

---

## 5. Shared libraries are capabilities rather than miniature services

Extract a library only after two current consumers share the same semantics, or when an independent
protocol/client/schema boundary has a concrete compatibility reason.

```text
libs/ticket_contracts/
├── pyproject.toml
├── src/
│   └── ticket_contracts/
│       ├── __init__.py
│       ├── events.py
│       └── serialization.py
└── tests/
```

A library normally has no process entry point, runtime composition root, deployment settings,
background supervisor, API, or service-owned infrastructure lifecycle. It never imports a
deployable's private package.

Keep service-specific policy local. Two functions with similar syntax but different business
meaning are duplication, not necessarily reusable abstraction.

The sample's `ctc_database`, `platform_db`, and `platform_observability` libraries show three
concrete forms of reuse: database access, table mappings, and telemetry mechanics. None needs
`application/`, `ports/`, and `bootstrap/` solely to resemble its consumers. A library earns
internal ports when it owns policy that needs replaceable dependencies; a focused database
implementation can remain directly coupled to its database technology.

[Shared Libraries](13_share_libraries_without_service_layers.md) owns the detailed walkthrough:
why the worker still wraps the shared CTC reader, how ORM models differ from domain state, and
which schema and lifecycle obligations remain with the consumers.

---

## 6. Navigation pain and dependency leaks are the growth signals

**Success signal:** a new engineer can locate a business action, its external contract, concrete
implementation, and construction site without searching generic catch-alls. A change to one
capability edits a cohesive small region rather than parallel empty layers.

⚠️ The first failure of premature growth is import choreography: `__init__.py` re-exports and
forwarding modules exist only to hide a speculative tree. The symptom is a simple rename touching
many packages without changing behavior.

Do not flatten boundaries merely to reduce directories when concrete framework imports would then
mix with business policy. Flat-first applies *inside* meaningful owners; it does not erase the
dependency graph.

> **Production:** preserve public imports deliberately during a file-to-package migration, then
> remove transitional re-exports after consumers move. Use absolute imports through the full
> package path so ownership remains visible.

---

**Next**: [Part 11 — Migrate and Review an Existing Service](11_migrate_and_review_an_existing_service.md)
