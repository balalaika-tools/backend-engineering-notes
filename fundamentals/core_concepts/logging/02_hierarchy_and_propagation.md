# Logger Hierarchy and Propagation

> **Who this is for**: Python developers who see duplicate, missing, or wrongly
> filtered logs and need to understand exactly how logger names, effective
> levels, handlers, and `propagate` interact.

Logger names form a tree. A record is admitted by the logger where the call was
made, then offered to handlers on that logger and its ancestors.

---

## 1. Dotted Names Build the Tree

```text
root  ("")
├── myapp
│   ├── myapp.api
│   │   └── myapp.api.orders
│   └── myapp.workers
└── uvicorn
    └── uvicorn.access
```

```python
import logging

orders_logger = logging.getLogger("myapp.api.orders")
module_logger = logging.getLogger(__name__)
root_logger = logging.getLogger()
```

Multiple calls to `getLogger()` with the same name return the same `Logger`
object. Never instantiate `logging.Logger` directly.

Using `getLogger(__name__)` makes the logger hierarchy match the package
hierarchy. Configuration can then target one module or a complete subtree
without changing application code.

---

## 2. Effective Level Is the Admission Gate

A new non-root logger has level `NOTSET`. That means it searches upward for the
nearest explicitly set level; the result is its **effective level**.

```python
import logging

logging.getLogger().setLevel(logging.WARNING)
logging.getLogger("myapp").setLevel(logging.INFO)
logging.getLogger("myapp.api").setLevel(logging.DEBUG)

orders = logging.getLogger("myapp.api.orders")
workers = logging.getLogger("myapp.workers")

assert orders.getEffectiveLevel() == logging.DEBUG
assert workers.getEffectiveLevel() == logging.INFO
```

When code calls `orders.debug(...)`, the `orders` logger uses that effective
level to decide whether to create and process a record.

```
call on myapp.api.orders
           │
           ▼
record level >= orders effective level?
        │                    │
       no                   yes
        │                    │
     discard              create record
```

This ancestor lookup happens to determine the **originating logger's** effective
level. It is not a second round of filtering during propagation.

---

## 3. Propagation Goes Directly to Ancestor Handlers

After a record passes the originating logger's level and filters:

1. handlers attached directly to that logger are offered the record;
2. if `propagate=True` (the default), handlers on its parent are offered it;
3. the walk continues until root or a logger with `propagate=False`.

```text
myapp.api.orders  (origin, no handler, propagate=True)
        │
        ▼
myapp.api         (no handler, propagate=True)
        │
        ▼
myapp             (no handler, propagate=True)
        │
        ▼
root              (console handler emits)
```

The subtle rule: propagation passes the record directly to ancestor
**handlers**. Ancestor logger levels and ancestor logger filters are not
consulted.

```python
root = logging.getLogger()
root.setLevel(logging.ERROR)

child = logging.getLogger("myapp.api")
child.setLevel(logging.DEBUG)

# If root has a DEBUG-capable handler, this record can still be emitted there.
# root's ERROR logger level does not re-filter a record admitted by child.
child.debug("request decoded")
```

Handler levels still apply. To restrict what a destination emits, set the level
on that handler.

> **Key insight**: logger levels answer "should this call create a record?"
> Handler levels answer "should this destination emit the admitted record?"

---

## 4. Use `propagate=False` for an Owned Subtree

Set `propagate=False` when a named logger has its own complete handler route and
must not also reach ancestors:

```python
import logging

audit = logging.getLogger("myapp.audit")
audit.setLevel(logging.INFO)
audit.addHandler(logging.FileHandler("audit.log", encoding="utf-8"))
audit.propagate = False
```

```text
myapp.audit  ──► audit.log
     │
     └── propagation stops; root handlers do not receive the record
```

This setting applies to descendants too. A record from
`myapp.audit.writer` walks to `myapp.audit`, uses its handlers, and stops there.

Leave propagation enabled when root is the intended centralized route. Most
applications only need handlers at root.

---

## 5. Duplicate Emission Comes from Overlapping Handlers

A record is emitted once per handler it reaches. If a named logger and root each
have a console handler, one call can produce the same line twice:

```text
myapp.api.orders
        │ own handler ─────────────────────► console (first line)
        │ propagate=True
        ▼
root
        └── root handler ──────────────────► console (second line)
```

Choose one intentional topology:

```python
# Option A: centralized application logging
for handler in child.handlers[:]:
    child.removeHandler(handler)
    handler.close()
child.propagate = True
# Configure handlers at root.
```

```python
# Option B: the subtree owns its destination
child.addHandler(dedicated_handler)
child.propagate = False
```

Do not use `logger.hasHandlers()` to mean "does this logger have a handler
directly attached?" It searches ancestors and returns `True` when root has a
handler.

```python
if not logger.handlers:
    # This checks only handlers directly attached to this logger.
    logger.addHandler(dedicated_handler)
```

Even this guard is a fallback for an idempotent local factory. Centralized
configuration is clearer because it builds the topology in one place.

---

## 6. Diagnose a Missing or Duplicate Record

Inspect both the originating logger and every handler it can reach:

```python
import logging


def describe_logger(name: str) -> dict[str, object]:
    logger = logging.getLogger(name)
    return {
        "name": logger.name,
        "level": logging.getLevelName(logger.level),
        "effective_level": logging.getLevelName(logger.getEffectiveLevel()),
        "propagate": logger.propagate,
        "disabled": logger.disabled,
        "direct_handlers": [
            {
                "type": type(handler).__name__,
                "level": logging.getLevelName(handler.level),
            }
            for handler in logger.handlers
        ],
    }
```

Checklist:

1. Is the logger disabled by configuration?
2. Does the record pass the originating logger's effective level?
3. Does a logger filter reject it?
4. Which direct handlers receive it?
5. Is propagation stopped before the expected ancestor?
6. Does each reached handler's level and filters admit it?
7. Are two reached handlers writing to the same destination?
8. Did `dictConfig()` disable existing loggers?

> **Mental model**: one admission gate at the origin, then a handler walk toward
> root. During that walk, handler policy matters; ancestor logger levels do not.

---

**Next**: [Handlers and Formatters](03_handlers_and_formatters.md)
