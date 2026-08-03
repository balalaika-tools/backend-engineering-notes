# Background-Work Operations

> Production boundaries for deciding who may create work, how tenants share finite capacity, and how worker fleets are sized safely.

---

## Contents

| File | Topic | Description |
|---|---|---|
| [01_security_and_authorization.md](01_security_and_authorization.md) | Trust and authorization | Protects trigger, control, broker, worker, and operator boundaries without trusting message payloads |
| [02_multitenancy_admission_and_fairness.md](02_multitenancy_admission_and_fairness.md) | Tenant isolation | Bounds accepted work, preserves fair progress, and separates rate, backlog, concurrency, and cost limits |
| [03_capacity_planning_and_autoscaling.md](03_capacity_planning_and_autoscaling.md) | Capacity | Converts arrival rate, service demand, resource ceilings, and queue-age SLOs into bounded scaling decisions |

---

## Reading Order

1. **Security and authorization** — establish which identities may create or control durable work.
2. **Multitenancy and admission** — reserve finite capacity without letting one tenant own the backlog.
3. **Capacity planning** — size and autoscale the fleet inside database, provider, memory, and cost ceilings.

---

## Prerequisites

- [Queue and Worker Architectures](../04_queue_and_worker_architectures.md)
- [Background-Work Reliability](../reliability/README.md)
- [Durable Fan-Out and Join](../07_durable_fanout_and_join.md)
