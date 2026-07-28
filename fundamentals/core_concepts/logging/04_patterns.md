# Logging Configuration Patterns

> **Who this is for**: Application and library authors choosing where logging is
> configured, how records are routed, and how that configuration behaves under
> frameworks, tests, multiple processes, and production collectors.

The default for most applications is simple: configure handlers once at the
entry point, create `getLogger(__name__)` loggers everywhere else, and let records
propagate to root.

---

## 1. Choose the Smallest Pattern That Fits

| Situation | Recommended starting point |
|-----------|----------------------------|
| One-file script or CLI | `basicConfig()` in `main()` |
| Multi-module service | One `dictConfig()` or owned setup function at startup |
| FastAPI under Uvicorn | One coordinated config for app and server loggers |
| Reusable library | No application config; module loggers plus `NullHandler` |
| Containerized service | stdout/stderr, collected by the platform |
| Dedicated audit stream | Named logger with an additional dedicated handler |
| High-throughput async service | Queue between log calls and slow handlers |

Do not create a per-directory logger factory merely because code is organized
into directories. `getLogger(__name__)` already gives every package subtree a
stable name that configuration can target.

---

## 2. Small Applications: `basicConfig()`

```python
# app/main.py
import logging
import sys

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("service started")


if __name__ == "__main__":
    main()
```

Imported modules only declare their logger:

```python
# app/orders.py
import logging

logger = logging.getLogger(__name__)


def create_order(order_id: str) -> None:
    logger.info("order created id=%s", order_id)
```

Call `basicConfig()` before application logging begins. If root already has a
handler, it does nothing unless `force=True`; this often explains why a notebook,
test runner, or framework ignores a later `basicConfig()` call.

Use `force=True` only when this entry point intentionally owns and replaces all
root handlers.

---

## 3. Services: Declarative `dictConfig()`

`dictConfig()` makes names, destinations, thresholds, and propagation visible in
one structure:

```python
import logging.config

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "service": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "formatter": "service",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        # Keep a noisy dependency useful without hiding its warnings/errors.
        "httpcore": {
            "level": "WARNING",
            "handlers": [],
            "propagate": True,
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}


def configure_logging() -> None:
    logging.config.dictConfig(LOGGING)
```

`version` is required and currently must be `1`.

Set `disable_existing_loggers=False` unless intentionally disabling loggers
created before configuration. The default is `True`, a frequent cause of
third-party or import-time loggers disappearing.

Call the function once from the process entry point. Worker processes each need
their own logging setup after they start; handlers and listener threads are
process-local resources.

---

## 4. Dedicated Streams: Configure the Subtree, Not Each File

Suppose security audit records must go to `audit.log` while normal application
records go to the console:

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "service": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "service",
            "stream": "ext://sys.stdout",
        },
        "audit_file": {
            "class": "logging.FileHandler",
            "formatter": "service",
            "filename": "audit.log",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "myapp.audit": {
            "level": "INFO",
            "handlers": ["audit_file"],
            "propagate": False,
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}
```

Every module under `myapp.audit` still uses `getLogger(__name__)`. Propagation
stops at the configured subtree, so audit records only reach the file.

If audit records should reach both the file and root console, use
`propagate=True`. That is intentional fan-out to two destinations, not an
accidental duplicate. A duplicate occurs when overlapping handlers write the
same record to the same destination.

Before writing audit files, decide whether the application process is the right
retention boundary. Security audit logs often need tamper resistance, controlled
access, durable centralized storage, and retention policy beyond a local
`FileHandler`.

---

## 5. Avoid Handler Factories Unless Runtime Composition Requires Them

When dynamic setup is unavoidable, check direct handlers, not `hasHandlers()`:

```python
import logging


def configure_audit_logger(handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger("myapp.audit")
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        logger.addHandler(handler)

    logger.propagate = False
    return logger
```

`logger.hasHandlers()` searches parents. If root has a console handler, it
returns `True` even when `logger.handlers` is empty and the dedicated audit
handler was never installed.

Factories also create ownership questions:

- Who closes the handler?
- Can two calls supply different handlers?
- What happens after test reconfiguration?
- Does a forked worker inherit an unsafe open file descriptor?

Centralized declarative configuration avoids most of these ambiguities.

---

## 6. Frameworks and Servers Already Have Logging

Uvicorn, Gunicorn, test runners, notebooks, and CLI frameworks may configure
logging before application startup. Do not blindly call `basicConfig()` and
assume it won.

For a FastAPI deployment, decide explicitly:

1. preserve the server's configuration and let application loggers propagate;
2. supply Uvicorn a logging configuration that includes application and server
   logger names; or
3. intentionally replace the process-wide configuration before serving.

Keep access logs, application logs, and error logs distinguishable by logger name
or a structured field. Avoid attaching handlers to both `uvicorn` and its child
`uvicorn.access` unless their propagation settings are designed together.

When using multiple worker processes, each process writes independently. Local
file rotation and in-process queues do not coordinate across workers. A platform
collector or logging sidecar is usually the cleaner process boundary.

---

## 7. Libraries Emit Records but Never Own the Host

A reusable package should:

```python
# reusable_package/client.py
import logging

logger = logging.getLogger(__name__)
```

Optionally, its top-level package can install a `NullHandler`:

```python
# reusable_package/__init__.py
import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())
```

It should not:

- call `basicConfig()` or `dictConfig()`;
- add a `StreamHandler` or `FileHandler` for the host;
- set the root level;
- clear existing handlers;
- set `propagate=False` unless the package deliberately owns a complete route.

Expose logger names and meaningful levels in documentation so the consuming
application can tune noise.

---

## 8. Testing Logging Behavior

Test emitted semantics, not exact timestamps or a complete formatted line:

```python
import logging


def test_order_log_contains_identifier(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="app.orders"):
        create_order("ord-1042")

    matching = [
        record
        for record in caplog.records
        if record.name == "app.orders" and record.getMessage() == (
            "order created id=ord-1042"
        )
    ]
    assert len(matching) == 1
```

For configuration tests, verify topology separately:

- expected handlers are attached once;
- handler levels are correct;
- dedicated subtrees have the intended `propagate` value;
- repeated test setup does not accumulate handlers;
- queue listeners stop and flush during teardown.

Avoid global logging reconfiguration in ordinary unit tests. Pytest's `caplog`
works with records directly and keeps assertions focused on behavior.

---

## 9. Production Checklist

1. Configure once in each process entry point.
2. Use `getLogger(__name__)` in every module.
3. Keep root as the default handler route.
4. Put destination thresholds on handlers.
5. Use `propagate=False` only for a subtree that owns its full route.
6. Keep `disable_existing_loggers=False` unless silence is intentional.
7. Send container logs to platform-collected streams.
8. Do not share standard rotating files across worker processes.
9. Queue slow handlers off async event-loop threads.
10. Stop queue listeners and close owned handlers during shutdown.
11. Log exceptions once at the policy boundary.
12. Keep secrets and unnecessary personal data out of records.

> **Mental model**: logging configuration is a process-wide routing graph. Build
> it in one owned place, then let named module loggers feed it.

---

**Next**: [Structured Logging with structlog](../structlog_guide.md)
