# Long-Running Task & Batched Inference Patterns

A comprehensive guide to handling requests that take seconds to hours — covering every layer from the client that submits the work, through the orchestrator that manages the lifecycle, to the worker that does the heavy lifting.

---

## The Problem

A synchronous HTTP request has a natural ceiling: load-balancer timeouts (30–60 s), client timeouts, and the fact that holding a connection open wastes resources on both sides. When work takes **minutes** (LLM inference, batch classification, report generation, video processing) or **hours** (full dataset reprocessing, model training), you need an asynchronous architecture.

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

## Guide Structure

| Part | File | What It Covers |
|------|------|----------------|
| 1 | [Orchestration Patterns](01_orchestration_patterns.md) | Fire-and-forget, heartbeat monitoring, task tokens, polling — how the orchestrator manages worker lifecycles |
| 2 | [Worker Patterns](02_worker_patterns.md) | Health reporting, result storage, batch collection, graceful vs ungraceful failure, idempotency |
| 3 | [Client Delivery Patterns](03_client_delivery_patterns.md) | WebSocket, SSE, long-polling, short-polling, webhook, Redis pub/sub — how the client gets results |
| 4 | [Infrastructure & Technology](04_infrastructure.md) | Redis, RabbitMQ, SQS, Kafka, Step Functions, Celery, PostgreSQL LISTEN/NOTIFY — concrete tools for each pattern |

---

## When to Use What — Quick Decision Tree

```
Is the task < 30 seconds?
├── Yes → Consider synchronous with timeout + retry
└── No
    ├── Do you need real-time progress updates?
    │   ├── Yes → WebSocket (client) + Heartbeat (orchestrator)
    │   └── No
    │       ├── Is the client a browser/mobile app?
    │       │   ├── Yes → SSE or Short-Polling (client) + Fire-and-Forget with Callback (orchestrator)
    │       │   └── No (server-to-server)
    │       │       ├── Can the client expose an endpoint?
    │       │       │   ├── Yes → Webhook (client) + Fire-and-Forget with Callback (orchestrator)
    │       │       │   └── No → Short-Polling (client) + any orchestration pattern
    │       └── Is failure detection critical (SLA)?
    │           ├── Yes → Task Token or Heartbeat (orchestrator)
    │           └── No → Fire-and-Forget with Callback (orchestrator)
```

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
