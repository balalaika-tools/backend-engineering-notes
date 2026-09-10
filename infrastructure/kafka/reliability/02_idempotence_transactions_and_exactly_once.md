# Kafka's “Exactly Once” Ends at the Boundary It Can Transact

> **Who this is for**: engineers reviewing duplicate-prevention or exactly-once claims.

## Three mechanisms cover three scopes

| Mechanism | Prevents | Does not prevent |
|---|---|---|
| Producer idempotence | duplicate log appends from one producer retry sequence | duplicate business events sent intentionally |
| Kafka transaction | partial writes across Kafka records and consumed offsets | duplicate effects in an unrelated database or API |
| Consumer idempotency | repeated business effects for a stable key | data loss from committing too early |

---

## 1. Producer idempotence makes retry sequencing safe

Modern clients enable idempotence when compatible settings remain in force. `acks=all`, retries,
and bounded in-flight requests let the broker reject duplicate sequence numbers. Explicitly verify
effective configuration rather than assuming a framework wrapper preserved it. See the
[Kafka 4.3 producer configuration](https://kafka.apache.org/43/configuration/producer-configs/)
for the exact compatibility constraints.

---

## 2. Transactions atomically publish Kafka output and offsets

A consume-transform-produce application can write output records and the input group's offsets in
one Kafka transaction. `read_committed` consumers hide aborted transactional records. If the effect
is a card API call, Kafka cannot roll it back; use the provider's idempotency key or a durable local
state transition.

For input `orders[2]@8`, the processor API sequence and visible state are:

```text
producer.begin_transaction()
producer.produce("billing.commands", key="ord-42", value=charge)
producer.send_offsets_to_transaction({orders[2]: 9}, group_metadata)
producer.commit_transaction()
```

The offset value is `9`, the next record to read. If the process crashes after `produce` but before
commit, the broker aborts or times out the open transaction: a `read_committed` consumer sees no
charge, and the input group still resumes at offset 8. After restart, one committed charge and
offset 9 become visible together. If commit succeeds before the crash, restart begins at 9 and does
not repeat offset 8.

```text
crash point                         read_committed output   group resumes
after produce, before commit        none                    orders[2]@8
after successful commit             one charge              orders[2]@9
explicit abort_transaction()        none                    orders[2]@8
```

**Success signal:** kill the processor before commit and observe no partial output with
`read_committed`; restart and obtain one committed result. Counting records under the default
isolation silently includes aborted work.

> **Key insight**: exactly-once processing is credible only after naming every state store inside
> the atomic boundary and the strategy for every store outside it.

---

## 3. What breaks, and when not to use transactions

⚠️ Reusing a transactional ID concurrently fences one producer instance; generating random IDs on
every restart loses the stable identity required for recovery.

Avoid Kafka transactions for simple source producers or external sinks when idempotent writes are
clearer and cheaper.

---

**Next**: [Retries, Dead Letters, and Replay](03_retries_dead_letters_and_replay.md)
