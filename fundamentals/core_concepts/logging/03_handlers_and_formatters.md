# Handlers and Formatters

## The Pipeline

Every log record flows through this pipeline:

```
Logger.info("msg")
  └── creates LogRecord
        └── Handler  (decides *where* the record goes)
              └── Formatter  (decides *how* it looks)
```

- A **logger** can have multiple handlers
- A **handler** has exactly one formatter (or uses a default plain-text one)
- A **formatter** is stateless — it just renders the `LogRecord` to a string

---

## LogRecord

`LogRecord` is the object Python creates for every log call. It carries:

| Attribute | Format key | Value |
|-----------|------------|-------|
| Message | `%(message)s` | The string you passed in |
| Logger name | `%(name)s` | e.g. `src.data.load` |
| Level name | `%(levelname)s` | `INFO`, `ERROR`, etc. |
| Timestamp | `%(asctime)s` | e.g. `2025-07-05 15:00:00,000` |
| Filename | `%(filename)s` | Source file name |
| Line number | `%(lineno)d` | Line in source file |
| Function | `%(funcName)s` | Function that logged |
| Process ID | `%(process)d` | OS process ID |
| Thread ID | `%(thread)d` | OS thread ID |

---

## Formatters

A formatter turns a `LogRecord` into a string (or dict, for structured logging):

```python
import logging

formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
```

Attach a formatter to a handler:

```python
handler = logging.StreamHandler()
handler.setFormatter(formatter)
```

### Common format strings

```python
# Minimal
"%(levelname)s: %(message)s"

# Standard
"%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# With location
"%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
```

---

## Built-in Handlers

### `StreamHandler` — write to stdout/stderr

```python
import logging
import sys

handler = logging.StreamHandler(sys.stdout)   # default is sys.stderr
handler.setLevel(logging.INFO)
handler.setFormatter(formatter)
```

### `FileHandler` — write to a file

```python
handler = logging.FileHandler("app.log", mode="a", encoding="utf-8")
# mode="a" appends (default); mode="w" overwrites on each run
```

### `RotatingFileHandler` — roll over by size

```python
from logging.handlers import RotatingFileHandler

handler = RotatingFileHandler(
    "app.log",
    maxBytes=5_000_000,   # roll over at ~5 MB
    backupCount=5          # keep app.log.1 … app.log.5
)
```

### `TimedRotatingFileHandler` — roll over by time

```python
from logging.handlers import TimedRotatingFileHandler

handler = TimedRotatingFileHandler(
    "app.log",
    when="midnight",     # "S", "M", "H", "D", "midnight", "W0"–"W6"
    backupCount=7         # keep 7 days of files
)
```

### `NullHandler` — discard silently (library default)

```python
# In library code: prevents "No handlers could be found" warnings
logging.getLogger("mylib").addHandler(logging.NullHandler())
```

---

## Handler-Level Filtering

Both loggers and handlers have their own level. A record must pass **both**:

```python
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)    # logger: let everything through

file_handler = logging.FileHandler("errors.log")
file_handler.setLevel(logging.ERROR)   # handler: only write ERROR and above

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.DEBUG) # handler: write everything

root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)
```

Result: `DEBUG`/`INFO` go to console only; `ERROR`/`CRITICAL` go to both.

---

## Multiple Handlers on One Logger

```python
import logging

def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Console — INFO and above
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # File — everything (DEBUG and above)
    fh = logging.FileHandler("all.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)
```

---

## Custom Formatter (e.g. JSON)

```python
import logging
import json

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        })

handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
```

For production structured logging, prefer [structlog](../structlog_guide.md) — it builds on this model with a richer processor pipeline.
