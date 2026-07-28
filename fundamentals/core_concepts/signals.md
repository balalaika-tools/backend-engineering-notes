# Unix Signals and Graceful Shutdown

> **Who this is for**: Backend developers running Python services, workers, or
> schedulers under Docker, Kubernetes, systemd, or a process supervisor. The
> operational examples focus on Unix-like systems.

A signal is a small asynchronous notification sent to a process. For backend
services, the important problem is not merely "catch `SIGTERM`." It is turning a
shutdown request into a bounded sequence that stops new work, finishes or
abandons active work safely, releases resources, and exits before the platform
uses `SIGKILL`.

---

## 1. How Python Receives a Signal

The operating system delivers a signal to the process. CPython's low-level
handler records that it arrived; the Python handler runs later in the main thread
of the main interpreter, usually at the next bytecode instruction.

```text
OS / supervisor
      │
      │ SIGTERM
      ▼
CPython low-level handler
      │ sets a pending flag
      ▼
main Python thread reaches a safe point
      │
      ▼
registered Python callback runs
```

Consequences:

- only the main thread of the main interpreter can register a Python signal
  handler;
- the Python handler runs in that main thread even if another thread received
  the signal;
- a long-running C extension can delay the Python callback until it returns;
- signals are not an inter-thread messaging API—use `threading` primitives or a
  queue for that;
- handler code should be tiny because it interrupts the main program at an
  arbitrary safe point.

Signals and exceptions are different mechanisms, although Python's default
`SIGINT` handler raises `KeyboardInterrupt`.

---

## 2. Signals a Backend Service Encounters

Use symbolic names such as `signal.SIGTERM`; numeric values and availability
vary by platform.

| Signal | Normal meaning | Catch or ignore? | Typical source |
|--------|----------------|------------------|----------------|
| `SIGTERM` | Request orderly termination | Yes | Docker, Kubernetes, systemd, supervisors |
| `SIGINT` | Interactive interruption | Yes | `Ctrl+C` |
| `SIGHUP` | Terminal hangup; conventionally reload | Yes | Shell/session or supervisor |
| `SIGUSR1`, `SIGUSR2` | Application-defined event | Yes | Operations tooling |
| `SIGKILL` | Terminate immediately | No | Grace period expired or force-kill |
| `SIGSTOP` | Suspend immediately | No | Job control / operator |

Both `SIGKILL` and `SIGSTOP` cannot be caught, blocked, or ignored. `SIGKILL`
provides no cleanup opportunity.

Do not install handlers for synchronous faults such as `SIGSEGV` and expect
Python code to recover. The invalid native operation usually remains invalid
after the handler returns. Use crash reporting and `faulthandler` for diagnosis.

---

## 3. The Grace Period Is a Deadline, Not a Delay

Docker normally sends the image's configured stop signal (`SIGTERM` when none is
configured), waits for its stop timeout, and then sends `SIGKILL`.

Kubernetes starts a Pod's termination grace period, runs a `preStop` hook when
configured, asks the container runtime to stop the container, and force-kills
remaining processes after the deadline. The `preStop` hook consumes the same
grace-period budget; it does not add extra time.

At the same time, service routing is converging away from the terminating Pod.
EndpointSlices mark terminating endpoints not ready, but already-routed
connections and propagation races still exist. The application must tolerate a
short overlap.

```
termination requested
        │
        ├── endpoint begins leaving service routing
        ├── optional preStop work
        └── configured stop signal reaches container process
                         │
                         ▼
               application drains work
                         │
              exits before deadline?
                  │              │
                 yes             no
                  │              │
             clean exit       SIGKILL
```

Set `terminationGracePeriodSeconds` from measured shutdown behavior, including
worst-case request time, queue acknowledgement policy, pool teardown, log
flushing, and the `preStop` duration. A long grace period does not fix work that
has no cancellation or timeout path.

---

## 4. A Safe Handler for a Synchronous Worker

The handler should record intent and return. Let the normal control path perform
logging and cleanup:

```python
import signal

shutdown_requested = False


def request_shutdown(signum: int, frame: object) -> None:
    del signum, frame
    global shutdown_requested
    shutdown_requested = True


signal.signal(signal.SIGTERM, request_shutdown)
signal.signal(signal.SIGINT, request_shutdown)
```

The worker checks the flag at safe checkpoints:

```python
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def run_worker(
    process_one: Callable[[], None],
    close_resources: Callable[[], None],
) -> None:
    while not shutdown_requested:
        # This operation needs a finite poll/read timeout so the loop can notice
        # shutdown. An unbounded blocking call defeats the flag.
        process_one()

    logger.info("shutdown requested; worker is draining")
    close_resources()
```

The worker receives the application operations explicitly. In a queue consumer,
"draining" usually means stop reserving new jobs, then either finish or safely
requeue the current job.

Avoid locks, network calls, file I/O, arbitrary cleanup, and logging inside the
signal callback. Those operations can block or acquire a lock that the
interrupted code already holds.

Calling `sys.exit()` in a Python signal handler raises `SystemExit` in the main
thread. Although stack unwinding can run `finally` blocks, this "out of thin air"
exception can interrupt code at an unsafe point. A flag and explicit checkpoint
is easier to reason about for complex services.

---

## 5. Asyncio: Turn the Signal into an Event

On Unix, `loop.add_signal_handler()` schedules the callback on the event loop:

```python
import asyncio
import signal
from collections.abc import Awaitable, Callable


async def serve(
    run_server_until: Callable[[asyncio.Event], Awaitable[None]],
    close_resources: Callable[[], Awaitable[None]],
) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    for watched_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(watched_signal, stop.set)

    try:
        await run_server_until(stop)
    finally:
        for watched_signal in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(watched_signal)
        await close_resources()

```

An entry point passes the application coroutine functions to `serve()` and then
calls it with `asyncio.run(...)`.

An `Event` is idempotent, so a second signal does not cause the
`Future.set_result()` error found in many minimal examples.

`loop.add_signal_handler()` is Unix-specific and must be called from the main
thread. Cross-platform libraries should expose a shutdown event and let the
platform-specific entry point decide how to set it.

Cancellation still needs a policy. Give tasks time to finish, then cancel
remaining tasks and await them so their `finally` blocks run. Never wait without
a deadline during shutdown.

---

## 6. FastAPI and Uvicorn Own the Signal Handler

Uvicorn already handles termination signals, stops taking new work, finalizes
connections and tasks within configured timeouts, and drives the ASGI lifespan
shutdown phase. Application cleanup belongs in FastAPI's lifespan:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(lifespan=lifespan)
```

Do not replace Uvicorn's `SIGTERM` or `SIGINT` handler from application code.
Doing so can bypass its drain sequence. Configure the server's graceful-shutdown
timeout and the platform grace period so the outer platform deadline is longer
than Uvicorn's internal deadline plus cleanup margin.

The same ownership rule applies under Gunicorn or another supervisor: understand
which process receives signals, which process forwards them, and which lifecycle
hook your application owns.

---

## 7. PID 1 and Signal Forwarding in Containers

The container stop signal goes to the container's main process. Make the Python
server that process with the JSON/exec form:

```dockerfile
# ✅ Uvicorn becomes PID 1 and receives the stop signal directly.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Avoid a shell wrapper that does not `exec` its child:

```dockerfile
# ❌ A shell is PID 1 and may not forward the signal to Uvicorn.
CMD uvicorn app.main:app --host 0.0.0.0 --port 8000
```

If an entrypoint script is necessary, end it with `exec "$@"`. If the container
spawns child processes, use a real init process—such as Docker's `--init`
option—or ensure the supervisor forwards signals and reaps children.

PID 1 has special signal behavior on Linux, so relying on default termination
behavior is fragile. A framework or explicit application handler should own the
shutdown sequence.

---

## 8. Design Shutdown in Reverse Dependency Order

A practical service sequence is:

1. mark the instance unready or stop accepting new work;
2. stop polling queues and scheduling new jobs;
3. wait for active requests/jobs up to a deadline;
4. cancel, requeue, or abandon remaining work according to its durability
   contract;
5. flush telemetry and stop logging listener threads;
6. close HTTP clients, database pools, brokers, and other resources;
7. exit before the platform deadline.

Close dependants before their dependencies. A background telemetry exporter may
need its HTTP client, and a transaction may need its database pool, so shutting
the pool first can make later cleanup fail.

Make each cleanup step:

- **bounded**—it has a timeout;
- **idempotent**—a second call is harmless;
- **observable**—start, completion, timeout, and forced abandonment are visible;
- **failure-tolerant**—one failed closer does not skip every remaining closer.

`SIGKILL`, node loss, and power loss still bypass graceful shutdown. Durable
systems therefore also need acknowledgements, transactions, idempotency, leases,
or retryable work. Graceful shutdown improves normal deploys; it is not a
durability guarantee.

---

## 9. Test the Real Process Lifecycle

Unit-testing the callback is not enough. Start the service as a subprocess,
create in-flight work, send `SIGTERM`, and verify:

- readiness changes or new work stops;
- accepted work follows the documented finish/requeue policy;
- lifespan and resource cleanup run;
- the process exits inside the grace budget;
- final logs arrive;
- the next process can recover any unfinished durable work.

A second test should deliberately exceed the grace period and verify that the
system remains correct after force-kill. That is where missing acknowledgements
and non-idempotent jobs appear.

> **Mental model**: the signal is only the trigger. Graceful shutdown is a
> deadline-driven application protocol, and abrupt death must still be safe.

---

## 10. Official References

- [Python `signal` module](https://docs.python.org/3/library/signal.html)
- [Kubernetes Pod termination flow](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination-flow)
- [Docker container stop](https://docs.docker.com/reference/cli/docker/container/stop/)
- [Uvicorn graceful process shutdown](https://www.uvicorn.org/server-behavior/#graceful-process-shutdown)

---

**Next**: [Concurrency and Parallelism](../concurrency/README.md)
