# Test Through Architectural Boundaries

> **Who this is for**: Engineers who want tests to prove dependency isolation instead of mirroring implementation details.

The fastest evidence that an application boundary works is a business test with small port fakes.
The strongest evidence that the complete service works comes later from adapter integration and
end-to-end tests. One test type cannot replace the other.

---

## 1. Test application decisions without infrastructure

```python
class StubClassifier:
    def __init__(self, candidate: ClassificationCandidate) -> None:
        self.candidate = candidate

    async def classify(self, body: str) -> ClassificationCandidate:
        return self.candidate


async def test_low_confidence_requires_human_review():
    tickets = InMemoryTickets([open_ticket("T-100")])
    classifier = StubClassifier(
        ClassificationCandidate(category="billing", confidence=0.61)
    )

    result = await ClassifyTicket(tickets, classifier).execute("T-100")

    assert result.outcome is Outcome.HUMAN_REVIEW
```

This test proves business interpretation of confidence. It does not patch a model SDK, SQLAlchemy
session, FastAPI dependency, or AWS client because none of those owns the rule.

> **Core:** application tests replace external effects at port or repository boundaries; adapter
> tests replace or provision the adapter's direct technology collaborator.

---

## 2. Classify tests by what they execute

Once a suite has more than one execution profile, use profile first and behavioral owner second:

```text
tests/
├── unit/
│   ├── application/
│   ├── domain/
│   ├── api/
│   ├── adapters/
│   ├── genai/
│   └── bootstrap/
├── integration/
│   ├── db/
│   └── adapters/
├── contract/
│   ├── config/
│   └── deployment/
└── e2e/
```

A concrete SQS adapter tested with an injected fake AWS client is still a unit test. The same
adapter against a disposable emulator is integration. A FastAPI route exercised in-process with
outer ports replaced is a unit test of transport translation, not an end-to-end test.

The [testing notes](../../operations/testing/README.md) own general pytest mechanics. This chapter
owns how test seams demonstrate the architectural dependency graph.

The [investigation case study's evidence map](12_trace_an_investigation_across_services.md#7-tests-turn-the-source-tour-into-evidence)
connects existing sample tests to acceptance, checkpointed retry, and delivery behavior. Pay
attention to fake semantics: an in-memory Unit of Work with a no-op commit can prove action
sequencing while proving nothing about database rollback. Use real transaction tests for that
claim rather than making the fake look like an ORM.

---

## 3. Adapter tests prove translation in both directions

For an LLM classifier, test provider output to port output and provider failure to port failure:

```python
async def test_timeout_becomes_stable_unavailable_error():
    model = FakeModel(raises=ProviderTimeout("upstream timed out"))
    classifier = LLMTicketClassifier(model)

    with pytest.raises(ClassificationUnavailable):
        await classifier.classify("invoice is wrong")
```

For an API route, test request fields and application outcomes against status and response fields.
For a broker consumer, test envelope parsing and `ACK`, `RETRY`, or `DEAD_LETTER` mapping.

Changing one input should change one observable decision. If the test asserts private method calls
without checking translation, it locks down implementation while leaving the boundary unproved.

---

## 4. Bootstrap tests prove selection and disposal

Construction has different failure modes from business execution. Inject constructors or factories
and verify:

```text
settings select "rules"
  → RulesTicketClassifier constructed
  → ClassifyTicket receives that instance
  → shutdown disposes the shared engine exactly once
```

Do not prove classification policy again through bootstrap. Its contract is selection, dependency
closure, startup order, readiness, and disposal.

Integration tests own disposable databases, brokers, filesystems, or local protocol endpoints.
They require bounded timeouts and deterministic cleanup. A destructive database reset must refuse
an ordinary service database URL.

---

## 5. E2E tests close the gap without swallowing the suite

An end-to-end (E2E) test starts the deployable or crosses several real boundaries:

```text
POST /tickets/T-100/classification
  → application action
  → disposable PostgreSQL
  → fake local classifier endpoint
  → persisted result
  → 200 response
```

Use a fake local external endpoint when the claim is service composition rather than provider
availability. A paid or shared staging model call is a separate opt-in **live test**, with bounded
inputs, cost, credentials, and an explicit CI selector.

> **Key insight**: test folders should reveal both the execution cost and the behavior owner; the
> ability to test business decisions with tiny fakes is evidence that dependencies point inward.

---

## 6. Failure symptoms show where seams are wrong

⚠️ If application tests patch `boto3`, model SDK internals, or SQLAlchemy calls several modules
away, the application boundary is leaking concrete ownership.

⚠️ If an integration CI job passes because every intended test skipped when infrastructure was
missing, the profile has no trustworthy success signal. Selected jobs must fail fast on absent
prerequisites.

**Success signal:** the ordinary unit suite runs without network, provider credentials, live
wall-clock sleeps, or external processes. Each integration and E2E profile also has a direct,
reproducible command and provisions its prerequisites explicitly.

Do not introduce profile directories for four deterministic library tests. Keep a small suite flat
until a second execution environment makes the distinction real. Do not mirror every source folder
automatically; add an owner directory only when navigation, fixture scope, or naming pressure
justifies it.

> **Production:** align pytest markers, strict marker registration, fixture scope, local commands,
> pre-commit or pre-push selection, and CI jobs. Folder names alone do not prevent expensive tests
> from running accidentally.

---

**Next**: [Part 10 — Grow Without Package Ceremony](10_grow_without_package_ceremony.md)
