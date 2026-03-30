# Logging Patterns

## When to Use Which

| Pattern | Use when |
|---------|----------|
| Universal logger | Most projects — single audit/debug trail, easy to grep |
| Per-subfolder logger | You want log separation (data vs model), or shipping a package |
| Both | You want per-area files AND a global trail |

---

## Universal Logging (Centralized)

Configure logging **once** at startup. Every module uses `getLogger(__name__)` and records propagate up to root.

### Option A — `basicConfig` at entry point

Simple. Use this for scripts and small projects.

```python
# main.py
import logging

logging.basicConfig(
    filename="all.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
```

```python
# any other module
import logging
logger = logging.getLogger(__name__)

def do_something():
    logger.info("Running task")
```

### Option B — `setup_logging()` utility (recommended for teams)

More flexible: easy to add console + file together, update config in one place, avoid `basicConfig` idempotency issues.

```python
# src/utils/logger.py
import logging

def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root.addHandler(ch)

    fh = logging.FileHandler("all.log")
    fh.setFormatter(formatter)
    root.addHandler(fh)
```

```python
# main.py
from utils.logger import setup_logging

setup_logging()  # call ONCE at startup
```

```python
# any module
import logging
logger = logging.getLogger(__name__)

def do_stuff():
    logger.info("Logging from %s", __name__)
```

---

## Per-Subfolder Logging

Each subfolder logs to its own file. Modules in the same folder share one logger.

**Project structure:**

```
src/
  data/
    __init__.py
    logger.py
    load.py
  model/
    __init__.py
    logger.py
    train.py
```

**`src/data/logger.py`:**

```python
import logging
import os

LOG_FILE = os.path.join(os.path.dirname(__file__), "data.log")

def get_data_logger() -> logging.Logger:
    logger = logging.getLogger("data")
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():          # prevent duplicate handlers
        fh = logging.FileHandler(LOG_FILE)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
        logger.propagate = False          # don't also emit from root
    return logger
```

**In any `data` module, e.g. `load.py`:**

```python
from .logger import get_data_logger

logger = get_data_logger()

def fetch():
    logger.info("Fetching data...")
```

Repeat for `src/model/logger.py` (change logger name to `"model"` and filename to `"model.log"`).

---

## Mixing Both: Universal + Per-Subfolder

Set up root logging in `main.py` for a global trail, and add per-subfolder loggers on top.

**`main.py`:**

```python
from utils.logger import setup_logging
setup_logging()   # root → all.log + console
```

**`src/data/logger.py`** (same as above, but note `propagate=False` stops double-emit):

```python
def get_data_logger() -> logging.Logger:
    logger = logging.getLogger("data")
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        fh = logging.FileHandler(LOG_FILE)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(fh)
        logger.propagate = False   # ← critical when root also has handlers
    return logger
```

If you want records to appear in **both** `data.log` and `all.log`, set `propagate=True` (the default). The record will be handled by the subfolder's `FileHandler` AND walk up to root's handlers.

---

## Best Practices

1. **Never call `basicConfig` or `setup_logging()` more than once per process.** Multiple calls add duplicate handlers to root.
2. **Always check `if not logger.hasHandlers()`** before adding handlers in factory functions.
3. **Set `propagate=False`** on named loggers that have their own handlers, unless you explicitly want double-emit.
4. **Never configure logging in a library's `__init__.py`** — only in application entry points.
5. **Use `logger.exception()` in `except` blocks**, not `logger.error()` — it attaches the traceback automatically.
6. **Pass arguments as parameters**, not f-strings: `logger.info("user %s logged in", user_id)` — this avoids string formatting when the level is filtered out.

---

## TL;DR

| Need | What to do |
|------|------------|
| Log per subfolder | Each folder: `logger.py` + `get_logger()` + `FileHandler` + `propagate=False` |
| Log everywhere | Configure once at entry, `getLogger(__name__)` in every module |
| Both | Central `setup_logging()` + subfolder loggers with `propagate=False` |
