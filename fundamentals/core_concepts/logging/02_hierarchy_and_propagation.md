# Logger Hierarchy and Propagation

## The Logger Tree

Every logger has a name. Python maps these names to a tree using dot-notation — each dot is a parent-child boundary:

```
root  ("")
├── gtracer
├── exceptionist
│   ├── exceptionist.ai_engine
│   │   └── exceptionist.ai_engine.agent
│   └── exceptionist.core
│       └── exceptionist.core.tracer
└── uvicorn
    └── uvicorn.access
```

`logging.getLogger("exceptionist.ai_engine.agent")` creates (or retrieves) the node at that path. `logging.getLogger(__name__)` does the same thing automatically using the module's dotted import path.

The **root logger** is the top of every tree. It has no name (`""`) and is retrieved with `logging.getLogger()` (no argument).

---

## How Records Propagate

When a logger emits a record, it:

1. Passes the record to all its **own handlers**
2. If `propagate=True` (the default), passes it **up to the parent**, which does the same — all the way to root

```
exceptionist.ai_engine.agent  (no handler, propagate=True)
  → exceptionist.ai_engine    (no handler, propagate=True)
    → exceptionist             (no handler, propagate=True)
      → root                  (has StreamHandler) ← record emitted here
```

This is why you can call `logging.getLogger(__name__)` in every module without configuring handlers there — the record walks up to root where the handler lives.

---

## `propagate=False` — Stopping the Walk

Set `propagate=False` to prevent a record from reaching parent loggers:

```python
logger = logging.getLogger("gtracer")
logger.setLevel(logging.INFO)
logger.addHandler(some_handler)
logger.propagate = False   # record stops here, never reaches root
```

```
gtracer  (has its own handler, propagate=False)
  → stops here, never reaches root
```

This is the standard pattern when a logger needs its own destination (e.g. a dedicated file) and you don't want the same record also emitting from root.

---

## The Double-Emit Problem

If you add a handler to a named logger **and** leave `propagate=True`, records get emitted twice — once by the logger's own handler and once by root's handler:

```python
# BAD — double emit
logger = logging.getLogger("myapp")
logger.addHandler(FileHandler("myapp.log"))    # handler here
# propagate=True by default → also reaches root's StreamHandler
```

Fix with either:

```python
logger.propagate = False   # option 1 — cut the walk
```

```python
if not logger.hasHandlers():   # option 2 — skip adding if already set up
    logger.addHandler(...)
```

The `if not logger.hasHandlers()` guard is common in library code and per-subfolder logger factories, where the same setup function might be called multiple times.

---

## Level Filtering at Each Node

A logger only emits a record if the record's level is **≥ the logger's own level**. But a logger with no explicit level set inherits its **effective level** from its nearest ancestor that has one:

```python
logging.getLogger("exceptionist").setLevel(logging.WARNING)
# exceptionist.ai_engine inherits WARNING (no level set on it)
# exceptionist.ai_engine.agent inherits WARNING too

logging.getLogger("exceptionist.ai_engine").setLevel(logging.DEBUG)
# now exceptionist.ai_engine and its children use DEBUG
# exceptionist.core.tracer still uses WARNING (from exceptionist)
```

You can verify the effective level:

```python
logger.getEffectiveLevel()  # returns the numeric level in effect
```

---

## Quick Reference

| Concept | Default | What it does |
|---------|---------|--------------|
| `propagate` | `True` | Passes record up to parent after own handlers run |
| `propagate=False` | — | Record stops at this logger; parent never sees it |
| Level on logger | Inherited from parent | Filters records before they reach any handler |
| Root logger | Always exists | Last stop in every propagation chain |
