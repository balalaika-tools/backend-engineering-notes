# Delivery Semantics Are the Order of Effect and Checkpoint

> **Who this is for**: engineers who need to state what a consumer does across crashes.

## One crash distinguishes the guarantees

```text
at-most-once:  commit offset 8 → process 8 → crash   = possible loss
at-least-once: process 8 → effect succeeds → crash → no commit = possible duplicate
```

---

## 1. No commit order removes both outcomes

Committing before processing can skip work. Committing after processing can repeat work. Kafka's
usual baseline is at-least-once plus an idempotent effect, because duplicates are often safer than
silent loss.

An **idempotency key** makes repeated execution converge on one effect. Use stable `event_id`, not
partition offset alone, when the same logical event might be republished elsewhere.

---

## 2. Test the crash windows, not only the happy path

Inject failure before the effect, after the effect, and before the offset commit. **Success signal:**
every input reaches the intended final state and repeated attempts leave one effect. A passing
consumer test without process termination silently proves none of this. Use the reusable
[Kafka service test harness](05_testing_kafka_services.md) to control those crash points.

> **Key insight**: “once” is not a client setting; it is a claim about the combined record,
> checkpoint, and business-effect state transition.

---

## 3. What breaks, and when not to chase exactly once

⚠️ Committing a batch after only some records succeed skips the failed records on restart.

Do not pay transaction complexity for naturally idempotent derived state. Replacing a projection row
by stable key may already make replay safe.

---

**Next**: [Idempotence, Transactions, and Exactly Once](02_idempotence_transactions_and_exactly_once.md)
