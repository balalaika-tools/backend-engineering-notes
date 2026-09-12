# Migrate and Review an Existing Service Incrementally

> **Who this is for**: Engineers untangling an established FastAPI service, worker, consumer, or AI backend without mixing folder movement with business redesign.

A structure-only migration should preserve behavior while making ownership and dependency direction
visible. Start with one business action and its tests; do not begin by moving every file into a
canonical tree.

---

## 1. Inventory execution before proposing folders

For each candidate module, record:

| Question | Example answer |
|----------|----------------|
| Public entry point | `POST /tickets/{id}/classification` |
| Business action | Classify one eligible ticket |
| External effects | PostgreSQL, model call, SQS publication |
| Current inbound processes | FastAPI and bulk-import worker |
| Success/failure contract | Classified, human review, unavailable |
| Lifecycle resources | Engine, model HTTP client, consumer loop |
| Tests and prerequisites | Mixed route test requiring DB and model patches |

Trace imports in both directions. Inspect actual test execution, fixture scope, markers, hooks, CI
selectors, and deployment entry points; filenames alone do not reveal whether a test is unit or
integration.

> **Core:** establish the current behavior and ownership map before choosing the target directory
> map.

---

## 2. Classify findings by impact rather than taste

Use three labels:

- **Violation:** an inner package imports a concrete outer technology, provider failures cross a
  port, or business policy lives in transport code.
- **Improvement:** a split would materially clarify ownership, testing, lifecycle, or change
  isolation.
- **Preference:** naming or layout differs but dependency direction and ownership remain clear.

For example, naming the composition package `wiring/` instead of `bootstrap/` is usually a
preference in a generic review. In this repository's standardized service layout, rename it only
when consistency provides a stated navigation benefit—not because one name is universally correct.

---

## 3. Choose one vertical migration slice

For the coupled route from part 1, the smallest coherent sequence is:

```text
1. Characterize current HTTP behavior with tests
2. Extract typed ClassificationCandidate and stable failures
3. Extract ClassifyTicket with existing behavior unchanged
4. Put provider invocation behind TicketClassifier
5. Move SQLAlchemy queries to the persistence owner
6. Keep the route as input/output translation
7. Move concrete construction and disposal to bootstrap
8. Add the worker against the public action
```

Move contract errors before application code would otherwise import concrete provider errors.
Move deterministic policy with its focused tests. Then move concrete integrations and composition.

Run the focused test slice after each meaningful step. Repository-wide movement followed by one
large test run makes regressions hard to locate and review.

---

## 4. Preserve behavior while changing dependency ownership

Temporary re-exports can keep consumers working during an incremental migration:

```python
# Transitional compatibility import; remove after callers migrate.
from ticket_triage.application.classify_ticket import ClassifyTicket

__all__ = ["ClassifyTicket"]
```

Mark these explicitly and track their removal. Do not leave two permanent public paths to the same
action.

Update more than Python imports: process commands, FastAPI app targets, worker entry points,
migrations, fixture paths, pytest markers, pre-commit filters, CI selectors, Docker commands,
telemetry service names, and documentation may all encode the old layout.

---

## 5. Review the final dependency graph and test evidence

The highest-value final checks are:

```text
[ ] Every public business action lives under application/
[ ] Domain and ports import no framework, ORM, provider SDK, or bootstrap
[ ] Application imports no API, adapter, DB, GenAI, or concrete client
[ ] API and consumers call public application actions
[ ] Concrete adapters translate success and failure at their boundary
[ ] Bootstrap alone selects ordinary runtime implementations
[ ] Every AI concern lives below root genai/
[ ] No root messaging/, utils/, common/, or global error/constant bucket exists
[ ] Packages are flat until demonstrated pressure justifies nesting
[ ] Unit tests replace costly boundaries without patching SDK internals
[ ] Integration and E2E prerequisites are explicit and reproducible
[ ] All Python imports use the full absolute package path
```

**Success signal:** changing the classifier implementation modifies its adapter and bootstrap wiring
while application tests remain unchanged; adding a worker reuses the action without importing
FastAPI.

> **Key insight**: a successful migration is measured by smaller, one-way dependency edges and
> clearer test seams—not by how closely the final tree resembles a diagram.

---

## 6. Migration failures have recognizable symptoms

⚠️ If a “structure-only” pull request changes business outcomes, retry semantics, and data models,
reviewers cannot separate movement risk from redesign risk. Split the work.

⚠️ If tests pass only after broadening `pythonpath`, importing another service's test helpers, or
promoting expensive fixtures to root scope, the migration damaged test ownership.

Do not migrate a stable small service merely for symmetry with a larger neighbor. Apply the
architecture when it solves named ownership, change, lifecycle, or test-isolation problems.

> **Production:** remove transitional imports only after import searches, focused suites, type
> checks, process startup checks, and CI profiles prove all consumers moved. Roll out independently
> deployed process changes in a compatibility-safe order.

---

## 7. The review can now answer operational questions

After migration, a cold reader should be able to answer:

- Where is each process constructed and shut down?
- Which action owns classification policy?
- Which contract shields that action from model-provider behavior?
- Where are provider errors translated?
- Which test proves business handling without live infrastructure?
- Which integration test proves the concrete repository or broker?
- What fails readiness when construction is incomplete?
- Which module changes when HTTP, broker, database, or model details change?

If those answers still require tracing through generic services and utilities, the folder movement
did not establish meaningful ownership.

---

**Next**: [Part 12 — Trace One Investigation Across Services](12_trace_an_investigation_across_services.md), or return to the [Hexagonal Architecture index](README.md) for another reading path.
