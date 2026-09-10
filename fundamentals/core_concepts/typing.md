# Python Typing

> **Who this is for**: Python developers who can read basic annotations and want
> to design useful contracts for backend code without turning every signature
> into a type-system puzzle.

> Type hints are not enforced at runtime by Python itself — they exist for readers, IDEs, and type checkers (mypy, pyright). In this corpus Pydantic and FastAPI read them at runtime to derive validation and schemas, so on the backend they do real work beyond documentation. This guide covers the parts you actually hit writing FastAPI / SQLAlchemy / data code.

To see that distinction instead of taking it on faith, start with
[Run a Type Checker as an Engineering Feedback Loop](typing_workflow.md): one command accepts a
valid contract and points at the exact invalid call after one argument changes.

> **Key insight**: a type hint is a contract for tools and readers. It becomes
> runtime behavior only when some library explicitly inspects it.

---

## 1. Basic Annotations

```python
def add(a: int, b: int) -> int:
    return a + b


name: str = "alice"
count: int = 0
ratio: float = 1.5
flag: bool = True
blob: bytes = b"\x00"
```

Collections — **prefer the built-in generic syntax** standardized by Python Enhancement Proposal
(PEP) 585 in Python 3.9+:

```python
names: list[str] = []
index: dict[str, int] = {}
pair: tuple[int, str] = (1, "x")
items: set[int] = set()
```

Old-style `List[str]`, `Dict[str, int]` from `typing` still work but are legacy. Use them only if you support Python 3.8 or earlier.

---

## 2. Nullable Values — Prefer `X | None`

For this Python 3.11+ corpus, prefer the modern union spelling `X | None`.
`Optional[X]` remains fully valid and means exactly the same thing, so expect to
see it in older code and libraries.

```python
def lookup(user_id: int) -> User | None:
    ...

def greet(name: str | None):            # required; caller must pass name or None
    ...

def greet(name: str | None = None):     # omittable; defaults to None
    ...
```

The annotation controls which values are accepted. The default controls whether
the caller may omit the argument; “nullable” and “omittable” are separate
decisions.

---

## 3. Union — "One of these types"

```python
def parse(x: int | str) -> int:
    return int(x)
```

The older `Union[int, str]` spelling is equivalent. `Optional[X]` is the older
shorthand for `X | None`.

For more than ~3 members, a `Union` is often a code smell — consider splitting into separate functions or using a protocol.

---

## 4. `Literal` — Exact Values

```python
from typing import Literal

Environment = Literal["dev", "staging", "prod"]


def configure(env: Environment) -> None: ...


configure("prod")      # OK
configure("production")  # type checker error
```

Pydantic validates `Literal` fields against the exact allowed values at runtime. Use `Literal` instead of `str` whenever the set of valid values is small and closed. It gives you IDE autocomplete, mypy checks, and runtime validation for free.

---

## 5. `TypedDict` — Structured Dicts

For dicts with known keys (e.g. JSON returned from an upstream API where you don't want a full Pydantic model):

```python
from typing import TypedDict


class Address(TypedDict):
    street: str
    city: str
    zip: str


addr: Address = {"street": "...", "city": "...", "zip": "..."}
```

TypedDict is for **type-checker-only** documentation. It is not validated at runtime. For actual validation from untrusted input, use Pydantic.

Use `NotRequired` when individual keys may be absent:

```python
from typing import NotRequired, TypedDict


class UserPatch(TypedDict):
    display_name: NotRequired[str]
    timezone: NotRequired[str]
    revision: int
```

`total=False` makes **every** key omittable. That is useful for a true partial
mapping, but it is too broad when some keys remain required:

```python
class PartialUser(TypedDict, total=False):
    display_name: str
    timezone: str
```

---

## 6. `TypeVar` — Generic Functions and Classes

Use when the function/class is parametric in some type and you want the return type to match the input type.

```python
from typing import TypeVar

T = TypeVar("T")


def first(items: list[T]) -> T | None:
    return items[0] if items else None


x: int | None = first([1, 2, 3])    # T is int
y: str | None = first(["a", "b"])  # T is str
```

With bounds (T must be a subclass of X):

```python
from typing import TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def parse_all(cls: type[ModelT], rows: list[dict]) -> list[ModelT]:
    return [cls.model_validate(r) for r in rows]


users: list[User] = parse_all(User, rows)   # ModelT is User
```

Python 3.12+ has the cleaner generic-function syntax introduced by PEP 695, where a function
declares type parameter `T` directly after its name. Both forms work; the `TypeVar` form is more
portable across supported Python versions.

---

## 7. `Protocol` — Structural Typing ("Duck Typing for Type Checkers")

A `Protocol` defines an interface by shape. Anything with the right methods satisfies it, **without inheriting**.

```python
from typing import Protocol


class SupportsClose(Protocol):
    async def aclose(self) -> None: ...


async def shutdown(resource: SupportsClose) -> None:
    await resource.aclose()
```

An `httpx.AsyncClient` has `.aclose()`, so it satisfies `SupportsClose` even though it doesn't inherit anything special. This is the idiomatic Python alternative to abstract base class (ABC) interfaces.

Use Protocol when:

- You want to accept multiple unrelated third-party types that share a shape.
- You're writing a library and don't want to force inheritance on users.
- You're mocking in tests and want the mock to type-check without faking a full class.

Protocols are static by default. `isinstance(value, SupportsClose)` raises
`TypeError` unless the protocol uses `@runtime_checkable`, and runtime-checkable
protocols only check that attributes exist—not their full signatures. Prefer
ordinary duck typing unless a runtime check is genuinely needed.

---

## 8. `Callable`

```python
from typing import Callable

# Callable[[arg1_type, arg2_type], return_type]
Handler = Callable[[Request], Response]


def register(handler: Handler) -> None: ...
```

For async callables, the return type is `Awaitable[X]`:

```python
from typing import Awaitable

AsyncHandler = Callable[[Request], Awaitable[Response]]
```

---

## 9. `ParamSpec` — Preserve a Callable's Parameters

`Callable[..., R]` preserves the return type but says nothing about the
parameters. A decorator or forwarding helper can use `ParamSpec` to keep the
complete signature:

```python
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def traced(operation: Callable[P, R]) -> Callable[P, R]:
    @wraps(operation)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        print(f"calling {operation.__qualname__}")
        return operation(*args, **kwargs)

    return wrapper
```

The type checker retains positional-only, keyword-only, variadic, and named
parameters represented by `P`. See
[Decorators](decorators.md#5-support-real-function-signatures) for the runtime
mechanics this annotation describes.

---

## 10. `Annotated` — Attach Metadata to a Type

`Annotated` keeps a normal type in its first position and adds metadata for
libraries:

```python
from typing import Annotated

from pydantic import Field

PageSize = Annotated[int, Field(ge=1, le=100)]


def list_orders(limit: PageSize = 20) -> list[str]:
    return []
```

A static type checker treats `PageSize` as `int`. Pydantic or FastAPI can inspect
the `Field` metadata to enforce bounds and generate a schema. Python itself still
does not validate a direct call such as `list_orders(10_000)`.

Use `Annotated` when metadata belongs to the type at a framework boundary.
Keep ordinary business functions explicit rather than assuming annotations
validate every call.

FastAPI uses this same mechanism for a second purpose: telling it *where* a route
parameter's value comes from in the HTTP request — not just how to validate it.

```python
from typing import Annotated

from fastapi import Header, Path, Query

def endpoint(
    user_id: Annotated[int, Path()],
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    token: Annotated[str, Header()] = ...,
): ...
```

`Path()`, `Query()`, `Header()`, `Cookie()`, `Body()`, `Form()`, and `File()` are
parameter-source markers — the second slot in `Annotated` — while the first slot
stays the plain type a checker sees. This is unrelated to `Depends()`: a
`Depends(...)` default means "call this function and inject its return value,"
not "read this value from part of the request." See
[../fastapi/01_http_and_parameter_mapping.md](../fastapi/01_http_and_parameter_mapping.md)
for the full resolution order FastAPI applies across path, query, header, cookie,
body, form, file, and dependency parameters.

---

## 11. `Final` and `ClassVar`

```python
from typing import Final, ClassVar


API_VERSION: Final[str] = "v1"    # reassigning is a type error


class Service:
    max_retries: ClassVar[int] = 3   # class-level, not instance field
```

`Final` is a promise, not a runtime constant. `ClassVar` matters for Pydantic and dataclasses — both treat annotated class-level names as instance fields unless you mark them `ClassVar`.

---

## 12. Where This Plays with Pydantic and FastAPI

| Construct                                          | FastAPI behavior                                          |
| -------------------------------------------------- | --------------------------------------------------------- |
| `x: int`                                         | Required query/body param (depending on where it appears) |
| `x: int = 0`                                     | Optional, defaults to 0                                   |
| `x: int | None = None`                           | Omittable and nullable                                    |
| `x: Literal["a", "b"]`                           | Validated enum; shows in OpenAPI as enum                  |
| `x: list[Item]`                                  | Expects a JSON array of items                             |
| `x: dict[str, int]`                              | Expects an object with string keys and int values         |
| `status: Annotated[str, Field(pattern="^...$")]` | Validated string                                          |

FastAPI uses annotations at route-registration time to build validation rules,
OpenAPI, and parameter behavior. A broad annotation such as `Any` gives it very
little to validate. An unannotated parameter is not equivalent to "accept
anything": FastAPI still applies its parameter-source rules, but lacks a precise
Python type contract. Make boundary annotations explicit.

---

## 13. Common Mistakes

- **Writing `X | None` when you mean "omittable argument".** Add `= None`; nullability alone does not provide a default.
- **Annotating `list` or `dict` without type parameters.** `list` means `list[Any]` — no type checking. Always write `list[str]`, not bare `list`.
- **Forgetting to annotate return types.** Type checkers infer them but the annotation documents intent for readers. Be explicit for public functions.
- **Using `Any` to silence the type checker.** It works but disables checking for everything downstream. Prefer `object`, `Protocol`, or a real type.
- **Typing something as the concrete class when a Protocol would do.** Makes tests harder — you need a full fake.
- **Assuming a type hint validates an ordinary call.** `def set_age(age: int)`
  still accepts `"12"` at runtime unless code or a framework validates it.
- **Catching type-checker errors with casts everywhere.** `cast()` changes the
  checker's view only; it performs no runtime conversion or check. Fix the source
  type or validate the data at the boundary.

---

## 14. Four Contracts That Prevent Backend Bugs

These are the next patterns to reach for after the basics. They solve different
problems; they are not decorations to add to every signature.

### Accept abstract inputs and return concrete results

A function that only iterates should not require a `list`. Accept the smallest
behavior the implementation needs, then return the concrete object callers
actually receive:

```python
from collections.abc import Iterable, Mapping


def active_ids(rows: Iterable[Mapping[str, object]]) -> list[str]:
    return [str(row["id"]) for row in rows if row.get("active") is True]
```

This accepts lists, tuples, generators, and database-result iterables without
giving up the useful promise that callers receive a reusable `list`.

### `NewType` stops IDs with the same runtime shape being swapped

User IDs and order IDs may both be strings, but passing one where the other is
expected is still a logic error:

```python
from typing import NewType

UserId = NewType("UserId", str)
OrderId = NewType("OrderId", str)


def load_order(order_id: OrderId) -> str:
    return str(order_id)


user_id = UserId("usr-42")
load_order(user_id)  # type checker error: UserId is not OrderId
```

`NewType` has almost no runtime behavior: `UserId("usr-42")` returns the same
string object. Validate untrusted ID formats separately.

### `@overload` preserves an input-dependent return type

A union return loses the relationship between a particular input and its
result. Overloads describe that relationship to the checker; one ordinary
implementation still runs at runtime:

```python
from typing import Literal, overload


@overload
def decode(raw: bytes, *, as_text: Literal[False] = False) -> bytes: ...


@overload
def decode(raw: bytes, *, as_text: Literal[True]) -> str: ...


def decode(raw: bytes, *, as_text: bool = False) -> bytes | str:
    return raw.decode("utf-8") if as_text else raw


binary = decode(b"ok")                    # inferred as bytes
text = decode(b"ok", as_text=True)        # inferred as str
```

Use overloads only when callers gain a meaningfully more precise result. If the
function genuinely returns a union regardless of its inputs, annotate the union.

### `TypeGuard` carries validated narrowing into the guarded branch

After checking every element in an untrusted list, an ordinary `bool` helper
does not tell a type checker what changed. `TypeGuard` makes the successful
branch explicit:

```python
from typing import TypeGuard


def is_str_list(value: list[object]) -> TypeGuard[list[str]]:
    return all(isinstance(item, str) for item in value)


def join_tags(value: list[object]) -> str:
    if not is_str_list(value):
        raise TypeError("every tag must be a string")
    return ",".join(value)  # narrowed to list[str] here
```

The predicate must be correct; a false claim makes the static model unsound.
Python 3.13 added `TypeIs`, which can narrow both branches when the narrowed type
is compatible with the input. Use `TypeGuard` for this repository's Python 3.11
baseline.

---

## 15. See Also

- [typing_workflow.md](typing_workflow.md) — configure and run mypy, then keep it enforced in CI.
- [iterators_and_generators.md](iterators_and_generators.md) — the `Iterable`/`Iterator` runtime protocol and lazy pipelines.
- [context_managers.md](context_managers.md) — Protocol for `SupportsClose` pattern.
- [../fastapi/03_pydantic.md](../fastapi/03_pydantic.md) — how Pydantic consumes annotations.
- [../fastapi/01_http_and_parameter_mapping.md](../fastapi/01_http_and_parameter_mapping.md) — how FastAPI routes use annotations.

---

**Next**: [Iterators, Generators, and Lazy Pipelines](iterators_and_generators.md)
