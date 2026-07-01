# 13 - Testing LLM and Agentic Code

> **Purpose**: Test LLM-powered features without pretending the model is deterministic. Keep the fast suite fast, pin down your contracts, and move probabilistic quality checks into explicit evals.

LLM code has two very different parts:

1. **Normal software**: prompt builders, schema definitions, retries, tool dispatch, quota accounting, parsing, persistence.
2. **Model behavior**: classification quality, reasoning quality, tone, faithfulness, tool choice.

Treat them differently. Unit tests should make the normal software boring. Evals should measure the model behavior.

---

## The Testing Pyramid for LLM Features

| Layer | Runs in fast CI? | What it proves |
|-------|------------------|----------------|
| Prompt construction unit tests | Yes | The right instructions, variables, tools, and schema are sent |
| Parser / schema tests | Yes | Bad model output is rejected, good output is accepted |
| Provider adapter tests | Yes | Your wrapper handles SDK responses, errors, retries, and timeouts |
| Agent workflow tests with fake tools | Yes | Tool routing, state transitions, and side effects are correct |
| Golden/eval dataset runs | No, or scheduled | The prompt/model still performs well on representative examples |
| Live smoke tests | No, or deploy gate | Credentials, model names, provider limits, and network paths work |

The mistake is putting live model calls in every pull request. That makes your suite slow, flaky, expensive, and hard to debug. Put live calls behind an explicit marker.

```python
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "llm_eval: live or expensive LLM evaluation tests",
]
```

Run them intentionally:

```bash
pytest -m "not llm_eval"
RUN_LLM_EVALS=1 pytest -m llm_eval
```

---

## Shape the Code for Testing

Do not let route handlers build prompts, call providers, parse output, mutate the database, and trigger tools all in one function. Split the seam where determinism ends.

```python
# app/ai/tickets.py
from enum import StrEnum
from typing import Protocol
from pydantic import BaseModel, Field


class TicketLabel(StrEnum):
    billing = "billing"
    bug = "bug"
    sales = "sales"
    other = "other"


class TicketClassification(BaseModel):
    label: TicketLabel
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=300)


class ModelClient(Protocol):
    async def classify_ticket(self, messages: list[dict]) -> TicketClassification:
        ...


def build_ticket_prompt(ticket_text: str) -> list[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Classify the support ticket. Return only the structured "
                "classification object."
            ),
        },
        {"role": "user", "content": ticket_text},
    ]


async def classify_ticket(ticket_text: str, model: ModelClient) -> TicketClassification:
    messages = build_ticket_prompt(ticket_text)
    return await model.classify_ticket(messages)
```

Now the unit tests can be honest:

```python
from unittest.mock import AsyncMock

import pytest

from app.ai.tickets import TicketClassification, TicketLabel, build_ticket_prompt, classify_ticket


def test_build_ticket_prompt_includes_user_text():
    messages = build_ticket_prompt("I was charged twice")

    assert messages[0]["role"] == "system"
    assert "Classify" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "I was charged twice"}


@pytest.mark.asyncio
async def test_classify_ticket_calls_model_with_prompt():
    model = AsyncMock()
    model.classify_ticket.return_value = TicketClassification(
        label=TicketLabel.billing,
        confidence=0.91,
        reason="The customer describes a duplicate charge.",
    )

    result = await classify_ticket("I was charged twice", model)

    assert result.label is TicketLabel.billing
    model.classify_ticket.assert_awaited_once()
    sent_messages = model.classify_ticket.await_args.args[0]
    assert sent_messages[-1]["content"] == "I was charged twice"
```

The model is not in this test. That is the point. The contract is: given text, build messages, call the adapter, return a validated object.

---

## Assert Contracts, Not Prose

Bad LLM tests:

```python
assert answer == "This ticket is about billing because the user was charged twice."
```

That fails on harmless wording changes. Prefer assertions on the contract:

```python
assert result.label == TicketLabel.billing
assert 0 <= result.confidence <= 1
assert "charge" in result.reason.lower() or "billing" in result.reason.lower()
```

For structured features, make the model return a schema. OpenAI's Structured Outputs guide emphasizes JSON Schema conformance and SDK helpers for schemas; Anthropic's tool-use docs similarly frame tools as schema-shaped calls. Your application should still validate the returned object with Pydantic because provider guarantees do not replace your domain constraints.

```python
import pytest
from pydantic import ValidationError


def test_classification_rejects_unknown_label():
    with pytest.raises(ValidationError):
        TicketClassification.model_validate(
            {"label": "angry", "confidence": 0.5, "reason": "not an allowed enum"}
        )
```

Keep exact-string assertions for deterministic code you own: prompt templates, error messages, normalized enum values, and SQL. Avoid exact strings for free-form model prose.

---

## Mock the Adapter, Not the SDK Internals

Provider SDKs change response shapes and transport details over time. Put them behind a small adapter and mock that adapter in most tests.

```python
# app/ai/openai_client.py
from openai import AsyncOpenAI

from app.ai.tickets import TicketClassification


class OpenAIModelClient:
    def __init__(self, client: AsyncOpenAI):
        self.client = client

    async def classify_ticket(self, messages: list[dict]) -> TicketClassification:
        response = await self.client.responses.parse(
            model="gpt-5.5",
            input=messages,
            text_format=TicketClassification,
        )
        return response.output_parsed
```

Unit-test application code against `ModelClient`. Adapter tests can patch the SDK call or, if your adapter uses `httpx` directly, use `respx` to mock the HTTP boundary.

```python
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_openai_adapter_parses_response():
    sdk = AsyncMock()
    sdk.responses.parse.return_value.output_parsed = TicketClassification(
        label="bug",
        confidence=0.88,
        reason="The user reports an application crash.",
    )

    adapter = OpenAIModelClient(sdk)
    result = await adapter.classify_ticket([{"role": "user", "content": "app crashes"}])

    assert result.label == "bug"
    sdk.responses.parse.assert_awaited_once()
```

If the SDK has built-in retries, turn them off in the adapter test when you are testing your own retry loop. Otherwise you will accidentally test two retry systems at once.

---

## Test Error Paths Like Normal HTTP Code

The happy path is cheap. The valuable tests are usually provider failures:

- timeout before any response
- rate limit / 429
- provider 500
- malformed structured output
- safety refusal
- partial streaming response
- cancellation by the client

```python
import pytest


class RateLimited(Exception):
    pass


@pytest.mark.asyncio
async def test_classification_surfaces_rate_limit():
    model = AsyncMock()
    model.classify_ticket.side_effect = RateLimited("provider rate limited")

    with pytest.raises(RateLimited):
        await classify_ticket("please help", model)
```

If your application converts provider errors into HTTP responses, test that at the endpoint layer with dependency overrides.

```python
@pytest.mark.asyncio
async def test_endpoint_returns_503_when_model_unavailable(client, app):
    fake_model = AsyncMock()
    fake_model.classify_ticket.side_effect = TimeoutError("model timed out")

    app.dependency_overrides[get_model_client] = lambda: fake_model

    response = await client.post("/tickets/classify", json={"text": "help"})

    assert response.status_code == 503
    assert response.json()["code"] == "MODEL_UNAVAILABLE"
```

---

## Agent Tests: Fake the Tools

Agentic code is just workflow code with a model choosing steps. Test the workflow with tools that are deterministic and observable.

```python
class FakeCRM:
    def __init__(self):
        self.notes: list[tuple[str, str]] = []

    async def add_note(self, customer_id: str, note: str) -> None:
        self.notes.append((customer_id, note))


@pytest.mark.asyncio
async def test_agent_records_billing_note(fake_agent_model):
    crm = FakeCRM()
    agent = SupportAgent(model=fake_agent_model, crm=crm)

    await agent.handle("customer-123", "I was charged twice")

    assert crm.notes == [
        ("customer-123", "Customer reports duplicate charge."),
    ]
```

For a tool-using agent, assert these contracts:

- The tool schema exposes only safe parameters.
- Missing required tool parameters produce a recoverable error.
- The same tool call can be retried idempotently.
- Tool results are summarized without leaking secrets.
- The final response reflects the tool result, not just the model's guess.

Do not let agent tests call real payment APIs, send real email, or mutate real CRM records. Those are integration tests with explicit credentials and cleanup.

---

## Golden Tests and Evals

An eval is a small dataset plus a grader. Use it when the question is "is the behavior good enough?", not "did this function call that function?"

```jsonl
{"input": "I was charged twice", "expected_label": "billing"}
{"input": "The app crashes when I upload a CSV", "expected_label": "bug"}
{"input": "Can I talk to sales?", "expected_label": "sales"}
```

```python
import os

import pytest


pytestmark = pytest.mark.llm_eval


@pytest.mark.skipif(
    os.getenv("RUN_LLM_EVALS") != "1",
    reason="live LLM evals are opt-in",
)
@pytest.mark.asyncio
async def test_ticket_classifier_eval(live_model, eval_cases):
    correct = 0

    for case in eval_cases:
        result = await classify_ticket(case["input"], live_model)
        correct += result.label == case["expected_label"]

    accuracy = correct / len(eval_cases)
    assert accuracy >= 0.90
```

Keep eval datasets boring and representative:

- Real-ish user inputs, scrubbed of secrets and PII.
- Edge cases that previously failed.
- A stable expected outcome per case.
- Enough examples to catch regression, not so many that every local run costs money.

As of July 2026, OpenAI documents its older Evals platform as being deprecated, with read-only and shutdown dates later in 2026. That does not make evals obsolete; it means your eval data should live in your repo or data warehouse, and your runner should be portable across providers and hosted tooling.

---

## Snapshot Tests: Use Carefully

Snapshot tests are tempting for LLM output. They are usually too brittle for prose.

Good snapshot targets:

- Prompt messages your code constructs.
- Tool schemas.
- JSON schemas.
- Normalized structured results.

Bad snapshot targets:

- Full model prose.
- Exact token streams.
- Reasoning text.
- Provider metadata that changes between SDK versions.

```python
def test_ticket_prompt_snapshot(snapshot):
    messages = build_ticket_prompt("The app crashes")
    snapshot.assert_match(messages)
```

If a snapshot changes every time the model changes a synonym, delete it or move that concern into an eval.

---

## Production Checklist

| Concern | Fast test | Eval / live test |
|---------|-----------|------------------|
| Prompt template | Exact messages | Representative prompts |
| Structured output | Pydantic validation | Field accuracy by case |
| Provider errors | Mocked exceptions | One live smoke per provider |
| Tool calls | Fake tools | Sandbox environment |
| Streaming | Fake async generator | One live cancellation test |
| Cost / quotas | Unit-test accounting | Scheduled budget-limited eval |
| Model upgrade | Contract tests | Golden dataset comparison |

Rules of thumb:

1. Test everything deterministic in normal CI.
2. Use schemas to make model output less fuzzy.
3. Put live model calls behind markers and environment flags.
4. Treat eval datasets as product assets, not throwaway tests.
5. Never block every pull request on a probabilistic external service.

---

## References

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [OpenAI Working with evals](https://developers.openai.com/api/docs/guides/evals)
- [OpenAI Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals)
- [Anthropic Evaluation Tool](https://platform.claude.com/docs/en/test-and-evaluate/eval-tool)
- [Python `AsyncMock`](https://docs.python.org/3/library/unittest.mock.html#unittest.mock.AsyncMock)
- [RESPX](https://lundberg.github.io/respx/)

---

## Next

- [09 - Mocking External Services](09_mocking_external.md) - provider and network boundaries.
- [11 - Coverage & CI](11_coverage_and_ci.md) - keep live evals out of the fast suite.
