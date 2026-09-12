# Compose the Runtime at the Process Edge

> **Who this is for**: Engineers wiring settings, database engines, clients, model handles, application actions, and deterministic shutdown.

Moving constructors out of an action is incomplete if concrete dependencies become module globals
spread across routes and workers. A **composition root** is the one ordinary runtime location that
knows which implementations satisfy which application needs.

---

## 1. Import-time construction hides failure and disposal

This module opens a client as a side effect of import:

```python
# Wrong owner: importing the module starts runtime construction.
settings = Settings()
http_client = httpx.AsyncClient(timeout=settings.timeout)
classifier = LLMTicketClassifier(http_client)
```

Tests importing one symbol now require configuration. Reload behavior can duplicate handles.
Shutdown ownership is unclear, and startup failures happen before the process can report readiness
cleanly.

Construct resources explicitly inside `bootstrap/runtime.py`, after configuration is validated and
before the process accepts work.

> **Core:** configuration describes policy, factories construct technology-specific objects, and
> bootstrap decides when construction and disposal occur.

---

## 2. A typed runtime exposes already-composed entry points

This explanatory excerpt shows the shape; the concrete factories belong to their technical owners:

```python
@dataclass(frozen=True)
class Runtime:
    classify_ticket: ClassifyTicket
    session_factory: async_sessionmaker[AsyncSession]


@asynccontextmanager
async def build_runtime(settings: Settings) -> AsyncIterator[Runtime]:
    engine = create_async_engine(settings.database_url)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        model = build_model(
            model_name=settings.classification_model,
            model_provider=settings.model_provider,
        )
        classifier = LLMTicketClassifier(model=model)
        repository = SqlAlchemyTicketRepository(session_factory)
        action = ClassifyTicket(repository, classifier)
        yield Runtime(action, session_factory)
    finally:
        await engine.dispose()
```

The runtime container exposes useful constructed dependencies, not the whole settings object or a
generic dictionary. FastAPI dependencies and worker consumers receive the action; they do not
rebuild it.

If the model handle or HTTP client owns an async close operation, bootstrap closes it too. Disposal
runs in reverse dependency order: stop new intake, await bounded in-flight work, then close the
resources that work uses.

---

## 3. Factories retain technology-specific construction knowledge

Bootstrap coordinates factories; it should not absorb their internals:

```python
# genai/ticket_classification/llm.py
def build_model(*, model_name: str, model_provider: str):
    return init_chat_model(
        model=model_name,
        model_provider=model_provider,
    ).with_structured_output(ClassificationOutput)
```

```python
# bootstrap/runtime.py
model = build_model(
    model_name=settings.classification_model,
    model_provider=settings.model_provider,
)
classifier = LLMTicketClassifier(model=model)
```

The task-level factory knows how to bind structured output. Bootstrap knows which validated values
and lifecycle dependencies to supply. Neither application code nor a module import chooses the
provider.

---

## 4. FastAPI lifespan enters the same runtime

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with build_runtime(load_settings()) as runtime:
        app.state.runtime = runtime
        yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(ticket_router)
    return app
```

`bootstrap/app.py` owns the FastAPI instance, lifespan, router registration, and framework
instrumentation. `api/dependencies.py` may retrieve the already-built action from app state and
present it to routes.

A worker process can enter the same `build_runtime()` context without importing FastAPI. That is
why runtime composition belongs above process adapters rather than inside a route.

---

## 5. Supervisors own loops; actions own work

A long-running worker needs task creation, stop events, health, and graceful shutdown. Put those
mechanics in `bootstrap/supervisor.py`:

```python
async def supervise(consumer: TicketConsumer, stop: asyncio.Event) -> None:
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(consumer.run(stop))
        tasks.create_task(report_health(stop))
```

The supervisor controls process lifetime. The consumer translates deliveries. `ClassifyTicket`
owns business execution. Business stage order does not belong in the supervisor merely because it
runs actions sequentially.

> **Key insight**: bootstrap may know every concrete object while knowing no business decision; its
> job is to create, connect, start, and dispose the graph.

---

## 6. Partial startup needs cleanup before a runtime exists

Suppose the worker creates database engines and an HTTP client, then fails to connect to the
broker. There is no completed runtime for the caller to close. Each successfully acquired resource
must already have a cleanup owner before the next acquisition can fail.

`AsyncExitStack` is a standard-library cleanup registry: callbacks run in reverse registration
order when the stack closes. The sample worker registers cleanup during `_build_resources()`,
then uses `pop_all()` to transfer those callbacks to the runtime without executing them. If
`_compose_runtime()` fails afterward, `build_runtime()` closes the resource bundle instead.

This standalone Python example demonstrates that ownership transfer using recorded events instead
of live clients. Run the whole block as a script; it needs only the standard library:

```python
import asyncio
from contextlib import AsyncExitStack


async def build(events: list[str], *, fail: bool) -> AsyncExitStack:
    async def close(name: str) -> None:
        events.append(f"close {name}")

    async with AsyncExitStack() as pending:
        for name in ("database", "http"):
            events.append(f"open {name}")
            pending.push_async_callback(close, name)
        if fail:
            raise RuntimeError("broker unavailable")
        return pending.pop_all()


async def main() -> None:
    failed: list[str] = []
    try:
        await build(failed, fail=True)
    except RuntimeError:
        pass
    assert failed == ["open database", "open http", "close http", "close database"]

    succeeded: list[str] = []
    runtime_cleanup = await build(succeeded, fail=False)
    assert succeeded == ["open database", "open http"]
    await runtime_cleanup.aclose()
    assert succeeded == failed
    print("startup failure and runtime shutdown both clean up in reverse order")


asyncio.run(main())
```

The printed line is the success signal. The first assertion detects leaked partial startup;
the second detects premature cleanup caused by returning clients without transferring ownership.
This verifies the lifecycle mechanism, not connectivity or graceful worker draining.

Returning a resource bundle makes ownership explicit; it does not make concurrent work stop.
The supervisor must first stop intake and resolve or cancel in-flight tasks within the shutdown
budget, then close the stack. Cancellation and provider-specific close operations still need
bounded handling; see [signals and shutdown](../../fundamentals/core_concepts/signals.md).

### Several supervisors can share resources without owning business stages

In the investigation worker, `AdmissionSupervisor` controls how much work enters, `AimdSupervisor`
adjusts the concurrency target, and `ReconcilerSupervisor` runs periodic checks. **AIMD**, additive
increase and multiplicative decrease, means raising capacity gradually during stable operation
and cutting it proportionally under pressure. Its pure decisions live in `domain/admission.py`;
the supervisor supplies observations and timing.

```text
current target 4; stable window; chosen increase 1 → target 5
current target 4; pressure window; decrease factor 0.5 → target 2
```

These are illustrative policy inputs, not the sample's configured defaults. Changing the target
controls future admission; it does not undo work already running. The investigation's analysis,
write-back, and checkpoint order remains in `application/investigate_exception.py`.

⚠️ A supervisor can be alive while repeatedly failing its useful work. Readiness needs progress
evidence, and shutdown needs tests for a stalled action, not just a successful cleanup callback.
The sample's `tests/unit/bootstrap/test_runtime.py` exercises composition failure; its
`test_supervisor.py` exercises drain timeout and interruption handling. Those are different claims.

---

## 7. Readiness and shutdown prove lifecycle ownership

**Success signal:** startup builds one shared graph, readiness becomes true only after required
dependencies initialize, and shutdown stops intake before closing those dependencies. Tests can
replace constructors and assert creation/disposal without executing business behavior.

⚠️ The first failure is a use-after-close during shutdown: the database engine or HTTP client closes
while a consumer task is still processing. The symptom is a burst of connection or cancellation
errors exactly when the deployment terminates.

Do not centralize short-lived request transactions in the process runtime. Bootstrap owns the
session factory or engine lifetime; the repository or request dependency owns each transaction's
narrower lifetime.

> **Production:** give graceful shutdown a bounded deadline. Once it expires, record unfinished work
> and rely on durable redelivery or reconciliation instead of waiting forever.

---

**Next**: [Part 7 — Apply the Pattern to APIs and Workers](07_apply_the_pattern_to_apis_and_workers.md)
