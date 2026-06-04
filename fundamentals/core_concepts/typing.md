# Python Typing

> Type hints are not enforced at runtime by Python itself — they exist for readers, IDEs, and type checkers (mypy, pyright). In this corpus Pydantic and FastAPI read them at runtime to derive validation and schemas, so on the backend they do real work beyond documentation. This guide covers the parts you actually hit writing FastAPI / SQLAlchemy / data code.

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

Collections — **prefer the built-in generic syntax** (PEP 585, Python 3.9+):

```python
names: list[str] = []
index: dict[str, int] = {}
pair: tuple[int, str] = (1, "x")
items: set[int] = set()
```

Old-style `List[str]`, `Dict[str, int]` from `typing` still work but are legacy. Use them only if you support Python 3.8 or earlier.

---

## 2. Optional — User Preference for This Corpus

> ⚠️ **This codebase prefers `Optional[X]` over `X | None`.** Both are semantically identical in Python 3.10+, but `Optional` reads as "this can be missing" more clearly, especially in function signatures where `X | None` can visually fuse with adjacent punctuation.

```python
from typing import Optional

def lookup(user_id: int) -> Optional[User]:        # ✅ preferred in this repo
    ...

def lookup(user_id: int) -> User | None:           # works, not the convention here
    ...
```

Both mean "returns a `User` or `None`". Pick the first in new code.

**`Optional[X]` does not make a parameter optional.** It only says the type can be `None`. To make the argument omittable, also give it a default:

```python
def greet(name: Optional[str]):            # required; caller must pass name or None
    ...

def greet(name: Optional[str] = None):     # optional; defaults to None
    ...
```

---

## 3. Union — "One of these types"

```python
from typing import Union

def parse(x: Union[int, str]) -> int:
    return int(x)
```

In Python 3.10+ the pipe syntax `int | str` is equivalent. `Optional[X]` is literally `Union[X, None]`.

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

Use `total=False` for dicts where some keys may be absent:

```python
class Partial(TypedDict, total=False):
    a: int
    b: int
```

---

## 6. `TypeVar` — Generic Functions and Classes

Use when the function/class is parametric in some type and you want the return type to match the input type.

```python
from typing import Optional, TypeVar

T = TypeVar("T")


def first(items: list[T]) -> Optional[T]:
    return items[0] if items else None


x: Optional[int] = first([1, 2, 3])    # T is int
y: Optional[str] = first(["a", "b"])   # T is str
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

Python 3.12+ has a cleaner PEP 695 syntax: `def first[T](items: list[T]) -> Optional[T]: ...`. Both forms work; the `TypeVar` form is more portable.

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

An `httpx.AsyncClient` has `.aclose()`, so it satisfies `SupportsClose` even though it doesn't inherit anything special. This is the idiomatic Python alternative to ABC-based interfaces.

Use Protocol when:
- You want to accept multiple unrelated third-party types that share a shape.
- You're writing a library and don't want to force inheritance on users.
- You're mocking in tests and want the mock to type-check without faking a full class.

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

## 9. `Final` and `ClassVar`

```python
from typing import Final, ClassVar


API_VERSION: Final[str] = "v1"    # reassigning is a type error


class Service:
    max_retries: ClassVar[int] = 3   # class-level, not instance field
```

`Final` is a promise, not a runtime constant. `ClassVar` matters for Pydantic and dataclasses — both treat annotated class-level names as instance fields unless you mark them `ClassVar`.

---

## 10. Where This Plays with Pydantic and FastAPI

| Construct | FastAPI behavior |
|-----------|------------------|
| `x: int` | Required query/body param (depending on where it appears) |
| `x: int = 0` | Optional, defaults to 0 |
| `x: Optional[int] = None` | Optional, nullable |
| `x: Literal["a", "b"]` | Validated enum; shows in OpenAPI as enum |
| `x: list[Item]` | Expects a JSON array of items |
| `x: dict[str, int]` | Expects an object with string keys and int values |
| `status: Annotated[str, Field(pattern="^...$")]` | Validated string |

FastAPI uses the annotations at route-registration time to build OpenAPI and routing. Missing or wrong annotations silently degrade validation — a parameter typed `Any` or unannotated gets no validation.

---

## 11. Common Mistakes

- **Writing `Optional[X]` when you mean "optional argument".** Add `= None` to make it omittable.
- **Annotating `list` or `dict` without type parameters.** `list` means `list[Any]` — no type checking. Always write `list[str]`, not bare `list`.
- **Forgetting to annotate return types.** Type checkers infer them but the annotation documents intent for readers. Be explicit for public functions.
- **Using `Any` to silence the type checker.** It works but disables checking for everything downstream. Prefer `object`, `Protocol`, or a real type.
- **Typing something as the concrete class when a Protocol would do.** Makes tests harder — you need a full fake.

---

## See also

- [context_managers.md](context_managers.md) — Protocol for `SupportsClose` pattern.
- [../fastapi/03_pydantic.md](../fastapi/03_pydantic.md) — how Pydantic consumes annotations.
- [../fastapi/01_http_and_parameter_mapping.md](../fastapi/01_http_and_parameter_mapping.md) — how FastAPI routes use annotations.
