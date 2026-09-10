# Choose JetStream When Durable Subject Messaging Is the Requirement

> **Who this is for**: engineers choosing messaging infrastructure for a backend system.

## Decide from the failure contract

Start with one scenario: an order event must survive a worker restart, be replayable for 24 hours,
and normally reach workers with low operational overhead. JetStream is a strong candidate because
subjects provide flexible routing while streams and consumers add bounded persistence and
server-side progress.

Change the requirement to “connected dashboards receive current updates; missed updates do not
matter,” and Core NATS is the better starting point. Change it to “retain years of events for many
independent analytical readers,” and Kafka deserves evaluation.

---

## 1. Use the smallest system that owns the needed guarantee

| Requirement | Initial choice | Reason to move away |
|---|---|---|
| Transient fan-out or request/reply | Core NATS | Offline consumers must catch up |
| Durable work and replay with subject routing | **JetStream** | Existing platform or ecosystem dominates |
| Large retained event-log platform and stream ecosystem | Kafka | Operational scale/complexity is unjustified |
| Traditional broker queues and rich exchange routing | RabbitMQ | Replayable retained history is central |
| Cloud-native managed queue with minimal operations | SQS or peer service | Portability, latency, or routing needs differ |
| Work atomically coordinated with one database | Database queue/outbox | Independent scaling or broker routing is needed |

These are starting points, not benchmark conclusions. Existing team expertise, managed-service
availability, client maturity, compliance, and measured workload can outweigh abstract feature fit.

> **Core:** decide from durability, replay, ordering, routing, and ownership requirements before
> comparing throughput numbers or client syntax.

---

## 2. JetStream's useful center is smaller than “all streaming”

JetStream fits especially well when a team already values NATS for request/reply or transient
pub/sub and needs selected subjects to become durable. One server binary provides Core NATS and
JetStream, while accounts and subject permissions keep their contracts distinct.

It also suits work queues, event distribution, last-value/state patterns, and edge topologies when
their retention and consistency semantics are explicit. Higher-level Key/Value and Object Store
APIs are built on JetStream, but they are not substitutes for a transactional database or general
blob store.

> **The near-miss**: JetStream is often described as “Kafka-lite.” Both retain messages, but NATS
> starts from subject-based messaging and adds streams; Kafka starts from partitioned logs. Their
> ordering, consumer, ecosystem, and operating models should be evaluated directly.

---

## 3. Reject JetStream when another owner already has the state

If a job must be created in the same transaction as application rows, a PostgreSQL queue or
transactional outbox may be the first correct mechanism. Adding a direct publish inside that
transaction creates a dual-write gap; JetStream does not participate in the database commit.

If the workload is a multi-step business process with timers, compensation, human approval, and
queryable progress, use durable workflow state or an orchestration engine. JetStream can carry task
notifications, but consumer state is not the canonical business state machine. See
[When a task becomes a workflow](../../background_work/foundations/02_task_or_workflow.md).

> **Key insight**: choose JetStream for durable message movement; keep business truth in the system
> that can enforce its transaction and lifecycle invariants.

---

## 4. Prove the choice with a workload-shaped trial

Before adoption, test the actual payload distribution, publish durability, consumer concurrency,
retention window, and failure domains. Terminate a worker after its effect commits, remove one
stream replica, fill storage toward its alert threshold, and restore a snapshot into isolation.

**Success signal:** the trial meets latency and recovery objectives without lost required events,
duplicate business effects, unbounded backlog, or manual state repair. A happy-path messages/second
number is insufficient; it can hide slow disks, redelivery storms, and a missing recovery procedure.

> **Production:** include client upgrade cadence and broker ownership in the decision record. A
> technically suitable broker with no team responsible for patching and recovery is not production-ready.

---

## 5. What breaks, and when to stop evaluating

⚠️ Comparing only peak throughput ignores the cost of ordering, replicas, acknowledgment mode,
payload size, and downstream work. Benchmark the required guarantee, not the loosest configuration.

⚠️ Treating a durable consumer as workflow state loses domain meaning during replay, deletion, or
operator repair. Persist business progress explicitly.

Stop evaluating JetStream when an existing approved platform meets the delivery contract and the
migration would only replace syntax. Adopt it when its unified Core NATS plus durable-subject model
removes systems or materially simplifies the required topology.

---

**Next**: [NATS JetStream learning paths](README.md)
