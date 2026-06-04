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

---

## stdout, stderr, and Buffering

### What are stdin, stdout, and stderr?

When the OS starts any process it automatically opens three communication channels for it, before your code runs a single line. These are called the **standard streams**:

- **stdin** (standard input) — the channel the process reads from. By default it's the keyboard; when you pipe data (`cat file.txt | python app.py`), stdin becomes that pipe.
- **stdout** (standard output) — the channel the process writes its normal output to. `print()` goes here. By default it goes to the terminal.
- **stderr** (standard error) — a second output channel reserved for diagnostics, warnings, and errors. It goes to the same terminal by default, but it's a *separate stream* so it can be redirected independently.

The term **stdio** (short for "standard I/O") refers to all three together. It comes from the C standard library (`<stdio.h>`) and the concept carried into every Unix-derived language and OS.

In Python these are exposed as `sys.stdin`, `sys.stdout`, and `sys.stderr` — ordinary file objects you can read from, write to, or replace entirely.

### The three standard streams

Every process inherits three open file descriptors from the OS:

| Stream | fd | `sys` name | Default destination |
|--------|----|------------|---------------------|
| stdin  | 0  | `sys.stdin`  | keyboard / pipe in  |
| stdout | 1  | `sys.stdout` | terminal / pipe out |
| stderr | 2  | `sys.stderr` | terminal (unbuffered)|

They are just file objects — you can redirect, replace, or wrap them like any other file.

### Why logging defaults to stderr

`StreamHandler()` with no argument writes to `sys.stderr`, not `sys.stdout`. The reason: logs are diagnostic output, not program output. Keeping them on separate streams lets you pipe or redirect one without polluting the other:

```bash
python app.py > output.txt        # stdout captured, logs still visible
python app.py 2>/dev/null         # logs suppressed, stdout visible
python app.py > out.txt 2> err.txt  # both captured separately
```

If you send logs to stdout you lose that separation — structured output (JSON API responses piped into `jq`, for example) and log lines get mixed together.

### Python buffering modes

Python buffers writes to speed up I/O. Three modes exist:

| Mode | Behaviour | Default for |
|------|-----------|-------------|
| Unbuffered | Every write hits the OS immediately | `sys.stderr`, binary streams |
| Line-buffered | Flush on each `\n` | `sys.stdout` when attached to a TTY |
| Block-buffered | Accumulate up to ~8 KB then flush | `sys.stdout` when **not** attached to a TTY (e.g. inside a container or pipe) |

The problem in containers: when stdout is not a TTY (it's a pipe to Docker's log driver), Python switches to block-buffered mode. A crash mid-block means those log lines never reach the log driver — they were still sitting in the buffer when the process died.

### `PYTHONUNBUFFERED=1`

Set this environment variable to force unbuffered stdout/stderr:

```bash
# Dockerfile
ENV PYTHONUNBUFFERED=1

# or at runtime
PYTHONUNBUFFERED=1 python app.py
```

This is equivalent to running `python -u`. It ensures every log line (or `print`) is flushed immediately, so container logs are complete even on crash.

> Always set `PYTHONUNBUFFERED=1` in any Docker/Kubernetes workload. The cost is negligible; the benefit is that you never lose the last lines before a crash.

---

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

## Async Gotcha: Logging Is Blocking

Every handler call is **synchronous and blocking**. `FileHandler` does a blocking disk write; a network/HTTP handler does a blocking socket write. Inside an `async def` (FastAPI route, asyncio task), a slow handler stalls the event loop and blocks *every* concurrent request — `logger.info(...)` is not awaitable and offers no yield point.

Fix: hand records off to a background thread with `QueueHandler` + `QueueListener`. The logger only enqueues (near-instant); a dedicated listener thread runs the real handlers.

```python
import logging
import queue
from logging.handlers import QueueHandler, QueueListener

log_queue: queue.Queue = queue.Queue(-1)   # unbounded

# The real handlers run in the listener thread, off the event loop
file_handler = logging.FileHandler("app.log")
file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)

# Loggers only enqueue — non-blocking
root = logging.getLogger()
root.setLevel(logging.INFO)
root.addHandler(QueueHandler(log_queue))

listener = QueueListener(log_queue, file_handler, respect_handler_level=True)
listener.start()   # start at boot; call listener.stop() on shutdown to flush
```

This keeps the event loop responsive. `dictConfig` can wire this up declaratively via a `queue_handler` / `QueueListener` config (Python 3.12+ supports it natively in `dictConfig`).

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
