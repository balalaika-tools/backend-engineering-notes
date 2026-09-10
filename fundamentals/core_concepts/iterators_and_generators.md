# Iterators, Generators, and Lazy Pipelines

> **Who this is for**: Python backend developers who use `for`, comprehensions,
> `yield`, or streaming responses but do not yet have a precise model of
> one-shot iteration, suspension, cleanup, and async generators.

## A generator computes one value, pauses, and keeps its place

Building a list does all the work before the caller receives anything. A
generator lets the caller pull values one at a time:

```python
from collections.abc import Iterator


def order_ids(rows: list[dict[str, str]]) -> Iterator[str]:
    for row in rows:
        print(f"reading {row['id']}")
        yield row["id"]


ids = order_ids([{"id": "ord-1"}, {"id": "ord-2"}])
print("created")
print(next(ids))
print(list(ids))
```

Output:

```text
created
reading ord-1
ord-1
reading ord-2
['ord-2']
```

Calling `order_ids(...)` creates the generator without running its body.
`next(ids)` resumes execution until the first `yield`; `list(ids)` resumes from
that exact point and consumes the remainder.

> **Core:** distinguish the reusable iterable from its advancing iterator, and
> treat any generator object as a one-shot resource unless its API explicitly
> promises otherwise.

> **Key insight**: laziness moves control and resource lifetime from the
> producer to the consumer. The consumer decides when work advances—and must
> also decide how unfinished work is closed.

---

## 1. `for` coordinates the iterable and iterator protocols

Python does not require a collection to support indexing for `for` to work. It
asks for an **iterator**—an object that returns the next value and remembers its
position:

```text
for item in source
        │
        ├── iterator = iter(source)
        ├── item = next(iterator)
        ├── run loop body
        └── repeat until next() raises StopIteration
```

An **iterable** can produce an iterator through `__iter__`. An iterator is the
one-shot cursor itself: its `__iter__` returns itself and its `__next__`
produces the next value or raises `StopIteration`.

```python
numbers = [10, 20]
cursor = iter(numbers)

assert next(cursor) == 10
assert next(cursor) == 20

try:
    next(cursor)
except StopIteration:
    print("exhausted")
```

A `for` loop catches `StopIteration` for you. Application code should normally
use `for` rather than calling `next()` repeatedly; direct `next()` is useful when
the first item has special meaning or when tracing the protocol.

---

## 2. Containers are reusable; iterators are usually one-shot

A list is an iterable that creates a fresh iterator for every pass. A generator
object is already an iterator, so consuming it changes its state:

```python
values = [1, 2, 3]
assert list(values) == [1, 2, 3]
assert list(values) == [1, 2, 3]

stream = (value for value in values)
assert list(stream) == [1, 2, 3]
assert list(stream) == []
```

The empty second result is not a cache or timing bug; the cursor is exhausted.
If every caller needs an independent pass, return an iterable container or a
factory that creates a fresh iterator:

```python
def new_stream():
    return (value for value in values)


assert list(new_stream()) == [1, 2, 3]
assert list(new_stream()) == [1, 2, 3]
```

> **The near-miss**: a generator looks like a compact list because both work in
> `for`. The analogy stops at ownership: a list stores reusable values; a
> generator stores one advancing computation.

---

## 3. Generator functions retain local state between pulls

Any function containing `yield` is a generator function. Calling it returns a
generator object; its local variables and instruction position survive each
suspension:

```python
def batches(items: list[str], size: int):
    if size < 1:
        raise ValueError("size must be at least 1")

    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []

    if batch:
        yield batch


assert list(batches(["a", "b", "c"], 2)) == [["a", "b"], ["c"]]
```

The generator holds at most one batch, while `list(...)` at the call site
chooses to materialize all results. Laziness saves memory only while the
consumer also processes incrementally.

Use `yield from` when a generator merely delegates to another iterable:

```python
def all_ids(primary: list[str], archived: list[str]):
    yield from primary
    yield from archived
```

It forwards values and the deeper generator protocol; it is more than a shorter
nested `for` when callers use `send()`, `throw()`, or return values. Ordinary
backend pipelines rarely need those advanced controls.

---

## 4. Early exit makes cleanup the caller's responsibility

A generator's `finally` block runs when it exhausts, raises, or is explicitly
closed. If the consumer breaks early and keeps the generator alive, exhaustion
never happens and the resource can remain open:

```python
from collections.abc import Iterator
from pathlib import Path


def read_lines(path: Path) -> Iterator[str]:
    handle = path.open(encoding="utf-8")
    try:
        for line in handle:
            yield line.rstrip("\n")
    finally:
        handle.close()
```

Own early closure with `contextlib.closing`:

```python
from contextlib import closing


with closing(read_lines(Path("events.log"))) as lines:
    for line in lines:
        if line == "STOP":
            break
```

Leaving the `with` calls `lines.close()`, which resumes the generator with
`GeneratorExit` so its `finally` block closes the file. Do not rely on garbage
collection timing for resource cleanup.

For a simple file, `with path.open(...)` directly is clearer. A resource-owning
generator earns its complexity when it also performs a useful lazy
transformation and the caller has an explicit close boundary.

---

## 5. Async generators add waiting, not parallelism

An `async def` containing `yield` creates an **async generator**. The consumer
uses `async for`; each pull may await I/O before producing the next value:

```python
import asyncio
from collections.abc import AsyncIterator


async def events() -> AsyncIterator[str]:
    for event in ("accepted", "stored"):
        await asyncio.sleep(0)
        yield event


async def main() -> None:
    async for event in events():
        print(event)


asyncio.run(main())
```

This prints `accepted` and then `stored`. It does not run multiple iterations
concurrently; it gives the event loop a suspension point while the producer
waits.

When the consumer may break early, `contextlib.aclosing` provides deterministic
async cleanup in the same task context:

```python
from contextlib import aclosing


async with aclosing(events()) as stream:
    async for event in stream:
        if event == "accepted":
            break
```

This matters when the generator owns an HTTP response, database cursor, or
subscription whose `finally` block awaits release work.

---

## 6. Type the capability the caller receives

Use abstract interfaces from `collections.abc` in signatures:

| Type | Caller can do | Reusable? |
|---|---|---|
| `Iterable[T]` | start a `for` loop | Not guaranteed |
| `Iterator[T]` | call `next()` and continue one cursor | No |
| `AsyncIterable[T]` | start an `async for` loop | Not guaranteed |
| `AsyncIterator[T]` | pull with `anext()` and continue one async cursor | No |

A generator function normally returns `Iterator[T]`; an async generator
function returns `AsyncIterator[T]`. Accept `Iterable[T]` when your function
only loops, because that contract also admits lists, tuples, sets, and custom
iterables.

---

## 7. What breaks first, and when not to use laziness

⚠️ The first common failure is accidental double consumption: a debug statement
calls `list(stream)`, then production code receives an empty iterator. Inspect
or tee a stream deliberately; do not consume it merely to log it.

⚠️ A generator can hold a database cursor, response, or file open across every
suspension. Slow consumers therefore extend transactions and occupy pool slots.
Measure the complete consumption lifetime, not just the time spent producing
one item.

Do not use a generator when callers need random access, a known length, repeated
passes, or an immutable snapshot. Materialize a tuple/list at the boundary. Do
not use one generator to coordinate several concurrent consumers; use an async
queue or broker with an explicit fan-out and backpressure policy.

---

## 8. Prove suspension, exhaustion, and cleanup

This check exercises the three behaviors that most often surprise readers:

```python
events: list[str] = []


def stream():
    try:
        for value in (1, 2):
            events.append(f"produce:{value}")
            yield value
    finally:
        events.append("closed")


cursor = stream()
assert events == []
assert next(cursor) == 1
assert events == ["produce:1"]
cursor.close()
assert events == ["produce:1", "closed"]
assert list(cursor) == []
print("generator lifecycle verified")
```

**Success signal:** the script prints `generator lifecycle verified`. If
`closed` is missing, the consumer abandoned the generator without closing it;
if a second value appears before the first `next()`, the producer was
materialized rather than lazy.

For the language-level details, see [Python yield expressions](https://docs.python.org/3/reference/expressions.html#yieldexpr), checked 2026-09-10.

---

**Next**: [Context Managers — setup, teardown, and resource lifetimes](context_managers.md)
