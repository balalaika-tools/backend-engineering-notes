# Kafka Operations

> Make the cluster's security, capacity, health, and recovery contracts observable.

---

## Contents

| File | Role | Reader outcome |
|---|---|---|
| [Security and multitenancy](01_security_and_multitenancy.md) | Implementation | Authenticate, encrypt, authorize, and isolate clients |
| [Capacity and performance](02_capacity_planning_and_performance.md) | Decision guide | Estimate partitions, storage, and throughput |
| [Observability and incidents](03_observability_and_incident_response.md) | Implementation | Detect lag, replica, disk, and coordinator failures |
| [Deployment and recovery](04_deployment_upgrades_and_disaster_recovery.md) | Deep dive | Plan ownership, upgrades, and regional recovery |
| [Configuration and topic administration](05_configuration_and_topic_administration.md) | Reference | Change topics and effective configs with rollback evidence |

**Working result by entry 2**: apply a least-privilege client/ACL policy and calculate a partition,
storage, network, and catch-up envelope. **Stop here if** a managed provider owns brokers; still
read observability because application lag remains yours. Continue to configuration administration
when your team creates topics or changes broker/client policy.
