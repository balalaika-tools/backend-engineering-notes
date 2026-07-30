# Python Decorators: From Rebinding to Production Patterns

> **Who this is for**: Python developers who can define and call functions but find
> `@something` syntax mysterious. Read [typing.md](typing.md) first if
> `Callable`, `TypeVar`, or `ParamSpec` are unfamiliar.

A decorator is not a special kind of function call. It is a compact way to
**replace a function with the value returned by another callable**.

---

## 1. The One Transformation to Remember

This:

```python
@decorate
def calculate_total(subtotal: float) -> float:
    return subtotal * 1.2
```

means:

```python
def calculate_total(subtotal: float) -> float:
    return subtotal * 1.2


calculate_total = decorate(calculate_total)
```

Python creates the original function, passes that function object to `decorate`,
and assigns the returned object back to the name `calculate_total`.

```
original function
       │
       ▼
decorate(original)
       │
       ▼
returned callable ─── assigned to calculate_total
```

After decoration, the name usually refers to a **wrapper**, not directly to the
original function.

> **Key insight**: `@decorate` is rebinding syntax. Start by mentally expanding
> it to `function = decorate(function)`.

### Names carry no meaning to Python

The names you will see throughout this note — `decorate`, `register`, `wrapper`
— are conventions, not keywords. Python has no rule that a decorator's inner
function must be called `wrapper`, and no rule that it must return a wrapper at
all. This works identically:

```python
def command(name):
    def banana(operation):        # any name; `register` is just clearer
        COMMANDS[name] = operation
        return operation

    return banana
```

The only rule is the one above: whatever the callable returns becomes the new
value of the decorated name. Everything else in this note follows from that.

### Three shapes, one rule

Almost every decorator you write is one of three shapes. They are not different
language features — they differ only in **what the callable returns**.

| Shape | Example | Returns | Effect on calls | Built in |
|-------|---------|---------|-----------------|----------|
| **Wrapper** | `@measure` | a new function that calls the original | behavior added around every call | §3–§6 |
| **Factory** | `@repeat(times=3)` | a decorator, which then returns something | one extra definition-time layer that captures configuration | §7 |
| **Passthrough** | `@command("health")` | the original function, unchanged | none — the whole point is a side effect at import time | §10 |

One clarification that saves confusion later: **the factory shape is orthogonal
to the other two.** Arguments in the `@` line tell you only that an extra layer
exists, never what the innermost layer does. `@repeat(times=3)` is a factory that
ends in a wrapper; `@command("health")` is a factory that ends in a passthrough.
So when you read a decorator, count the layers *and* check the innermost return
separately.

Section 13 covers the shapes beyond these three — descriptors, class-based
decorators, class decorators, and dual-form decorators — once the core mechanics
are in place.

---

## 2. Why Functions Can Be Decorated

Python functions are objects. A function can be assigned to another name,
passed as an argument, and returned from another function:

```python
from collections.abc import Callable


def greet(name: str) -> str:
    return f"Hello, {name}"


def run_twice(operation: Callable[[str], str], value: str) -> tuple[str, str]:
    return operation(value), operation(value)


say_hello = greet                 # assign the function; do not call it yet
result = run_twice(say_hello, "Mina")

assert result == ("Hello, Mina", "Hello, Mina")
```

Compare the two expressions:

| Expression | Meaning |
|------------|---------|
| `greet` | The function object itself |
| `greet("Mina")` | Call the function now and produce its result |

A decorator receives the first one: the function object.

---

## 3. Build a Decorator by Hand

Suppose every call to `create_invoice()` should be announced. Keeping that
cross-cutting behavior outside the business function leaves the function focused
on invoices.

```python
from collections.abc import Callable


def announce(operation: Callable[[], str]) -> Callable[[], str]:
    def wrapper() -> str:
        print(f"starting {operation.__name__}")
        result = operation()
        print(f"finished {operation.__name__}")
        return result

    return wrapper


def create_invoice() -> str:
    return "invoice-1042"


create_invoice = announce(create_invoice)
invoice_id = create_invoice()
```

Output:

```text
starting create_invoice
finished create_invoice
```

There are two different moments here:

```
MODULE LOAD / DEFINITION TIME             CALL TIME

create original function
        │
        ▼
announce(original)
        │
        ├── create wrapper
        └── return wrapper
        │
        ▼
create_invoice now names wrapper ───────► wrapper()
                                             │
                                             ├── before behavior
                                             ├── original()
                                             └── after behavior
```

`announce()` runs once when the definition is executed. `wrapper()` runs every
time `create_invoice()` is called.

The wrapper can still call `operation` because it forms a **closure**: it
remembers the surrounding `operation` variable after `announce()` has returned.

---

## 4. Replace the Manual Rebinding with `@`

Now use the exact same decorator with the shorter syntax:

```python
@announce
def create_invoice() -> str:
    return "invoice-1042"
```

The behavior is unchanged. The `@` form is useful because the decoration is
visible directly above the function and cannot be separated from it by later
code.

A wrapper must return the original result unless the decorator deliberately
changes the function's contract:

```python
def broken_decorator(operation):
    def wrapper():
        operation()
        # ❌ The result is lost, so callers receive None.

    return wrapper
```

> **Rule**: a behavior-only decorator should preserve the wrapped function's
> inputs, return value, and exceptions.

---

## 5. Support Real Function Signatures

A zero-argument wrapper only works for zero-argument functions. `*args` collects
positional arguments and `**kwargs` collects keyword arguments so the wrapper can
forward any call unchanged.

```python
import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)


def measure(operation: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(operation)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        succeeded = False
        try:
            result = operation(*args, **kwargs)
            succeeded = True
            return result
        finally:
            duration_ms = (time.perf_counter() - started) * 1_000
            logger.info(
                "operation finished",
                extra={
                    "operation": operation.__qualname__,
                    "duration_ms": round(duration_ms, 2),
                    "outcome": "success" if succeeded else "error",
                },
            )

    return wrapper


@measure
def calculate_shipping(weight_kg: float, *, express: bool = False) -> float:
    multiplier = 2.0 if express else 1.0
    return round(weight_kg * 1.25 * multiplier, 2)


assert calculate_shipping(4.0, express=True) == 10.0
```

The `finally` block makes the timing log run on success and failure. The
exception is not caught, so it still propagates to the caller.

`Callable[..., Any]` is easy to read but discards the precise signature for a
type checker. A reusable library decorator can preserve it with `ParamSpec` and
`TypeVar`:

```python
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def transparent(operation: Callable[P, R]) -> Callable[P, R]:
    @wraps(operation)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        return operation(*args, **kwargs)

    return wrapper
```

`P` represents the complete parameter list; `R` represents the return type.
The type checker therefore sees the decorated callable with the same contract as
the original.

---

## 6. `functools.wraps` Preserves the Public Identity

Without `@wraps`, introspection sees the wrapper:

```python
def noisy(operation):
    def wrapper(*args, **kwargs):
        return operation(*args, **kwargs)

    return wrapper


@noisy
def find_order(order_id: int) -> str:
    """Load one order."""
    return f"order-{order_id}"


assert find_order.__name__ == "wrapper"
assert find_order.__doc__ is None
```

That is not cosmetic. Debuggers, tracebacks, documentation generators,
dependency-injection frameworks, and route-registration tools inspect function
metadata.

```python
from functools import wraps


def noisy(operation):
    @wraps(operation)
    def wrapper(*args, **kwargs):
        return operation(*args, **kwargs)

    return wrapper


@noisy
def find_order(order_id: int) -> str:
    """Load one order."""
    return f"order-{order_id}"
```

`@wraps(operation)` copies important metadata and adds `__wrapped__`, a reference
to the original function:

```python
assert find_order.__name__ == "find_order"
assert find_order.__wrapped__(42) == "order-42"
```

Tools such as `inspect.signature()` follow `__wrapped__` by default, which is why
the visible signature remains useful.

> **Rule**: use `@wraps` on every wrapper unless hiding the original callable is
> an intentional part of the API.

---

## 7. Decorators with Configuration Need Three Layers

`@retry(attempts=3)` is not passed the decorated function immediately.
`retry(attempts=3)` runs first and must return the actual decorator.

```python
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def repeat(times: int) -> Callable[[Callable[P, R]], Callable[P, R]]:
    if times < 1:
        raise ValueError("times must be at least 1")

    def decorate(operation: Callable[P, R]) -> Callable[P, R]:
        @wraps(operation)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            result: R
            for _ in range(times):
                result = operation(*args, **kwargs)
            return result

        return wrapper

    return decorate


@repeat(times=3)
def refresh_cache() -> str:
    print("refreshing")
    return "ready"


assert refresh_cache() == "ready"
```

Mentally expand it in two steps:

```python
configured_decorator = repeat(times=3)
refresh_cache = configured_decorator(refresh_cache)
```

or as one line:

```python
refresh_cache = repeat(times=3)(refresh_cache)
```

`repeat` is a **decorator factory**: it is not itself a decorator, it builds
one. `decorate` is the actual decorator — it is the layer that receives the
function.

The three layers have separate jobs:

| Layer | Runs | Job |
|-------|------|-----|
| `repeat(times=3)` | At definition time | Capture decorator configuration |
| `decorate(operation)` | At definition time | Capture the function |
| `wrapper(...)` | On every call | Apply behavior and call the function |

Validate decorator configuration in the outer function so invalid settings fail
at import/startup, not on the first production request.

### What runs when: prove it with prints

A frequent misreading is that configuration is the thing that makes a decorator
run code before the wrapper exists. It is not. **Both** forms run code at
definition time; the configured form simply has one extra call in that phase.

```python
def announce(operation):
    print("decorating")           # definition time — runs once

    def wrapper():
        print("before")           # call time — runs per call
        result = operation()
        print("after")
        return result

    return wrapper


@announce
def work() -> str:
    return "done"
```

Executing the module prints `decorating` immediately. Nothing else prints until
you call `work()`.

Now the configured form, with each layer labelled:

```python
def repeat(times: int):
    print("1. configuration received")

    def decorate(operation):
        print("2. function received")

        @wraps(operation)
        def wrapper():
            print("3. wrapper called")
            for _ in range(times):
                operation()

        return wrapper

    return decorate


@repeat(3)
def hello() -> None:
    print("hello")
```

Import time prints:

```text
1. configuration received
2. function received
```

Only `hello()` prints:

```text
3. wrapper called
hello
hello
hello
```

So the difference is one extra definition-time call, not the presence of
definition-time work:

```text
No configuration:
  decorator(function) → wrapper
  wrapper()           → runtime behavior

With configuration:
  factory(config)     → decorator
  decorator(function) → wrapper
  wrapper()           → runtime behavior
```

This matters in practice because anything you do in the factory or the decorator
body happens once, at import, before your app serves a request: config
validation, metric registration, and route table entries belong there; anything
that depends on a specific call belongs in `wrapper`.

---

## 8. Decorating `async def` Requires an Async Wrapper

A synchronous wrapper around an async function only receives a **coroutine
object**. It does not wait for the operation to finish.

```python
# ❌ The "finished" log happens before fetch_order has actually run.
def broken_trace(operation):
    @wraps(operation)
    def wrapper(*args, **kwargs):
        result = operation(*args, **kwargs)  # result is a coroutine object
        logger.info("finished")
        return result

    return wrapper
```

Use `async def` and `await`:

```python
import logging
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)


def measure_async(
    operation: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    @wraps(operation)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        started = time.perf_counter()
        succeeded = False
        try:
            result = await operation(*args, **kwargs)
            succeeded = True
            return result
        finally:
            duration_ms = (time.perf_counter() - started) * 1_000
            logger.info(
                "async operation finished",
                extra={
                    "operation": operation.__qualname__,
                    "duration_ms": round(duration_ms, 2),
                    "outcome": "success" if succeeded else "error",
                },
            )

    return wrapper
```

Keep separate sync and async decorators unless a single combined API clearly
earns its extra branching and typing complexity.

---

## 9. Stacked Decorators Apply Bottom-Up

```python
@outer
@inner
def handle_request() -> None:
    ...
```

is:

```python
handle_request = outer(inner(handle_request))
```

Decoration is bottom-up, but a call enters the outer wrapper first:

```
call handle_request()
        │
        ▼
outer wrapper: before
        │
        ▼
inner wrapper: before
        │
        ▼
original function
        │
        ▼
inner wrapper: after
        │
        ▼
outer wrapper: after
```

Expand a stack one line at a time until only plain calls remain:

```python
original = handle_request
inner_wrapper = inner(original)
outer_wrapper = outer(inner_wrapper)
handle_request = outer_wrapper
```

A runnable version, using one configured decorator twice:

```python
def trace(label: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(operation: Callable[P, R]) -> Callable[P, R]:
        @wraps(operation)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            print(f"{label}: before")
            result = operation(*args, **kwargs)
            print(f"{label}: after")
            return result

        return wrapper

    return decorate


@trace("OUTER")
@trace("INNER")
def process_order(order_id: int) -> str:
    print(f"processing order {order_id}")
    return "completed"


process_order(42)
```

Output:

```text
OUTER: before
INNER: before
processing order 42
INNER: after
OUTER: after
```

Order changes behavior. Authentication should usually be outside expensive
timing, caching, or transaction work when unauthorized calls must not trigger
that work:

```python
@require_authenticated_user
@cache_result
def load_dashboard(user_id: int) -> Dashboard:
    ...
```

Read a stack from the function upward: the decorator closest to `def` wraps
first.

### Order matters most when one decorator captures a reference

With two wrappers, order changes the sequence of behavior. With a **registration**
decorator (Section 10) in the stack, order changes *which object* gets registered
— and that is a real bug source. Given:

```python
COMMANDS: dict[str, Callable[[], str]] = {}


def command(name: str):
    def register(operation):
        COMMANDS[name] = operation
        return operation          # returns the same object it received

    return register


def log_call(operation):
    @wraps(operation)
    def wrapper():
        print("starting")
        result = operation()
        print("finished")
        return result

    return wrapper
```

**Case A — `command` outermost (usually what you want):**

```python
@command("health")
@log_call
def health_check() -> str:
    return "ok"

# health_check = command("health")(log_call(health_check))
```

`log_call` builds the wrapper first, so `command` stores the wrapper.
`COMMANDS["health"]` and the module-level `health_check` are the same object, and
calling either one logs.

**Case B — `command` innermost (a silent trap):**

```python
@log_call
@command("health")
def health_check() -> str:
    return "ok"

# health_check = log_call(command("health")(health_check))
```

`command` runs first and stores the *undecorated* function. Then the name
`health_check` is rebound to the logging wrapper. So `health_check()` logs, but
the dispatcher calling `COMMANDS["health"]()` gets the raw function and **no
logging**. Nothing raises; the behavior is just quietly missing on the path that
matters.

The rule: put registration decorators outermost so they capture the fully
decorated object. If you dispatch through a registry, assert what you registered:

```python
assert COMMANDS["health"] is health_check
```

---

## 10. Methods and Decorators That Do Not Wrap

The general `*args, **kwargs` pattern also works on instance methods because
`self` is simply the first positional argument:

```python
class BillingService:
    @measure
    def charge(self, invoice_id: str, amount: float) -> str:
        return f"charged:{invoice_id}:{amount}"
```

When combining a custom function decorator with `@classmethod`, let the custom
decorator receive the plain function and make `@classmethod` outermost:

```python
class BillingService:
    @classmethod
    @measure
    def from_environment(cls) -> "BillingService":
        return cls()
```

Not every decorator returns a wrapper. A registration decorator can record the
function and return it unchanged:

```python
from collections.abc import Callable

COMMANDS: dict[str, Callable[[], str]] = {}


def command(
    name: str,
) -> Callable[[Callable[[], str]], Callable[[], str]]:
    def register(operation: Callable[[], str]) -> Callable[[], str]:
        if name in COMMANDS:
            raise ValueError(f"duplicate command: {name}")
        COMMANDS[name] = operation
        return operation

    return register


@command("health")
def health_check() -> str:
    return "ok"


assert COMMANDS["health"]() == "ok"
assert COMMANDS["health"] is health_check      # same object, not a wrapper
```

Step by step, `@command("health")` expands to:

```python
register = command("health")          # factory returns the decorator
health_check = register(health_check) # register stores it and hands it back
```

This is a genuine decorator even though no wrapper exists anywhere in it. It
satisfies the only requirement — take the function, return the object to bind —
and it changes nothing about how the function behaves when called. Its entire
effect is the side effect: the function is now reachable through `COMMANDS`.

This registration happens when the module is imported, which has a practical
consequence: a command whose module is never imported is never registered. If a
registry looks empty, suspect import wiring before suspecting the decorator.
Framework route decorators (`@app.get(...)`) use the same broad idea: attach or
register metadata at definition time.

---

## 11. Common Production Mistakes

### Doing per-call work at definition time

```python
# ❌ check_permission() runs once during import.
@require_permission(check_permission())
def delete_order() -> None:
    ...
```

Decorator arguments are evaluated when the `def` statement executes. Pass
configuration to the decorator; read request-specific state inside the wrapper
or accept it as a function argument.

### Keeping unsafe shared state in a closure

```python
# ❌ Concurrent calls mutate the same counter without synchronization.
def count_calls(operation):
    calls = 0

    @wraps(operation)
    def wrapper(*args, **kwargs):
        nonlocal calls
        calls += 1
        return operation(*args, **kwargs)

    return wrapper
```

The closure belongs to the decorated function, not to one request. Use an
external metrics system or synchronization when state is shared across calls.

### Catching more than the decorator can handle

```python
# ❌ Converts programming bugs and cancellations into fake success.
def unsafe_call(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except Exception:
        return None
```

A decorator follows the same exception rules as ordinary code: catch only
errors it can recover from, and otherwise let them propagate.

### Hiding dependencies

If a decorator secretly opens a database session, reads the current tenant,
commits a transaction, and changes errors, the function signature no longer
explains what the function needs or does. Prefer explicit parameters,
dependency injection, middleware, or a context manager when lifecycle or order
must be visible.

---

## 12. Choosing and Testing Decorators

Use a decorator when:

- the same behavior applies to many callables;
- the behavior surrounds a complete call;
- callers should always receive the enhanced behavior;
- the wrapper can preserve a clear input/output contract.

Prefer another tool when:

| Need | Better fit |
|------|------------|
| Setup and guaranteed teardown around a block | [Context manager](context_managers.md) |
| Behavior around every HTTP request | Middleware |
| An explicit service dependency | Function parameter / dependency injection |
| One readable call at one call site | A normal helper function |
| Object construction or shared object state | A class |

Test both the public decorated behavior and, when useful, the undecorated unit:

```python
def test_decorator_adds_behavior() -> None:
    assert calculate_shipping(4.0, express=True) == 10.0


def test_original_calculation_in_isolation() -> None:
    assert calculate_shipping.__wrapped__(4.0, express=True) == 10.0
```

The second test is possible because `@wraps` installed `__wrapped__`. Do not use
it to bypass security or transaction decorators in application code.

---

## 13. Reading Any Unfamiliar Decorator

Do not try to classify a decorator by the names inside it. Use two mechanical
steps.

**Step 1 — expand until only calls remain.**

```python
@A
@B(config=1)
def operation():
    ...
```

becomes:

```python
operation = A(B(config=1)(operation))
```

**Step 2 — ask what each layer returns.**

| What you see | What it means |
|--------------|---------------|
| Called with arguments in the `@` line (`@repeat(3)`) | Outer callable is a factory; the decorator is the layer below it |
| No arguments in the `@` line (`@measure`) | That callable *is* the decorator |
| Returns a new inner function | Call behavior changes — arguments, result, and exceptions now pass through the wrapper |
| Returns the function it received | Registration or metadata attachment; call behavior is unchanged |
| Returns something else entirely | The name now refers to that object — e.g. `@property` yields a descriptor, not a function |

Since Python 3.9 the `@` line accepts any expression, so you may also meet
`@registry["health"]` or `@handlers[0].wrap` — one more reason to expand rather
than pattern-match on names.

### Beyond the three shapes

The three shapes in Section 1 cover most application code. These five turn up in
libraries and framework internals.

**4. Metadata attachment.** A passthrough that tags the function instead of
registering it. The tag is read later by a test runner, serializer, or router:

```python
def deprecated(reason: str):
    def attach(operation):
        operation.__deprecated__ = reason   # mutate, then hand back unchanged
        return operation

    return attach
```

Cheap and non-invasive: call behavior is untouched, so nothing can break at
runtime. The cost is that the tag is invisible unless something looks for it.

**5. Descriptor replacement.** `@property`, `@staticmethod`, `@classmethod`, and
`functools.cached_property` do not return functions at all — they return
descriptor objects, and attribute access on the class triggers the behavior.

```python
class Invoice:
    @property
    def total(self) -> float:
        return 42.0


assert Invoice().total == 42.0          # no parentheses — it is not a method
```

The rule still holds (`total = property(total)`), but the consequence is that the
name is no longer callable. This is why `@classmethod` must be outermost when
stacked (Section 10): a function decorator handed a `classmethod` object would
receive a descriptor, not a function.

**6. Class-based decorator.** Instead of a closure, use an object: `__init__`
receives the function, `__call__` runs per call. Reach for this when the
decorator needs real state or an inspectable API.

```python
from functools import wraps


class CountCalls:
    def __init__(self, operation):
        self.operation = operation
        self.calls = 0
        wraps(operation)(self)      # copies metadata onto the instance

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.operation(*args, **kwargs)


@CountCalls
def ping() -> str:
    return "pong"


ping(); ping()
assert ping.calls == 2
assert ping.__name__ == "ping"
```

⚠️ **The trap**: this breaks on methods. A plain function stored as a class
attribute implements the descriptor protocol and binds `self`; your instance does
not, so `self` is never passed:

```python
class BillingService:
    @CountCalls
    def charge(self, amount: float) -> float:
        return amount


BillingService().charge(5.0)
# TypeError: charge() missing 1 required positional argument: 'amount'
#   — 5.0 was bound to `self`, because nothing bound the instance
```

Fixing it means implementing `__get__` to return a bound partial. Unless you need
the object, a closure-based decorator is the simpler default — and the shared
counter above has the concurrency problem described in Section 11.

**7. Class decorators.** The decorated object does not have to be a function.
`@dataclass` and `@functools.total_ordering` take a class and return one, usually
the same class with attributes added:

```python
from dataclasses import dataclass


@dataclass          # Invoice = dataclass(Invoice)
class Invoice:
    invoice_id: str
    amount: float
```

Same transformation, different input type. Prefer a class decorator over
metaclasses or inheritance when you only need to add or adjust class attributes.

**8. Dual-form decorator.** Usable both bare and configured. Supporting both
means detecting which call you received:

```python
def retry(operation=None, *, attempts: int = 3):
    def decorate(op):
        @wraps(op)
        def wrapper(*args, **kwargs):
            ...
            return op(*args, **kwargs)

        return wrapper

    if operation is None:      # called as @retry(attempts=5)
        return decorate
    return decorate(operation)  # called as @retry
```

The keyword-only `*` is what makes this safe: it prevents `@retry(5)` from being
read as "decorate the function `5`". Worth the branch in a public library where
both spellings are expected; in application code, pick one form and stay
consistent.

That is the whole catalogue, because the rule was never "a decorator has a
wrapper". The rule is:

```python
decorated_name = decorator(original_object)
```

A wrapper is simply the most common way to satisfy it when you want behavior
before and after each call.

---

> **Mental model**:
>
> 1. `@decorator` means `name = decorator(name)`.
> 2. Decoration runs once at definition time; a configured decorator just adds
>    one more definition-time call to capture the configuration.
> 3. The returned wrapper runs on every call — if a wrapper is returned at all.
> 4. Inner names are arbitrary. Classify a decorator by what each layer returns,
>    not by what it is called.
> 5. A closure lets the wrapper remember the original function and configuration.
> 6. `@wraps`, correct argument forwarding, async awareness, and exception
>    transparency keep the contract intact.

---

**Next**: [Exceptions — propagation, recovery, and translation](exceptions.md)
