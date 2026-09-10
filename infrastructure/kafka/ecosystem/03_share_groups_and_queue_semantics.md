# Share Groups Add Queue-Like Work Distribution Without Rewriting the Log

> **Who this is for**: engineers whose workers need per-record acknowledgment and concurrency beyond partitions.

## One partition no longer means one active worker

Conventional groups assign a partition to one consumer. A **share group** can deliver records from
the same partition to multiple consumers and tracks per-record outcomes such as accept, release for
redelivery, or reject. This supports work-queue shapes while Kafka still retains the underlying log.

---

## 1. Flexibility changes the ordering and retry model

Concurrent record delivery weakens strict partition-order processing. Redelivery counts and
acknowledgment timeouts become central. Workers still need idempotent effects because leases,
timeouts, and crashes can cause repeated delivery.

With a 30-second record lock, the per-record state is inspectable:

```text
t=00  worker-a receives offset 41, delivery-count=1
t=08  ACCEPT 41                 → complete; no immediate redelivery
t=10  worker-a receives offset 42, delivery-count=1
t=18  RELEASE 42                → eligible now; worker-b receives count=2
t=20  worker-a receives offset 43, delivery-count=1
t=50  lock expires (no RENEW)   → worker-b receives 43, count=2
t=55  REJECT 43                 → terminal for the group's configured policy
```

`RENEW` extends the lock for legitimate long work; it does not acknowledge success. The changed
delivery count is the visible proof that release or timeout caused redelivery.

Share groups became [production-ready in Kafka 4.2](https://kafka.apache.org/blog/2026/02/17/apache-kafka-4.2.0-release-announcement/)
and continue to evolve in 4.3. Confirm client and broker support before choosing them; non-Java
client coverage may lag the broker feature.

**Success signal:** kill a worker holding one record and observe another worker receive it, while an
accepted record is not immediately redelivered. Throughput alone cannot prove acknowledgment policy.

> **Key insight**: share groups change record ownership and acknowledgment, not Kafka's retained-log
> foundation or the need for idempotent business effects.

---

## 2. What breaks, and when not to use share groups

⚠️ A processing timeout shorter than legitimate work causes concurrent redelivery of still-running
effects.

Do not use share groups when per-key order is a hard invariant or when your client ecosystem cannot
operate and observe them. Conventional groups or a mature dedicated queue may be the safer choice.

---

**Next**: [When to Use Kafka](04_when_to_use_kafka.md)
