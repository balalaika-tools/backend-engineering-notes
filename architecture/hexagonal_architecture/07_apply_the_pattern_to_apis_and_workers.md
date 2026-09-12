# Apply One Application Core to APIs and Workers

> **Who this is for**: Engineers building FastAPI routes, long-running workers, scheduled jobs, broker consumers, or a process that combines them.

HTTP and queues deliver different envelopes and demand different lifecycle behavior, but they need
not own different copies of the business action. Each inbound adapter translates its transport into
the action's typed input and translates the outcome back into transport behavior.

---

## 1. A FastAPI route should end at one public action

```python
@router.post(
    "/tickets/{ticket_id}/classification",
    response_model=ClassificationResponse,
)
async def classify_ticket(
    ticket_id: UUID,
    action: Annotated[ClassifyTicket, Depends(get_classify_ticket)],
) -> ClassificationResponse:
    result = await action.execute(ticket_id)
    return ClassificationResponse.model_validate(result)
```

The route owns path parsing, dependency resolution, and HTTP response shape. An exception handler
can map a stable `TicketNotFound` application failure to `404`. The route does not execute SQL,
invoke a model SDK, or decide whether low confidence requires review.

Keep transport-only Pydantic models under `api/schemas/`. Do not pass `Request`, response models,
or framework dependency objects into the application action.

> **Core:** an inbound adapter authenticates and translates the caller, invokes a public
> application action, then translates the result; it does not become a second application layer.

---

## 2. A broker consumer owns delivery mechanics

This explanatory excerpt omits the provider polling loop but shows the translation boundary:

```python
async def handle_delivery(self, delivery: SQSDelivery) -> DeliveryDecision:
    try:
        message = TicketClassificationMessage.model_validate_json(delivery.body)
        await self._classify_ticket.execute(message.ticket_id)
    except ValidationError:
        return DeliveryDecision.DEAD_LETTER
    except ClassificationUnavailable:
        return DeliveryDecision.RETRY
    else:
        return DeliveryDecision.ACK
```

The consumer owns wire-envelope validation, trace-context extraction, acknowledgement, visibility
heartbeat, retry delivery, and dead-letter mapping. The action owns durable admission, business
idempotency, classification, state transitions, and downstream handoff.

A receipt handle must never enter `application/`; it has meaning only to SQS. Likewise, Kafka
offsets and RabbitMQ delivery tags remain private to their adapters.

---

## 3. Transport retry and business retry answer different questions

Suppose the classifier times out:

```text
SQS delivery
  → consumer parses ticket_id=T-100
  → ClassifyTicket returns DEFERRED after recording business state
  → consumer ACKs the current delivery
  → a scheduler creates the next deliberate attempt
```

In another design, `ClassificationUnavailable` may escape and the consumer requests broker
redelivery. Both are possible, but the owner must be explicit. Blindly combining an application
retry loop with broker redelivery multiplies attempts and can duplicate effects.

Use the [retries and cancellation guide](../../background_work/reliability/04_retries_timeouts_and_cancellation.md)
for the complete reliability mechanism. Hexagonal boundaries make the decision visible; they do not
choose it for you.

---

## 4. Scheduled and one-shot workers need less shell

A scheduled batch that runs once and exits does not need a supervisor:

```python
async def main() -> int:
    async with build_runtime(load_settings()) as runtime:
        result = await runtime.submit_batch.execute()
        return 0 if result.accepted else 2
```

A long-running worker adds a supervisor because it owns repeated intake, task health, stop signals,
and bounded shutdown. Business stage ordering remains inside an action or explicit workflow under
`application/`.

Do not create a root `workflows/`, `pipeline/`, or `operations/` package as an alternative home
for business execution. Those concepts may exist beneath `application/` when the real use case has
commands, stages, or workflow semantics.

---

## 5. A hybrid process shares composition without merging boundaries

```text
main.py
bootstrap/
├── app.py
├── runtime.py
└── supervisor.py
api/
application/
ports/
adapters/
genai/
```

FastAPI lifespan may enter the runtime and start a consumer supervisor. API and worker adapters then
share clients and actions while retaining separate transport code.

Split the API and worker into independent deployables when they need separate scaling, release,
security, or failure isolation. Extract only stable shared contracts or domain logic; one deployable
must not import another deployable's private source.

> **Key insight**: reuse the application action, not the transport wrapper—HTTP and brokers should
> converge on typed business input and diverge again only when mapping the outcome.

---

## 6. Authorization and health stay explicit

Authentication extraction is transport work; authorization based on ticket ownership or tenant
policy is business work. Pass a trusted, immutable application context containing actor and tenant
identity. Never reconstruct trusted identity from a queue payload or AI prompt.

Liveness reports that the process is alive. Readiness reports whether it can accept useful work:
construction completed, required schema is compatible, and the worker is making acceptable
progress. Health probes must not execute business actions.

**Success signal:** the same action test covers policy for both API and worker entry points, while
small adapter tests cover `HTTP status ↔ application outcome` and `delivery outcome ↔ ack/retry`.

⚠️ The first failure is duplicated policy: the route rejects a closed ticket but the worker accepts
it. Move the rule into the shared action or domain owner rather than adding another transport check.

Do not share an action when the two entry points perform materially different business operations.
Give each operation a precise public action even if both call some of the same domain behavior.

For example, the supplied API's `RequestInvestigations` accepts durable work, while the worker's
`InvestigateException` performs it. These are different operations connected by an event and
persisted state. They should not be forced into one shared action merely because both refer to
investigations. The [investigation case study](12_trace_an_investigation_across_services.md) traces
that handoff and the handler's overlap between transport and execution coordination.

> **Production:** make shutdown, acknowledgement, idempotency, and trace propagation observable.
> A healthy route alone does not prove a background consumer is progressing.

---

**Next**: [Part 8 — Treat GenAI as an External Capability](08_treat_genai_as_an_external_capability.md)
