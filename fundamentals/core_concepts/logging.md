

# 📘 Logging in Python — A Complete Guide

This README explains how to set up and use logging in Python across your entire project, including how to:

* Configure logging for console and file output
* Use proper log levels (`INFO`, `ERROR`, `WARNING`, `CRITICAL` etc.)
* Share logging across multiple files/packages
* Log exceptions with full tracebacks
* Customize logging formats and locations
* Structure logging for **per-folder** and **universal** use cases

---

## ✅ 1. Why Use Logging Instead of `print()`?

* `print()` is quick but unstructured and hard to control.
* `logging` gives you:

  * Timestamped messages
  * Log levels (info, warning, error, etc.)
  * Logging to files (for debugging, audits, production)
  * Centralized control across scripts and modules

---

## ✅ 2. Basic Logging Setup

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

This prints logs to the **console** like:

```
2025-07-05 15:00:00 [INFO] root: App started
```

---

## ✅ 3. Logging to a File

To log to a file instead of (or in addition to) the console:

```python
logging.basicConfig(
    filename="app.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
```

* File will be created in the current working directory (`./app.log`)
* Log messages will be appended on every run

---

## ✅ 4. Logging from Multiple Files or Modules

In each module/script (e.g., `data.py`, `model.py`):

```python
import logging
logger = logging.getLogger(__name__)

logger.info("Fetching data...")
logger.error("Failed to load model")
```

All modules will respect the **main config** you define in your `main.py` or entry point.

---

## ✅ 5. Logging Exceptions with Stack Traces

Use `logger.exception()` inside `except` blocks to automatically include traceback:

```python
try:
    1 / 0
except ZeroDivisionError:
    logger.exception("Something went wrong")
```

Log output:

```
2025-07-05 15:02:00 [ERROR] my_module: Something went wrong
Traceback (most recent call last):
  File "script.py", line 10, in <module>
    1 / 0
ZeroDivisionError: division by zero
```

---

## ✅ 6. Structured Logging: Per-Subfolder and Universal Options

### 1️⃣ **Per-Subfolder Logging (One Log File per Subfolder)**

**Goal:**

* Each sub-folder (e.g., `src/data/`, `src/model/`) logs to its **own log file** (e.g., `data.log`, `model.log`).
* All `.py` files in the same sub-folder write to the same log file.

**How to do it:**

* Create a `logger.py` in each sub-folder, returning a named logger that writes to a dedicated file.

**Example structure:**

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

def get_data_logger():
    logger = logging.getLogger("data")  # or f"{__name__}"
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():  # avoid duplicate handlers!
        fh = logging.FileHandler(LOG_FILE)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
```

**In any `data` file, e.g., `load.py`:**

```python
from .logger import get_data_logger

logger = get_data_logger()

def fetch():
    logger.info("Fetching data...")
```

*Repeat similarly for `src/model/logger.py` (change names to `"model"` and `"model.log"`).*

**📝 Notes:**

* All code in a sub-folder uses its `logger.py` to get a logger writing to one sub-folder log file.
* You avoid duplicate handlers using `if not logger.hasHandlers()`.
* Log files (`data.log`, `model.log`) are created in each sub-folder.

---

### 2️⃣ **Universal Logging (Centralized Logging for All Folders)**

**Goal:**

* Every log message from **anywhere in `src`** goes to the *same* log file (e.g., `all.log`).
* Classic central logging setup.

#### **Option A: Direct setup in your entry point**

* Configure logging **ONCE** in your main file (e.g., `main.py` or `src/__init__.py`):

**Example:**

```python
import logging

logging.basicConfig(
    filename="all.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
```

**In any other module:**

```python
import logging
logger = logging.getLogger(__name__)

def do_something():
    logger.info("Running task")
```

*Now everything is centralized to `all.log` (wherever you run the script from).*

---

#### **Option B: Using a reusable setup function (recommended for most teams/projects)**

* Define a `setup_logging()` function in `logger.py` or `utils/logger.py`.
* Call it **ONCE** at the very start of your main script.
* In every other file, just use `logging.getLogger(__name__)` as above.

**Example:**

**`src/utils/logger.py`:**

```python
import logging

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    # File handler
    fh = logging.FileHandler("all.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
```

**In your `main.py`:**

```python
from utils.logger import setup_logging

setup_logging()  # Only call this ONCE, at startup!
```

**In any other module:**

```python
import logging
logger = logging.getLogger(__name__)

def do_stuff():
    logger.info("Logging from %s", __name__)
```

**📝 Notes:**

* The `setup_logging()` function pattern makes it easy to:

  * Add console and file logging in one go.
  * Update logging config later without changing every script.
  * Avoid issues with `basicConfig` being called multiple times.
* **Never call `setup_logging()` or `basicConfig` in every module—do it once at the start!**

---

## ⚡️ *When to use which?*

**Per-subfolder logger:**

* You want separation (e.g., logs for data vs. logs for model).
* You want to debug only one area, or ship logs with a package.
* More *microservice* style.

**Universal logger:**

* Most projects do this, especially if you want a single audit or debug trail.
* Easier to grep/search.

---

## ✅ *Mixing both: Universal + Per-subfolder*

You *can* do both:

* All code logs to a **universal** file.
* AND specific subfolders have their own logger.

**How:**

* In `main.py`, configure root logger for universal logging.
* In subfolders, set up their own loggers with additional file handlers.

---

## 💡 *Best Practices / Tips*

1. **Never call `basicConfig` or your logging setup function more than once per process.**
2. Always check `if not logger.hasHandlers()` before adding handlers (prevents duplicate logs).
3. Use logger names (`logging.getLogger("data")`, etc) for clear sources.
4. Keep log config (`logger.py`) out of your sub-folder's `__init__.py` to avoid side-effects.
5. To rotate logs (avoid huge files), use `logging.handlers.RotatingFileHandler`.
6. For tests or CLI tools, always make sure logging is not reconfigured multiple times.

---

## 🎯 Example: Both Universal and Subfolder Logging

**In `main.py`:**

```python
from utils.logger import setup_logging
setup_logging()
```

**In `src/data/logger.py`:**

```python
import logging
import os

LOG_FILE = os.path.join(os.path.dirname(__file__), "data.log")

def get_data_logger():
    logger = logging.getLogger("data")
    logger.setLevel(logging.INFO)
    if not logger.hasHandlers():
        fh = logging.FileHandler(LOG_FILE)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger
```

**In `src/data/load.py`:**

```python
from .logger import get_data_logger
import logging

logger = get_data_logger()
root_logger = logging.getLogger()  # this goes to all.log

def do_stuff():
    logger.info("Logging to data.log")
    root_logger.info("Logging to all.log")
```

---

## 🧑‍💻 *How to "invoke" or "import" loggers*

* For **per-subfolder**:
  In every `.py` file in the subfolder, do
  `from .logger import get_data_logger`
  and then `logger = get_data_logger()`

* For **universal logging**:
  In every `.py` just do
  `import logging`
  and then `logger = logging.getLogger(__name__)`
  (and make sure logging is configured ONCE at startup.)

---

## ✨ TL;DR Table

| Need               | What to do                                           |
| ------------------ | ---------------------------------------------------- |
| Log per sub-folder | Each folder: logger.py + get\_logger() + FileHandler |
| Log everywhere     | Setup logging once at entry, getLogger(**name**)     |
| Both               | Both: central config + subfolder loggers             |

---

## ✅ 8. Log Levels Reference

| Level      | Use for                               |
| ---------- | ------------------------------------- |
| `DEBUG`    | Detailed dev/debug info               |
| `INFO`     | Normal operation messages             |
| `WARNING`  | Something unexpected but not crashing |
| `ERROR`    | An error occurred, app can continue   |
| `CRITICAL` | Severe error, app may not recover     |

---

## ✅ 9. Bonus: Logging to Console AND File

If you want logs both in the console and in a file, use the setup function in your utility module (`logger.py`):

```python
import logging

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    # Console
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    # File
    fh = logging.FileHandler("combined.log")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
```

Call this **once at startup** (e.g., in `main.py`).

---

## ✅ 10. Optional: Log to a Folder

If you want to keep all logs in a dedicated folder:

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

Or adjust your `setup_logging()` to use a path in your logs directory.

---

## ✅ 11. Optional: Log Rotation

To avoid huge log files, use log rotation:

```python
import logging
from logging.handlers import RotatingFileHandler

def setup_logging():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Rotating file handler
    fh = RotatingFileHandler("all.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
```

---

# 🎉 That's It!

* Logging is powerful, flexible, and professional when you set it up right.
* Always configure logging ONCE at the top of your app, then use `getLogger(__name__)` everywhere.
* For most projects, a single universal logger is easiest and cleanest, but you can do per-folder if needed.
* Utility functions like `setup_logging()` in a central module make config easy to maintain.

---
