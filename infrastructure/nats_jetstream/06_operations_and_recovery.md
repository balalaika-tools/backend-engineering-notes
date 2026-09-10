# Operate JetStream Around Quorum, Disk, and Backlog

> **Who this is for**: engineers taking ownership of JetStream availability and recovery.

## Start with three explicit objectives

For an `ORDERS` stream, write down values before choosing topology:

```text
availability: continue after one server loss
replay:       retain 24 hours at the measured peak rate
recovery:     restore the stream and credentials in a tested target time
```

A three-replica stream on three JetStream-enabled servers can tolerate one replica loss while a
quorum remains. That does not prove 24-hour retention fits disk, nor that operator and account
identity can be recovered.

---

## 1. Replication protects a stream through quorum

JetStream uses Raft groups for replicated stream and consumer state. With replication factor
`R=3`, two replicas form a quorum; one unavailable server is tolerated. `R=5` can tolerate two but
adds storage, network, and commit latency. Put replicas in distinct failure domains or three copies
can disappear with one node or zone.

Replication is selected per stream. A three-node cluster does not automatically make an `R=1`
stream durable, and clustering Core NATS routes is not evidence that JetStream data is replicated.
The [official clustering guide](https://docs.nats.io/learn/topologies/jetstream-in-a-cluster)
separates those layers.

> **Core:** use `R=3` as the normal production starting point, then change it only from an explicit
> failure-domain and latency requirement.

---

## 2. Disk capacity must satisfy both steady state and recovery

Use local, high-performance persistent storage for file-backed streams. Bound each stream with age
and bytes, set account and server storage limits, and reserve headroom for bursts, compaction,
catch-up, and snapshots. A volume at 95% utilization has no useful recovery margin even if the
steady-state calculation fits.

Measure:

- stored bytes, messages, and oldest-message age per stream;
- publish rate and publish acknowledgment latency;
- consumer pending, acknowledgment-pending, and redelivery counts;
- replica leader, lag, and current/offline state;
- host disk latency, free bytes, CPU, memory, and network saturation.

The monitoring port exposes endpoints such as `/healthz`, `/varz`, and `/jsz`; the
[monitoring endpoint reference](https://docs.nats.io/learn/monitoring/monitoring-endpoints) explains
their scopes. Do not expose that unauthenticated HTTP port publicly.

> **Key insight**: stream health is the intersection of quorum, writable storage, and consumer
> progress; a green TCP connection proves none of the three.

---

## 3. Backlog age is usually more actionable than message count

Ten thousand pending messages could be seconds or hours of work. Alert on the age of the oldest
unprocessed business event when possible, then add count and redelivery rate for diagnosis.

```text
page:  no stream quorum, publish rejection, disk near exhaustion
page:  oldest required work exceeds its service-level objective
ticket: sustained growth with enough time and disk to intervene
```

Correlate backlog with downstream latency and worker concurrency. Scaling workers during a database
incident can amplify the failure; `MaxAckPending` and bounded fetches are the brake.

---

## 4. Replication and backup solve different failures

Replication keeps serving through node failure and faithfully replicates accidental deletion.
A **stream snapshot** creates a portable point-in-time backup of stream configuration and data.
Back up account/operator identity and server configuration separately; stream bytes alone may be
unusable when credentials and trust roots are lost.

Use the supported `nats stream backup` and `nats stream restore` workflow described in the
[backup and recovery guide](https://docs.nats.io/learn/backup-recovery/), store backups outside the
cluster's failure domain, and rehearse restore into an isolated environment. **Success signal:** a
fresh environment can authenticate, restore the stream, report the expected message count, and
consume a sampled event within the recovery target.

Do not treat filesystem-level copying of a live store directory as a portable logical backup.

> **Production:** pin a supported server series, read upgrade notes, back up before changes, and use
> rolling upgrades that preserve stream quorum. Test client reconnect behavior during the roll.

---

## 5. What breaks, and when not to self-host

⚠️ Losing quorum makes replicated stream writes unavailable even while clients can connect to Core
NATS. Publish acknowledgments and `/jsz` state expose the difference.

⚠️ Disk latency spikes first appear as publish-ack latency, replica lag, and growing consumer
backlog. CPU dashboards alone miss the bottleneck.

Do not self-host when the team cannot own quorum incidents, storage forecasts, security upgrades,
and restore drills. A managed NATS service or an already-operated durable broker may be the safer
system even when JetStream's API is attractive.

> **Edge case:** mirrors, sources, gateways, and leaf nodes address cross-cluster or edge movement.
> Introduce them only after defining data ownership, consistency expectations, and failover writes;
> they are not a transparent multi-region switch.

---

**Next**: [Security and Multitenancy](07_security_and_multitenancy.md)
