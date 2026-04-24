# Unix Signals

## What is a Signal?

A signal is a notification the OS sends to a running process. It's an asynchronous interrupt — the process doesn't ask for it, and it can arrive at any time between any two instructions.

Signals are not exceptions. They don't travel through your call stack. The OS delivers them directly to the process, which can either:
- **Handle** them — run a custom function (signal handler)
- **Ignore** them — do nothing
- **Default** — let the OS take the default action (usually terminate)

---

## Signals a Backend Developer Encounters

| Signal | Number | Default action | Catchable? | When you see it |
|--------|--------|----------------|------------|-----------------|
| `SIGTERM` | 15 | Terminate | Yes | Container/systemd graceful shutdown |
| `SIGKILL` | 9 | Terminate | **No** | Force-kill after grace period |
| `SIGINT` | 2 | Terminate | Yes | `Ctrl+C` in terminal |
| `SIGHUP` | 1 | Terminate | Yes | Terminal closed; also used to trigger config reload |
| `SIGUSR1/2` | 10/12 | Terminate | Yes | App-defined custom events |

`SIGKILL` is the only signal that cannot be caught or ignored — the kernel handles it directly, your process gets no chance to clean up.

---

## SIGTERM — The Graceful Shutdown Signal

`SIGTERM` is the standard way to ask a process to shut down cleanly. It's the signal sent by:

- **Kubernetes** — when a Pod is deleted or a rolling deploy replaces it
- **Docker** — `docker stop` (sends SIGTERM, waits `--time` seconds, then SIGKILL)
- **systemd** — `systemctl stop`
- **Process supervisors** — gunicorn, uvicorn, supervisord

The sequence in Kubernetes:

```
1. Pod enters Terminating state
2. SIGTERM sent to PID 1 in the container
3. K8s waits terminationGracePeriodSeconds (default: 30s)
4. If still alive → SIGKILL
```

If you don't handle `SIGTERM`, Python's default is to terminate immediately — in-flight requests are dropped, buffers may not be flushed, database connections are not closed cleanly.

---

## Handling Signals in Python

```python
import signal
import sys

def handle_sigterm(signum, frame):
    # clean up, flush, close connections...
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)
```

`signal.signal(signalnum, handler)` registers a callable that receives:
- `signum` — the signal number (e.g. `15` for SIGTERM)
- `frame` — the current stack frame (rarely needed)

Calling `sys.exit(0)` from a signal handler raises `SystemExit`, which lets `finally` blocks and context managers run — so resources get cleaned up properly.

### Handling multiple signals the same way

```python
for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, handle_sigterm)
```

---

## Graceful Shutdown Pattern

The standard pattern for a backend service:

```python
import signal
import sys
import logging

log = logging.getLogger(__name__)
_shutting_down = False

def _on_shutdown(signum, frame):
    global _shutting_down
    log.info("shutdown signal received", extra={"signal": signum})
    _shutting_down = True

signal.signal(signal.SIGTERM, _on_shutdown)
signal.signal(signal.SIGINT, _on_shutdown)
```

In your request loop or worker:

```python
while not _shutting_down:
    process_next_item()

# after loop: drain, flush, close
cleanup()
sys.exit(0)
```

The flag approach is safer than calling `sys.exit()` directly from the handler — it lets your main loop reach a known-good checkpoint before exiting.

---

## Signals in Async Code

Signal handlers run in the main thread. In async code, registering them via `signal.signal()` still works but you can also use asyncio's cleaner API:

```python
import asyncio
import signal

async def main():
    loop = asyncio.get_running_loop()

    stop = loop.create_future()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set_result, sig)

    sig = await stop  # suspend until a signal arrives
    print(f"shutting down on signal {sig}")
    # cancel tasks, close connections, etc.

asyncio.run(main())
```

`loop.add_signal_handler()` is asyncio-aware: the callback is scheduled on the event loop rather than interrupting it mid-coroutine, which avoids subtle race conditions.

---

## FastAPI / Uvicorn Graceful Shutdown

Uvicorn handles `SIGTERM` and `SIGINT` internally — it stops accepting new connections and waits for in-flight requests to finish before exiting. You hook into this via FastAPI's lifespan:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    yield
    # shutdown — runs on SIGTERM/SIGINT before uvicorn exits
    await close_db_pool()
    await close_redis()

app = FastAPI(lifespan=lifespan)
```

The `yield` splits startup (before) from shutdown (after). Uvicorn calls the shutdown side when it receives a termination signal, giving you the grace period to close resources cleanly.

> Don't register your own `SIGTERM` handler when running under uvicorn — you'll override its handler and break graceful shutdown. Use `lifespan` instead.

---

## Connection to Logging and Buffering

See [03_handlers_and_formatters.md](logging/03_handlers_and_formatters.md) — the `PYTHONUNBUFFERED=1` requirement exists for the same reason graceful shutdown matters: if your process is killed before stdout is flushed, the last log lines are lost. Handling `SIGTERM` gives you time to flush; `PYTHONUNBUFFERED=1` ensures you never need to.

---

## Key Rules

- Always handle `SIGTERM` in any long-running service
- Never register your own `SIGTERM` handler when running under uvicorn/gunicorn — use `lifespan`
- Use a flag + loop check rather than `sys.exit()` directly from the handler in complex apps
- `SIGKILL` cannot be caught — make sure you finish within the grace period
- Set `terminationGracePeriodSeconds` in your K8s manifest to match how long your service realistically needs to drain
