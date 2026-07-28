# Logging Basics: From a Call to an Observable Event

> **Who this is for**: Python developers who have used `print()` and want a
> reliable mental model for application logging before learning logger hierarchy,
> handlers, or structured logging.

Logging records facts about a running system. Unlike `print()`, a log call
creates a record with a level, logger name, timestamp, call site, message, and
optional exception/context. Configuration decides which records survive and
where they go.

---

## 1. The Smallest Useful Setup

Configure logging once in the application entry point, before application code
starts emitting records:

```python
import logging


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("application started")


if __name__ == "__main__":
    main()
```

`basicConfig()` attaches a `StreamHandler` to the root logger and uses
`sys.stderr` unless `stream=` or `filename=` is supplied.

The call flows through four concepts:

```text
logger.info(...)
      │
      ▼
Logger decides whether the level is enabled
      │
      ▼
LogRecord stores message, level, logger name, call site, context
      │
      ▼
Handler sends it to a destination
      │
      ▼
Formatter turns it into text
```

The next two notes expand this pipeline. For now, remember that application code
creates records; startup configuration controls them.

`basicConfig()` only configures the root logger when it has no handlers. Later
calls are normally no-ops. `force=True` removes and closes existing root handlers
first, which is useful in some tests or owned entry points but can disrupt
framework or library logging.

---

## 2. Use One Logger per Module

Every application module should create a logger from its import name:

```python
# app/orders/service.py
import logging

logger = logging.getLogger(__name__)


def create_order(order_id: str) -> None:
    logger.info("creating order %s", order_id)
```

If the module is imported as `app.orders.service`, its logger has that name.
Dotted names form a hierarchy, so startup can configure:

- every module through the root logger;
- the `app` package as a group;
- a noisy dependency such as `httpcore` separately.

Do not call `basicConfig()` in each module. A module declares its logger; the
application entry point owns destinations and levels.

---

## 3. Let Logging Perform Message Formatting

Pass values as arguments:

```python
logger.debug(
    "cache lookup key=%s hit=%s duration_ms=%.2f",
    cache_key,
    hit,
    duration_ms,
)
```

The logging package stores the template and arguments and formats them only if a
handler emits the record.

```python
# ❌ The f-string and expensive_summary() run even when DEBUG is disabled.
logger.debug(f"state={expensive_summary(state)}")

# ✅ Defer ordinary string interpolation until the record is emitted.
logger.debug("state=%s", state)
```

Lazy formatting does not defer function calls. If producing a debug value is
expensive, guard the calculation:

```python
if logger.isEnabledFor(logging.DEBUG):
    logger.debug("state=%s", expensive_summary(state))
```

Use stable event wording. Put variable data in arguments or structured fields so
operators can group related records.

---

## 4. Choose Levels by Required Action

| Level | Meaning | Example |
|-------|---------|---------|
| `DEBUG` | Diagnostic detail disabled in normal production operation | Cache key choice, parsed protocol frame |
| `INFO` | Expected lifecycle or business milestone | Service started, job completed |
| `WARNING` | Unexpected condition handled without failing the operation | Fallback used, retry scheduled |
| `ERROR` | Current operation failed | Request dependency failed, job exhausted retries |
| `CRITICAL` | Process or service may be unable to continue safely | Corrupt required state, no writable storage |

A logger level is a minimum threshold. At `INFO`, `DEBUG` is rejected while
`INFO` and higher continue toward handlers.

Levels do not decide process behavior. `logger.critical()` does not exit, and
`logger.error()` does not raise. Code implements policy; logging reports it.

Avoid logging every handled condition as an error. A client sending invalid
input may be an ordinary `INFO` or `WARNING` event, while a database outage that
breaks a valid request is an operational `ERROR`.

---

## 5. Log Exceptions Where They Are Handled

Inside an `except` block, `logger.exception()` records the active exception and
traceback at `ERROR`:

```python
import json
import logging

logger = logging.getLogger(__name__)


def load_document(raw: str) -> dict[str, object]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        logger.exception("document decoding failed")
        raise

    if not isinstance(document, dict):
        raise TypeError("document must be a JSON object")
    return document
```

`logger.exception("...")` is equivalent to
`logger.error("...", exc_info=True)`.

If this function re-raises and a higher boundary also logs, the same failure
appears twice. Prefer logging once where the exception is finally converted into
a response, failed-job state, exit code, or other policy. Lower layers should
raise or translate with useful context. See
[Exceptions](../exceptions.md#8-low-layers-describe-boundaries-decide-policy).

---

## 6. Pick Destinations for the Deployment Model

For a local script, a file can be convenient:

```python
logging.basicConfig(
    filename="app.log",
    encoding="utf-8",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
```

For containers, emitting to stdout or stderr and letting the runtime or platform
collect logs is usually simpler than writing files inside the container:

```python
import sys

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
```

Choose stdout or stderr according to the platform's ingestion contract. The
logging default is stderr because logs are diagnostics, separate from a command's
normal stdout data.

When an application truly owns local log files, rotate them:

```python
import logging
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    "app.log",
    maxBytes=5_000_000,
    backupCount=5,
    encoding="utf-8",
)
handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
)

root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(handler)
```

`RotatingFileHandler` is not a multi-process log coordinator. Multiple worker
processes rotating the same file can race. Prefer one collector per process,
stdout/stderr aggregation, or a handler designed for the deployment.

---

## 7. Application Code and Library Code Have Different Roles

Application entry points:

- configure handlers, formatters, and levels;
- choose destinations;
- define retention/rotation or delegate it to the platform.

Reusable libraries:

- call `logging.getLogger(__name__)`;
- do not call `basicConfig()`;
- do not add application destinations;
- may attach `logging.NullHandler()` to their top-level package logger.

```python
# my_library/__init__.py
import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
```

This keeps the consuming application in control. A library must not silently
create files, change the root level, or clear handlers installed by its host.

> **Mental model**: modules emit named records; the owning application configures
> their route and presentation once.

---

**Next**: [Logger Hierarchy and Propagation](02_hierarchy_and_propagation.md)
