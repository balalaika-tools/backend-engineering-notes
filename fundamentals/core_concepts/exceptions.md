# Python Exceptions: Propagation, Recovery, and Translation

> **Who this is for**: Python developers who know `try` and `except` syntax but
> are unsure where errors go, what to catch, or how application layers should
> communicate failure. The examples target Python 3.11+.

An exception is both an error value and a request to leave the current execution
path. Good exception handling preserves that signal until some layer can make a
real decision: recover, translate, retry, report, or terminate.

---

## 1. Follow the Exception Up the Call Stack

When a function raises, its remaining statements do not run. Python walks back
through the active function calls looking for a matching `except` block:

```python
def parse_quantity(raw: str) -> int:
    quantity = int(raw)  # ValueError starts here for "many"
    return quantity


def build_order(raw_quantity: str) -> dict[str, int]:
    quantity = parse_quantity(raw_quantity)
    return {"quantity": quantity}


def handle_request(raw_quantity: str) -> dict[str, object]:
    try:
        return build_order(raw_quantity)
    except ValueError as exc:
        return {"status": 400, "error": str(exc)}


assert handle_request("3") == {"quantity": 3}
assert handle_request("many")["status"] == 400
```

For `"many"`, control moves like this:

```
handle_request()
    └── build_order()
            └── parse_quantity()
                    └── int("many") raises ValueError
                           │
                           ├── no handler in parse_quantity
                           ├── no handler in build_order
                           └── matching handler in handle_request
```

The frames with no handler are **unwound**. Local variables in those calls go out
of scope, and normal execution does not resume after the failed call.

If no matching handler exists, Python reports the exception and traceback at the
thread or task boundary. For a main program, that normally ends the process.

> **Key insight**: raising starts propagation; catching stops it. If a catch block
> neither re-raises nor returns an explicit failure, its caller sees normal
> completion.

---

## 2. Know What `except Exception` Does Not Catch

Python's exception classes form a hierarchy:

```text
BaseException
├── SystemExit
├── KeyboardInterrupt
├── GeneratorExit
└── Exception
    ├── ValueError
    ├── TypeError
    ├── LookupError
    │   ├── KeyError
    │   └── IndexError
    ├── OSError
    │   ├── FileNotFoundError
    │   ├── PermissionError
    │   └── TimeoutError
    └── application-specific exceptions
```

Catch `Exception`, not `BaseException`, for an ordinary broad application
boundary:

```python
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


def run_message(process_message: Callable[[], None]) -> None:
    try:
        process_message()
    except Exception:
        logger.exception("message processing failed")
```

`except Exception` intentionally leaves interpreter-exit signals such as
`KeyboardInterrupt` and `SystemExit` alone. Catching `BaseException` can make a
service refuse to stop.

`asyncio.CancelledError` also derives from `BaseException` in supported Python
versions. An ordinary `except Exception` does not suppress task cancellation.
If async code catches cancellation for cleanup, it should normally re-raise it.

> **Rule**: catch `BaseException` only for narrow cleanup logic that immediately
> re-raises. Application recovery policy belongs under `Exception`.

---

## 3. Give Each `try` Clause One Job

The four clauses have distinct meanings:

```python
import json
from typing import Any


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in payload.items()}


def decode_payload(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("payload is not valid JSON") from exc
    else:
        if not isinstance(parsed, dict):
            raise TypeError("payload must be a JSON object")
        return normalize_payload(parsed)
    finally:
        # Cleanup or bookkeeping only. This runs on both paths.
        pass
```

| Clause | Runs when | Use it for |
|--------|-----------|------------|
| `try` | Always | The smallest operation expected to fail |
| `except` | A matching exception leaves `try` | Recovery or translation |
| `else` | `try` completed without an exception | Work that should not be caught by those handlers |
| `finally` | The statement is leaving normally or exceptionally | Cleanup that must happen |

Keeping the `try` body narrow prevents accidental catches:

```python
# ❌ A ValueError from normalize_payload() is misreported as invalid JSON.
def decode_payload_badly(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw)
        return normalize_payload(parsed)
    except ValueError:
        raise ValueError("payload is not valid JSON")
```

`finally` runs during normal exits, exceptions, `return`, `break`, and
`continue`. It cannot run after an uncatchable process termination such as
`SIGKILL`, `os._exit()`, a power loss, or an interpreter crash.

Never `return`, `break`, `continue`, or raise an unrelated error from `finally`
unless overriding the pending result or exception is deliberate:

```python
def hides_failure() -> str:
    try:
        raise RuntimeError("database write failed")
    finally:
        return "ok"  # ❌ suppresses the RuntimeError
```

For standard resource lifecycles, prefer a
[context manager](context_managers.md) to hand-written `finally`.

---

## 4. Catch Only When You Can Decide Something

A useful handler does at least one of these:

- recovers with a valid fallback;
- retries an operation known to be safe to retry;
- translates an implementation error into the current layer's vocabulary;
- adds policy at a system boundary;
- performs cleanup and re-raises.

Catch the narrowest exception that represents the failure you expect:

```python
from pathlib import Path


def load_optional_banner(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Welcome"  # Missing optional content has a valid fallback.
```

Do not convert unrelated failures into the same fallback:

```python
def load_optional_banner(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "Welcome"  # ❌ Also hides permission and I/O failures.
```

Order handlers from specific to broad because Python chooses the first matching
clause:

```python
def load_banner(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "default"
    except PermissionError:
        raise
    except OSError as exc:
        raise RuntimeError("could not read banner") from exc
```

---

## 5. EAFP and LBYL Are Decisions, Not Religions

Python often favors **EAFP**: attempt the operation and catch the exact failure.
This avoids duplicating the operation's own validation and prevents
check-then-act races.

```python
def parse_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("port must be an integer") from exc

    if not 1 <= port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    return port
```

Pre-checking with `str.isdigit()` would reject `"+8000"` even though `int()`
accepts it, and it accepts some Unicode characters that `int()` cannot parse.
Let the actual parser define valid input.

Use **LBYL**—check first—when a cheap, exact query expresses the desired branch:

```python
DEFAULT_TIMEOUT = 5.0
config = {"retries": 3}

timeout = config.get("timeout", DEFAULT_TIMEOUT)
```

This is clearer than catching `KeyError` for a normal optional lookup.

The deciding question is not "are exceptions allowed as control flow?" They
are control flow. Ask whether failure is the clearest and race-safe way to learn
the answer.

---

## 6. Choose the Correct Form of `raise`

### Re-raise the active exception with bare `raise`

```python
class InventoryError(Exception):
    """Inventory could not be updated."""


def update_with_audit(update_inventory, record_failed_attempt) -> None:
    try:
        update_inventory()
    except InventoryError:
        record_failed_attempt()
        raise
```

Bare `raise` continues the active exception with its traceback intact.

Avoid this form:

```python
class InventoryError(Exception):
    """Inventory could not be updated."""


def update_badly(update_inventory) -> None:
    try:
        update_inventory()
    except InventoryError as exc:
        raise exc  # ❌ Adds this re-raising location to the traceback.
```

`raise exc` raises the same object, but it changes the visible traceback path.
Use it only if intentionally raising a stored exception outside its original
handler; use bare `raise` inside the handler.

### Create an exception when this layer detects the problem

```python
def validate_batch_size(batch_size: int) -> None:
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
```

### Translate with explicit chaining

```python
import json
from typing import Any


class ConfigurationError(Exception):
    """Application configuration is missing or malformed."""


def load_configuration(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc

    if not isinstance(value, dict):
        raise ConfigurationError("configuration root must be an object")
    return value
```

`raise ConfigurationError(...) from exc` expresses:

```
JSONDecodeError          ConfigurationError
implementation detail ──translated by current layer──► public vocabulary
        └──────────── retained as __cause__ ────────────┘
```

The traceback shows both errors and states that the second was the direct cause
of the first.

Use `from None` only when the lower-level context is noisy, expected, and adds no
diagnostic value:

```python
try:
    color = payload["color"]
except KeyError:
    raise ValueError("color is required") from None
```

Suppressing context removes useful evidence, so translation with `from exc` is
the safer default.

---

## 7. Design a Small Domain Exception Hierarchy

Application exceptions let callers depend on your vocabulary instead of a
database driver, HTTP client, or parser:

```python
class OrderError(Exception):
    """Base class for failures the order service intentionally exposes."""


class OrderNotFoundError(OrderError):
    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        super().__init__(f"order {order_id!r} was not found")


class OrderConflictError(OrderError):
    """The requested transition conflicts with current order state."""
```

Consumers can choose their precision:

```python
def load_order_response(service, order_id: str):
    try:
        order = service.load(order_id)
    except OrderNotFoundError:
        return not_found_response(order_id)
    except OrderError:
        return service_error_response()
    else:
        return order
```

Useful design rules:

- inherit application failures from `Exception`, not `BaseException`;
- define one base class per meaningful public boundary, not per function;
- store useful machine-readable attributes such as `order_id`;
- keep messages safe for logs—do not embed passwords, tokens, or full payloads;
- do not mirror every third-party exception one-for-one.

An exception class is part of an API. Renaming it, changing when it is raised, or
removing its attributes can break callers.

---

## 8. Low Layers Describe; Boundaries Decide Policy

Lower layers should normally raise or translate. Boundaries decide what the
process does next.

| Layer | Typical responsibility |
|-------|------------------------|
| Parser / repository / client | Raise a precise implementation error |
| Domain service | Translate to domain vocabulary when useful |
| HTTP endpoint | Map expected domain errors to safe HTTP responses |
| Worker loop | Mark failed work, retry/dead-letter if policy allows, then continue or stop |
| CLI entry point | Print one useful error and choose an exit code |

A worker boundary may catch broadly because it must keep one failed job from
terminating the whole worker:

```python
import logging
from collections.abc import Callable, Iterable
from typing import TypeVar

logger = logging.getLogger(__name__)
JobT = TypeVar("JobT")


def run_jobs(jobs: Iterable[JobT], process: Callable[[JobT], None]) -> None:
    for job in jobs:
        try:
            process(job)
        except Exception:
            logger.exception(
                "job failed",
                extra={"job_type": type(job).__name__},
            )
            # Real systems would mark the job failed or dead-letter it here.
```

That broad catch enforces a visible policy. The same catch deep inside
`process()` would hide which operations actually succeeded.

Log an exception where it is finally handled. Logging and immediately re-raising
at every layer produces duplicate stack traces:

```python
# ❌ This layer adds no recovery, translation, or useful context.
def save_with_duplicate_log(repository, order) -> None:
    try:
        repository.save(order)
    except Exception:
        logger.exception("save failed")
        raise
```

If a layer only needs to add context, translate with `raise ... from exc`. Let
the final boundary log the complete chain once.

---

## 9. Cleanup Does Not Mean Recovery

Cleanup and error handling are separate decisions:

```python
connection = pool.acquire()
try:
    write_order(connection)
except BaseException:
    connection.rollback()
    raise  # Cleanup happened; the operation still failed.
else:
    connection.commit()
finally:
    pool.release(connection)
```

A transaction context manager usually expresses the same lifecycle more safely:

```python
with session.begin():
    session.add(order)
```

If the body raises, the transaction manager rolls back and the exception
continues. On success, it commits. See
[Context Managers](context_managers.md) for the protocol behind this behavior.

Avoid retrying merely because an exception exists. A safe retry requires:

- a transient, identified failure;
- an idempotent operation or an idempotency mechanism;
- a finite attempt limit;
- backoff and usually jitter;
- observability for exhausted attempts.

Validation errors, programming errors, authorization failures, and permanent
conflicts should not be retried.

---

## 10. Concurrent Failures Use `ExceptionGroup`

Concurrent tasks can fail before their siblings are cancelled. Python 3.11+
represents multiple failures in an `ExceptionGroup`; `except*` selects matching
subgroups.

```python
import asyncio


async def fail_with(error: Exception) -> None:
    await asyncio.sleep(0)
    raise error


async def run_batch() -> None:
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(fail_with(ValueError("bad customer id")))
            group.create_task(fail_with(OSError("upstream unavailable")))
    except* ValueError as errors:
        for error in errors.exceptions:
            print(f"invalid input: {error}")
    except* OSError as errors:
        for error in errors.exceptions:
            print(f"infrastructure failure: {error}")


asyncio.run(run_batch())
```

Unlike ordinary `except`, multiple `except*` clauses can each handle a matching
part of the same group. Any unmatched subgroup continues to propagate.

Do not flatten a group to one string and discard its members. Each child
exception carries its own type, traceback, and cause.

For task cancellation, timeouts, and `TaskGroup` lifecycle, continue to
[Asyncio](../concurrency/async/README.md).

---

## 11. Common Failure Patterns

### Silent swallowing

```python
try:
    publish_event()
except Exception:
    pass  # ❌ Caller proceeds as though the event was published.
```

If failure is genuinely optional, catch the exact exception and expose the
fallback through a metric, status, or appropriately leveled log.

### Returning a sentinel that callers forget to check

```python
def load_user(user_id: str):
    try:
        return database.load(user_id)
    except DatabaseError:
        return None  # ❌ "not found" and "database unavailable" now look identical.
```

Use `None` for an expected absence only. Preserve operational failures as
exceptions.

### Catching a broad type around too much code

```python
def handle_request_badly():
    try:
        payload = parse_request()
        order = create_order(payload)
        send_receipt(order)
    except ValueError:
        return bad_request()
    return order
```

A `ValueError` bug in `send_receipt()` now looks like invalid input. Narrow the
`try` block or translate errors at the layer that understands them.

### Exposing internal exception text to clients

Database errors, file paths, hostnames, and query fragments can leak through
`str(exc)`. Log the internal exception at the boundary, but return a stable,
sanitized public error shape.

### Using `assert` for runtime validation

Python can remove `assert` statements when optimization is enabled. Use an
explicit exception for user input, configuration, permissions, or any condition
the deployed application must enforce.

---

## 12. Test the Failure Contract

Tests should prove the exception type, useful attributes, and causal chain—not
only the message:

```python
import pytest


def test_invalid_configuration_preserves_cause() -> None:
    with pytest.raises(ConfigurationError) as captured:
        load_configuration("{not-json}")

    assert "line 1" in str(captured.value)
    assert isinstance(captured.value.__cause__, json.JSONDecodeError)
```

Also test what must **not** be caught: a repository timeout should not become
`OrderNotFoundError`, and cancellation should not become a successful return.

> **Mental model**:
>
> - Raise a precise error where failure is detected.
> - Let it propagate until a layer can make a real decision.
> - Translate when crossing abstraction boundaries, using `raise ... from exc`.
> - Catch broadly only at boundaries that enforce visible policy.
> - Clean up without accidentally converting failure into success.
> - Log once, where the exception is finally handled.

---

**Next**: [Logging — making outcomes observable](logging/README.md)
