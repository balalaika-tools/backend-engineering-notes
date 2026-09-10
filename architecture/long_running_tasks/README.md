# Long-Running Task Client-Delivery and Infrastructure Patterns

This section compares client delivery, failure detection, and infrastructure choices after the
application already owns a durable job lifecycle. The canonical implementation of that lifecycle
lives in [Background Work](../../background_work/README.md); these notes use small excerpts and do
not replace its state-machine and reliability owners.

---

## The Problem

A synchronous request stops fitting when its end-to-end timeout budget, occupied server/dependency
capacity, retry ambiguity, or user experience no longer matches the work. There is no universal
30-second boundary: a short request can still need a durable job, while a deliberately streamed
long response can remain synchronous when every intermediary and client supports that contract.

The core idea is always the same:

```
Client  ──POST /jobs──►  Orchestrator  ──dispatch──►  Worker(s)
   ◄── 202 Accepted ──┘                                   │
   │                                                      │
   │    (time passes — seconds to hours)                  │
   │                                                      │
   ◄────── result arrives via one of many patterns ◄──────┘
```

The client gets an **immediate acknowledgement**, then retrieves or receives the result later. The design decisions are:

1. **How does the orchestrator know what the worker is doing?** (Orchestration patterns)
2. **How does the worker report progress, success, and failure?** (Worker patterns)
3. **How does the client get the result?** (Client delivery patterns)
4. **What infrastructure connects the layers?** (Infrastructure choices)

---

## Architecture Layers

### Layer 1 — Client

The consumer of the API. Submits work, receives an acknowledgement, then waits for or fetches the result. The client's concern is: *how do I know when my result is ready, and how do I get it?*

### Layer 2 — Orchestrator

The control plane. Receives the request, dispatches it to a worker, tracks the lifecycle (pending → running → succeeded/failed), and routes the result back to the client. The orchestrator's concern is: *how do I know if the worker is alive, and what do I do if it dies?*

### Layer 3 — Worker

The execution plane. Receives a task, does the work (inference, classification, processing), and reports the outcome. The worker's concern is: *how do I signal progress and deliver results?*

---

## Contents

| Part | File | What It Covers |
|------|------|----------------|
| 1 | [Orchestration Patterns](01_orchestration_patterns.md) | Fire-and-forget, heartbeat monitoring, task tokens, polling — how the orchestrator manages worker lifecycles |
| 2 | [Worker Patterns](02_worker_patterns.md) | Health reporting, result storage, batch collection, graceful vs ungraceful failure, idempotency |
| 3 | [Client Delivery Patterns](03_client_delivery_patterns.md) | WebSocket, SSE, long-polling, short-polling, webhook, Redis pub/sub — how the client gets results |
| 4 | [Infrastructure & Technology](04_infrastructure.md) | Redis, RabbitMQ, SQS, Kafka, Step Functions, Celery, PostgreSQL LISTEN/NOTIFY — concrete tools for each pattern |
| 5 | [Advanced Patterns](05_advanced_patterns.md) | Saga, Outbox, delivery semantics (at-least-once vs exactly-once), progress/cancellation/resumability, distributed tracing |

---

## When to Use What — Quick Decision Tree

```
Can the request safely fit the end-to-end deadline and resource budget?
├── Yes → Keep the synchronous contract; make retries and cancellation explicit
└── No → Create durable job state and return its status URL
    ├── Does the client need server-pushed progress?
    │   ├── No → Short polling is the universal fallback
    │   └── Yes
    │       ├── Is progress one-way from server to client?
    │       │   ├── Yes → SSE plus durable status recovery
    │       │   └── No → WebSocket plus durable status recovery
    └── Is the consumer another service with a registered callback endpoint?
        ├── Yes → Signed webhook notification plus durable status recovery
        └── No → Short polling
```

---

## Learning path

**Working result by entry 2**: choose a client delivery contract and a worker-loss detection
contract while retaining the Background Work job row as the source of truth.

1. **Do:** [Client Delivery Patterns](03_client_delivery_patterns.md) — start with authenticated short polling and select SSE only for one-way push or WebSocket for bidirectional interaction.
2. **Understand:** [Orchestration Patterns](01_orchestration_patterns.md) — add a bounded callback/heartbeat deadline without replacing durable job state.
3. **Harden:** [Worker Patterns](02_worker_patterns.md) — add lease ownership, idempotent effects, and shutdown behavior; use the canonical [reliability notes](../../background_work/reliability/README.md) for complete implementations.
4. **Choose infrastructure only when needed:** [Infrastructure](04_infrastructure.md), then [Advanced Patterns](05_advanced_patterns.md).

**Stop here if** short polling plus a durable status row and explicit worker-loss deadline meets the
product requirement. Continue to push transports, brokers, workflow engines, sagas, or tracing only
when a named interaction or failure requirement demands them.

---

## Terminology

| Term | Meaning |
|------|---------|
| **Job / Task** | A unit of work submitted by the client |
| **Dispatch** | The act of sending a task to a worker |
| **Heartbeat** | A periodic signal proving liveness |
| **Task Token** | An opaque string the worker uses to signal completion back to the orchestrator |
| **Callback** | A message from the worker to the orchestrator (or client) saying "I'm done" |
| **Visibility Timeout** | How long a message is hidden from other consumers after being read (SQS concept, but the pattern is universal) |
| **Dead Letter Queue (DLQ)** | Where messages go after repeated processing failures |
| **Backpressure** | Mechanisms that slow down producers when consumers can't keep up |
