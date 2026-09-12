# Hexagonal Architecture for Python Backends

> Design FastAPI services, workers, and AI-enabled backends so business actions outlive their current frameworks and providers.

---

## Contents

| File | Role | Topic | Reader outcome |
|------|------|-------|----------------|
| [01 — Why Hexagonal Architecture](01_why_hexagonal_architecture.md) | Foundation | The coupling pressure behind the pattern | Decide whether the pattern buys enough for a service |
| [02 — Build One Vertical Slice](02_build_one_vertical_slice.md) | Tutorial | A complete application action with two inbound adapters | Run one use case from API- and worker-shaped entry points |
| [03 — Dependency Direction](03_dependencies_point_toward_business_policy.md) | Foundation | Runtime calls versus source dependencies | Draw and audit the dependency rule |
| [04 — Boundary Placement](04_map_code_to_owning_boundaries.md) | Decision guide | Where source files belong | Place ambiguous code by ownership rather than framework |
| [05 — Ports and Adapter Contracts](05_design_ports_and_adapter_contracts.md) | Deep dive | Port admission, types, and failure translation | Define useful contracts without interface ceremony |
| [06 — Runtime Composition](06_compose_the_runtime_at_the_edge.md) | Implementation | Construction, lifespan, and disposal | Build a composition root without leaking policy into bootstrap |
| [07 — APIs and Workers](07_apply_the_pattern_to_apis_and_workers.md) | Implementation | FastAPI, consumers, scheduled work, and hybrids | Reuse an action across process boundaries safely |
| [08 — GenAI Boundary](08_treat_genai_as_an_external_capability.md) | Deep dive | Models, prompts, agents, tools, and typed results | Keep AI mechanics outside business execution |
| [09 — Testing Boundaries](09_test_through_architectural_boundaries.md) | Implementation | Unit, integration, contract, and E2E tests | Test business behavior without patching SDK internals |
| [10 — Flat-First Growth](10_grow_without_package_ceremony.md) | Decision guide | When modules earn packages and abstractions | Grow structure without empty layers or catch-alls |
| [11 — Migration and Review](11_migrate_and_review_an_existing_service.md) | Decision guide | Moving an existing service safely | Produce an ownership map and incremental migration plan |
| [12 — Investigation Case Study](12_trace_an_investigation_across_services.md) | Deep dive | One request across API, outbox, worker, and checkpoints | Trace durable changes and distinguish implemented behavior from unproven recovery guarantees |
| [13 — Shared Libraries](13_share_libraries_without_service_layers.md) | Decision guide | Shared integrations, ORM models, and resource ownership | Explain which boundaries remain service-owned and when a library needs its own ports |

---

## Reading Order

For the supplied `temp/services` and `temp/libs` examples, use
[Read the service and library samples](#read-the-service-and-library-samples) after the first-time
path's working slice. The case study includes self-contained traces so the temporary source
folders are not required to understand it.

### First-time path

**Working result by entry 2**: run one ticket-classification action through both an HTTP-shaped
handler and a queue-worker-shaped handler, then observe identical business results.

1. **Do—diagnose the coupled route:** [Why Hexagonal Architecture](01_why_hexagonal_architecture.md) traces concrete change requests and assigns each resulting responsibility an owner.
2. **Do—build the separated result:** [Build One Vertical Slice](02_build_one_vertical_slice.md) runs the smallest complete example.
3. **Understand the mechanism:** [Dependency Direction](03_dependencies_point_toward_business_policy.md) separates runtime call flow from import direction.
4. **Organize:** [Boundary Placement](04_map_code_to_owning_boundaries.md) turns that rule into a Python package tree.
5. **Harden when required:** add [Port Contracts](05_design_ports_and_adapter_contracts.md), [Runtime Composition](06_compose_the_runtime_at_the_edge.md), and [Boundary Testing](09_test_through_architectural_boundaries.md).

**Stop here if** one process, a small action, and direct dependencies remain easy to test and
change. Continue when another transport, costly external capability, complex lifecycle, or
independent failure policy appears.

### FastAPI plus worker path

1. **Do:** run the [vertical slice](02_build_one_vertical_slice.md).
2. **Understand:** trace [dependency direction](03_dependencies_point_toward_business_policy.md).
3. **Extend:** apply the action to [APIs and workers](07_apply_the_pattern_to_apis_and_workers.md).
4. **Harden:** centralize [runtime composition](06_compose_the_runtime_at_the_edge.md) and split [test profiles](09_test_through_architectural_boundaries.md).

### AI backend path

1. **Do:** run the [vertical slice](02_build_one_vertical_slice.md), whose fake classifier represents a nondeterministic external capability.
2. **Understand—revisit the reasoning:** read [Why Hexagonal Architecture](01_why_hexagonal_architecture.md) and map each example boundary back to the change pressure that earned it.
3. **Implement:** place provider code behind the [GenAI boundary](08_treat_genai_as_an_external_capability.md).
4. **Harden:** test deterministic policy separately using [boundary-oriented tests](09_test_through_architectural_boundaries.md).

### Existing-service migration path

1. **Do—map one current action:** use [Why Hexagonal Architecture](01_why_hexagonal_architecture.md) to label the business decisions and external effects in one coupled entry point.
2. **Understand:** audit its [dependency direction](03_dependencies_point_toward_business_policy.md) and assign each file through the [boundary placement test](04_map_code_to_owning_boundaries.md).
3. **Change incrementally:** apply [flat-first growth](10_grow_without_package_ceremony.md) and the [migration sequence](11_migrate_and_review_an_existing_service.md) one vertical slice at a time.

**Stop after the ownership map if** the current structure already preserves one-way dependencies
and clear test seams. Move code only when the map exposes a concrete violation or isolation gain.

---

### Read the service and library samples

**For:** readers who can already explain an application action and a port, and want to understand
the supplied orchestrator, worker, and shared packages. Start with the first-time path above if
those boundaries are still unfamiliar.

**Working outcome by entry 2:** trace one accepted investigation into durable work and explain
why its repositories share a transaction without sharing a session across concurrent requests.

1. **Do:** follow sections 1–3 of [the investigation case study](12_trace_an_investigation_across_services.md), from the HTTP request through acceptance and active-work deduplication.
2. **Understand:** read [the Unit of Work mechanism](05_design_ports_and_adapter_contracts.md#6-a-unit-of-work-makes-several-repositories-one-transaction), then identify the port, concrete session owner, and factory in that trace.
3. **Extend across packages:** [Shared Libraries](13_share_libraries_without_service_layers.md) follows the CTC reader, shared table models, and observability configuration.
4. **Harden—revisit the case study:** continue sections 4–7 for publication crashes, delivery ownership, checkpointed retry, and test evidence. Follow its reliability links for the full mechanisms.
5. **Inspect construction:** [Runtime Composition](06_compose_the_runtime_at_the_edge.md) and [GenAI](08_treat_genai_as_an_external_capability.md) explain partial-startup cleanup, supervisors, context snapshots, and cached agent construction.

**Stop after entry 3 if** you can locate and explain the action, transaction, service adapter,
shared implementation, and construction site. Continue when diagnosing retries, stale ownership,
startup failures, or changing AI configuration. The samples illustrate ownership decisions; they
are not a universal scaffold or proof that every failure window is handled.

---

## Prerequisites

- Comfortable reading typed Python and `async` functions.
- [FastAPI fundamentals](../../fundamentals/fastapi/README.md) are useful for the API chapter but not required for the first example.
- [Testing fundamentals](../../operations/testing/README.md) provide the broader pytest path; this section focuses on tests as architectural evidence.
- For the sample-reading path, [context managers](../../fundamentals/core_concepts/context_managers.md) and [typing](../../fundamentals/core_concepts/typing.md) provide optional depth on `async with`, `Callable`, and structural protocols.
