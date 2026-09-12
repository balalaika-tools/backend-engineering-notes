# Treat GenAI as an External Capability

> **Who this is for**: Engineers adding structured model calls, agents, tools, retrieval, or LangGraph workflows without turning AI code into the business layer.

A large language model (LLM) is remote, nondeterministic, expensive, and provider-controlled. It is
therefore an unusually strong candidate for a port. The application should receive a typed business
candidate—not model messages, prompt templates, graph state, or raw provider JSON.

---

## 1. A model call is not the classification use case

The application action may load a ticket, enforce eligibility, request a classification candidate,
interpret confidence, persist the decision, and publish the next outcome. The GenAI implementation
may build provider input, invoke the configured handle, validate structured output, and translate
provider failures.

```text
application/classify_ticket.py
    owns eligibility → request candidate → interpret → persist/handoff

genai/ticket_classification/classifier.py
    owns prompt input → invoke model → validate provider output → port result
```

If `genai/` decides whether a ticket is eligible or where low-confidence work goes, provider
mechanics have absorbed business policy. If `application/` constructs prompts or catches SDK
exceptions, the dependency points outward.

> **Core:** every LLM, prompt, agent, AI schema, tool, graph, model binding, and AI middleware lives
> under root `genai/`; business execution remains under `application/`.

---

## 2. A small structured-output task has four owners

```text
genai/
└── ticket_classification/
    ├── llm.py          # Construct and bind the model
    ├── schemas.py      # Validate provider-facing structured output
    ├── prompts.py      # Own prompt text and version, when nontrivial
    └── classifier.py   # Implement TicketClassifier and translate failures
```

Create only responsibilities that exist, but every task keeps model construction in `llm.py`.
Do not name that module `models.py`, which reads as business or persistence entities.

```python
# llm.py
def build_model(*, model_name: str, model_provider: str):
    return init_chat_model(
        model=model_name,
        model_provider=model_provider,
    ).with_structured_output(ClassificationOutput)
```

```python
# classifier.py
class LLMTicketClassifier:
    def __init__(self, model: Runnable) -> None:
        self._model = model

    async def classify(self, body: str) -> ClassificationCandidate:
        output = await self._model.ainvoke(build_prompt(body))
        return ClassificationCandidate(output.category, output.confidence)
```

Bootstrap calls the factory and injects the configured handle into the capability implementation.
No model, agent, checkpointer, or Model Context Protocol (MCP) client is constructed at module import.

---

## 3. Agent factories assemble harnesses but do not invoke them

An agent task adds `agent.py`:

```text
genai/pricing_agent/
├── llm.py
├── schemas.py
├── prompts.py
├── tools.py
├── agent.py
└── pricer.py
```

`agent.py` accepts constructed models and explicit tools, then returns the harness. `pricer.py`
implements the application-facing `TicketPricer` capability by invoking the harness and
translating its result.

A custom `graph/` package is justified only when the service defines graph state, nodes, routing,
or edges. Using an agent library that internally has a graph does not create a project-owned graph
responsibility.

---

## 4. Tools cross trust boundaries and need narrow authority

An AI tool is an adapter over a port or public application action. It validates typed input, applies
authorization from trusted application context, calls bounded behavior, and returns a safe result.

```python
async def get_ticket(ticket_id: str, context: ApplicationContext) -> TicketView:
    context.require_tenant_access()
    return await ticket_reader.get_for_tenant(
        ticket_id=ticket_id,
        tenant_id=context.tenant_id,
    )
```

The agent must not invent `tenant_id` from prompt text. Tool discovery must not silently broaden
permissions. A tool should not query a database directly if doing so bypasses application
authorization or auditing.

Prompt injection is the attack: untrusted ticket text tells the model to retrieve another tenant's
ticket; an overpowered tool obeys. The defense is server-supplied identity, authorization inside the
tool boundary, and a capability narrow enough that model text cannot choose wider authority.

---

## 5. Retrieval and prompts follow semantic ownership

Keep retrieval local to one AI capability until another genuinely reuses the same retrieval,
reranking, and context semantics. Application-owned ingestion and index-refresh actions remain in
`application/`; vector-index contracts remain in `ports/`; concrete persistence belongs in
`db/` or the appropriate adapter.

Prompts are versioned implementation details of their AI task. Persist or emit the version used
when reproducibility matters. Similar wording is not enough to justify `genai/shared/prompts/`;
promote only demonstrated shared semantics.

> **Key insight**: GenAI belongs outside the application not because it is unimportant, but because
> its nondeterminism, trust boundary, cost, and provider mechanics must not define business policy.

---

## 6. Context-dependent agents separate reusable configuration from one run

An investigation's valid reason codes and manuals can change after deployment. A single agent
constructed at startup can retain an obsolete output schema; rebuilding everything for every
message wastes work. The sample separates a **control context**, an immutable snapshot of manuals,
valid codes, and schema names, from the agent machinery built for that snapshot.

First follow one call with illustrative values:

```text
ResolveControlContext → context generation 7, allowed reason codes [R1, R2]
InvestigateException → analyst.analyze(input, context=context)
ContextualLLMExceptionAnalyst → build or reuse a harness for this context
LLMExceptionAnalyst → invoke harness, validate output, return ExceptionAnalysis
InvestigateException → interpret and checkpoint the result
```

A **harness** is the wrapper that assembles and executes the model, tools, middleware, and output
handling. `agent.py` assembles the agent; `harness.py` adapts its execution interface;
`analyst.py` presents the application-facing capability. These modules represent distinct work in
this sample. A simple structured model call needs fewer modules.

### Two caches avoid different kinds of repeated work

`ResolveControlContext` uses a shared Redis cache keyed by tenant scope, deployment release, and
configuration **generation**, a number advanced when configuration is reset. It coordinates a
rebuild through a temporary exclusive lease so concurrent workers do not all fetch and summarize
the same configuration. Summarized manuals have their own object-store keys that include the
configuration digest and prompt version.

`ContextualLLMExceptionAnalyst` separately caches constructed analysts inside one process. Its key
includes the generation, manual digests, code vocabularies, and schema names. When the generation
changes, it clears that local cache before selecting the analyst.

```text
call A: generation 7, context K → construct analyst A7
call B: generation 7, context K → reuse A7; execute a new analysis
call C: generation 8, context K → clear local cache; construct A8
```

Reusing an analyst does not mean reusing its answer. The sample creates a fresh tool-call budget
inside each `analyze()` invocation. The reusable harness must likewise avoid leaking one
investigation's conversation or tool state into another concurrent run.

For the shared context, a reset can require refetching configuration while unchanged manual
digests still permit reuse of stored summaries. “Cache invalidated” therefore does not imply
“every investigation pays for a new summary.” The sample tests these separately in
`tests/unit/application/test_resolve_control_context.py` and
`tests/unit/genai/exception_analysis/test_contextual_analyst.py`.

### Schema validation and application decisions remain different steps

If the active vocabulary contains R1 and R2 but the model returns R9, the provider-facing schema
and subsequent domain validation reject that result. `LLMExceptionAnalyst` converts accepted
`AnalysisOutput` into `ExceptionAnalysis`; its caller never needs to inspect model messages.
An exhausted transient provider failure becomes `AnalysisUnavailableError`, which the application
classifies for investigation retry. Invalid analysis is a different outcome from unavailability.

⚠️ A cache key that omits a changing input can preserve an old prompt or schema without raising
an error. The tell is output validated against yesterday's vocabulary after a configuration
change. Test equal contexts for reuse and changed contexts for reconstruction; do not infer cache
correctness from reduced model latency.

**Success signal:** the action sees the same typed capability regardless of how the harness is
built; changing the configuration generation replaces cached construction; each analysis still
gets fresh invocation state. These are architectural and behavioral checks, not a guarantee of
answer quality, which requires [LLM testing and evaluation](../../operations/testing/13_testing_llm_code.md).

Do not introduce generation caches and harness wrappers for a fixed prompt with one small model
call. Add them when configuration changes and repeated construction create a measurable need.

---

## 7. Test the seam before paying for a live call

Test prompt assembly, schema rejection, factories, capability invocation with a fake model handle,
failure translation, graph routing, and authorization propagation separately. Ordinary unit tests
make no live model call and require no provider credentials.

**Success signal:** `ClassifyTicket` can be tested with a six-line fake `TicketClassifier`, while
`LLMTicketClassifier` can be tested with a fake model handle returning structured output. Changing
model provider edits `genai/`, configuration, and bootstrap—not application policy.

⚠️ The first failure is raw provider output crossing the port. The symptom is application code
reading message content, tool-call arrays, or provider refusal fields. Translate them into stable
typed outcomes inside `genai/`.

Do not create a GenAI abstraction when the product is intentionally a thin provider-specific client
library with no independent business action. In a deployable business service, however, even one
small model call belongs under the explicit `genai/` boundary.

> **Production:** add timeouts, bounded attempts, token/cost limits, safe telemetry, refusal
> handling, prompt versioning, and optional live tests according to the failure each prevents.
> Never log secrets or sensitive prompts by default.

---

**Next**: [Part 9 — Test Through Architectural Boundaries](09_test_through_architectural_boundaries.md)
