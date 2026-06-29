# Context Managers

> Context managers are the mechanism behind the `with` statement. They pair **setup** with **guaranteed teardown** — even when the body raises — and they are everywhere in this corpus: database sessions, file handles, HTTP clients, logging contexts, contextvars binding, FastAPI lifespan, FastAPI dependencies with `yield`, distributed locks.

---

## 1. What `with` Actually Does

```python
with open("data.txt") as f:
    content = f.read()
# file is closed here, even if read() raised
```

The `with` statement is syntactic sugar for calling two dunder methods: `__enter__` and `__exit__`. Conceptually:

```python
# with open("data.txt") as f: body  # equivalent to:
cm = open("data.txt")
f = cm.__enter__()
try:
    # body
    content = f.read()
except BaseException:
    if not cm.__exit__(*sys.exc_info()):
        raise
else:
    cm.__exit__(None, None, None)
```

The important guarantees:

- `__exit__` **always** runs — on normal exit, on exception, on `return`/`break`/`continue` from inside.
- `__exit__` receives the exception info. If it returns a truthy value, the exception is **suppressed**. Almost every context manager returns `None` (falsy), meaning exceptions propagate normally.
- `__enter__`'s return value is what `as <name>` binds. It does not have to be the context manager itself.

---

## 2. Class-Based Context Managers

```python
class Timer:
    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        import time
        self.start = time.perf_counter()
        return self  # what `as t` will bind

    def __exit__(self, exc_type, exc, tb):
        import time
        self.duration = time.perf_counter() - self.start
        print(f"{self.label}: {self.duration:.3f}s")
        # return None → exceptions (if any) propagate


with Timer("load") as t:
    expensive_call()
print(t.duration)
```

**Parameters of `__exit__`:**

| Param | On normal exit | On exception |
|-------|----------------|--------------|
| `exc_type` | `None` | exception class |
| `exc` | `None` | exception instance |
| `tb` | `None` | traceback object |

---

## 3. `@contextmanager` Decorator

Most of the time a class is overkill. `contextlib.contextmanager` turns a generator into a context manager: the code before `yield` is `__enter__`, the code after `yield` is `__exit__`.

```python
from contextlib import contextmanager
import time


@contextmanager
def timer(label: str):
    start = time.perf_counter()
    try:
        yield               # body of `with` runs here
    finally:
        print(f"{label}: {time.perf_counter() - start:.3f}s")


with timer("load"):
    expensive_call()
```

**The `try/finally` is load-bearing.** Without it, an exception inside the `with` body jumps out of the generator before the cleanup code runs, defeating the point. The pattern is:

```python
@contextmanager
def my_cm():
    resource = acquire()
    try:
        yield resource
    finally:
        release(resource)
```

This is the generator-based equivalent of a class with `__enter__`/`__exit__`.

---

## 4. Suppressing Exceptions

A class-based `__exit__` returning truthy suppresses the exception. The generator equivalent is to catch the exception before `yield` re-raises:

```python
from contextlib import contextmanager


@contextmanager
def ignore(*exceptions):
    try:
        yield
    except exceptions:
        pass


with ignore(FileNotFoundError):
    open("maybe-missing.txt")
# control reaches here whether the file existed or not
```

`contextlib` ships this as `contextlib.suppress(...)`. Use it sparingly — silently swallowing exceptions is usually wrong.

---

## 5. Async Context Managers — `async with`

For resources that need **await** during setup or teardown (HTTP clients, DB sessions, async locks), use `__aenter__` / `__aexit__`, or `@asynccontextmanager`:

```python
from contextlib import asynccontextmanager
import httpx


@asynccontextmanager
async def http_client():
    client = httpx.AsyncClient(timeout=30)
    try:
        yield client
    finally:
        await client.aclose()   # async teardown


async def main():
    async with http_client() as client:
        r = await client.get("https://example.com")
```

**You cannot use `with` on an async context manager, and you cannot use `async with` on a sync one.** The `@contextmanager` and `@asynccontextmanager` decorators are not interchangeable.

---

## 6. `ExitStack` — Dynamic Composition

When you do not know *ahead of time* how many context managers you need — or you need to build them in a loop — `contextlib.ExitStack` lets you push them onto a stack that unwinds in LIFO order on exit.

```python
from contextlib import ExitStack


def open_many(paths: list[str]):
    with ExitStack() as stack:
        files = [stack.enter_context(open(p)) for p in paths]
        # all files are open here; all closed at the end of `with`
        return process(files)
```

Async version is `AsyncExitStack` with `enter_async_context(...)`.

`ExitStack` is also how you build "enter these N context managers, but decide which ones at runtime" patterns — e.g. conditionally attach a profiler, conditionally acquire a distributed lock.

---

## 7. Common Pitfalls

### Exception inside `with` — the resource still closes

```python
with open("x.txt") as f:
    data = f.read()
    raise ValueError("oops")
# f.close() ran before the ValueError propagated
```

This is the entire point of `with`. You do not need a manual `try/finally`.

### Forgetting `try/finally` in `@contextmanager`

```python
# ❌ resource leaks if body raises
@contextmanager
def broken():
    conn = open_connection()
    yield conn
    conn.close()            # never runs if body raised

# ✅ cleanup guaranteed
@contextmanager
def correct():
    conn = open_connection()
    try:
        yield conn
    finally:
        conn.close()
```

### `yield`ing twice in a `@contextmanager` generator

```python
@contextmanager
def bad():
    yield 1
    yield 2   # RuntimeError: generator didn't stop
```

A context-manager generator must yield **exactly once**. One setup, one body, one teardown.

### Mixing up what `as` binds

```python
with open("x.txt") as f:     # f is the file object (what __enter__ returned)
    ...

with Timer("t") as t:        # t is the Timer instance (we returned self)
    ...

with lock:                   # no `as` — we don't need the return value
    ...
```

### Catching `Exception` in `__exit__` and returning truthy by accident

```python
class Bad:
    def __exit__(self, *exc):
        log_if_error(exc)
        return True   # 💥 silently swallows every exception
```

Return `None` (or nothing) unless you specifically mean to suppress.

---

## 8. Where You Actually See This in This Corpus

| File | Context manager usage |
|------|-----------------------|
| [fastapi/02_dependency_injection.md](../fastapi/02_dependency_injection.md) | `yield`-based dependencies (same generator-CM pattern) + `@asynccontextmanager` lifespan |
| [concurrency/async/03_contextvars.md](../concurrency/async/03_contextvars.md) | `contextvars.Context().run(...)` and the set/reset `try/finally` pattern |
| [database/04_async_sqlalchemy.md](../database/04_async_sqlalchemy.md) | `async with session.begin():` for transactions |
| [httpx/01_mental_model.md](../httpx/01_mental_model.md) | `async with httpx.AsyncClient() as client:` |
| [infrastructure/redis/03_caching_patterns.md](../../infrastructure/redis/03_caching_patterns.md) | Distributed lock context managers |
| [fastapi/05_middleware.md](../fastapi/05_middleware.md) | `asynccontextmanager` in lifespan examples |

If you understand the generator-with-`try/finally` form, you understand every one of these — they are all the same mechanism with different setup/teardown work inside.
