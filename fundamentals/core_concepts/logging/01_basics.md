# Logging Basics

## Why Use Logging Instead of `print()`

`print()` is quick but unstructured and hard to control. `logging` gives you:

- Timestamped messages
- Log levels (info, warning, error, etc.)
- Logging to files (for debugging, audits, production)
- Centralized control across scripts and modules

---

## Basic Setup

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logging.info("App started")
logging.warning("Something might be wrong")
logging.error("An error occurred")
```

Output:

```
2025-07-05 15:00:00,000 [INFO] root: App started
```

---

## Logging to a File

```python
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
```

- File is created in the current working directory (`./app.log`)
- Log messages are appended on every run

---

## Logging from Multiple Modules

In each module (e.g. `data.py`, `model.py`):

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Fetching data...")
logger.error("Failed to load model")
```

All modules respect the **main config** you define once in your entry point (`main.py`). The logger name becomes the module's `__name__` (e.g. `src.data.load`), which maps to a node in the logger hierarchy. See [02_hierarchy_and_propagation.md](02_hierarchy_and_propagation.md) for how parent/child loggers relate and how records propagate up the tree.

---

## Logging Exceptions with Tracebacks

Use `logger.exception()` inside `except` blocks — it automatically captures the full traceback:

```python
try:
    1 / 0
except ZeroDivisionError:
    logger.exception("Something went wrong")
```

Output:

```
2025-07-05 15:02:00,000 [ERROR] my_module: Something went wrong
Traceback (most recent call last):
  File "script.py", line 10, in <module>
    1 / 0
ZeroDivisionError: division by zero
```

`logger.exception()` is equivalent to `logger.error(..., exc_info=True)`.

---

## Log Levels Reference

| Level      | Numeric | Use for                               |
|------------|---------|---------------------------------------|
| `DEBUG`    | 10      | Detailed dev/debug info               |
| `INFO`     | 20      | Normal operation messages             |
| `WARNING`  | 30      | Something unexpected but not crashing |
| `ERROR`    | 40      | An error occurred, app can continue   |
| `CRITICAL` | 50      | Severe error, app may not recover     |

A logger set to level `INFO` will emit `INFO`, `WARNING`, `ERROR`, and `CRITICAL` — but not `DEBUG`.

---

## Log Rotation

To avoid huge log files, use `RotatingFileHandler`:

```python
import logging
from logging.handlers import RotatingFileHandler

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    fh = RotatingFileHandler("all.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
```

`maxBytes=5_000_000` rolls over at ~5 MB. `backupCount=5` keeps the last 5 rotated files.

---

## Keeping Logs in a Dedicated Folder

```python
from pathlib import Path
import logging

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=LOG_DIR / "app.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
```
