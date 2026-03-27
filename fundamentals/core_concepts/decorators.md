# Python Decorators @ 
In Python, a **decorator** is a function that **wraps another function** to extend or modify its behavior **without changing its source code**.

Think of it as:

> "Take this function, add something before/after it, and give me back a new function."

---

## 1️⃣ Functions are first-class objects

This is the key idea behind decorators.

```python
def greet():
    return "Hello"

say_hi = greet      # no parentheses
print(say_hi())     # Hello
```

Functions can be:

* assigned to variables
* passed as arguments
* returned from other functions

---

## 2️⃣ A function that wraps another function

```python
def my_decorator(func):
    def wrapper():
        print("Before function runs")
        result = func()
        print("After function runs")
        return result
    return wrapper
```

Usage **without `@` syntax**:

```python
def say_hello():
    print("Hello!")

say_hello = my_decorator(say_hello)
say_hello()
```

Output:

```
Before function runs
Hello!
After function runs
```

What happened:

1. `say_hello` is passed to `my_decorator`
2. `wrapper` replaces `say_hello`
3. Calling `say_hello()` actually calls `wrapper()`

---

## 3️⃣ The `@decorator` syntax (clean version)

This is just **syntax sugar**.

```python
@my_decorator
def say_hello():
    print("Hello!")
```

Equivalent to:

```python
say_hello = my_decorator(say_hello)
```

---

## 4️⃣ Decorators with arguments

Most real functions have arguments, so wrappers usually look like this:

```python
def my_decorator(func):
    def wrapper(*args, **kwargs):
        print("Before")
        result = func(*args, **kwargs)
        print("After")
        return result
    return wrapper
```

Now it works for **any function**.

---

## 5️⃣ Preserving function metadata (`functools.wraps`)

Without this, the wrapped function **loses its name and docstring**.

```python
from functools import wraps

def my_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper
```

Without `@wraps`:

```python
print(say_hello.__name__)  # wrapper ❌
```

With `@wraps`:

```python
print(say_hello.__name__)  # say_hello ✅
```

---

## 6️⃣ Decorators with their own parameters

Example: repeat a function `n` times

```python
def repeat(n):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for _ in range(n):
                result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator
```

Usage:

```python
@repeat(3)
def hello():
    print("Hi")

hello()
```

---

## 7️⃣ Real-world use cases

Decorators are commonly used for:

* 🔒 Authentication / authorization
* ⏱ Timing / logging
* 🔁 Retry logic
* 📦 Caching (`@lru_cache`)
* 🔍 Input validation

Example: timing decorator

```python
import time
from functools import wraps

def timer(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        print(f"{func.__name__} took {time.time() - start:.3f}s")
        return result
    return wrapper
```

---

## 🧠 Mental model (important)

```text
@decorator
def f():
    ...
```

Means:

```text
f = decorator(f)
```

And calling `f()` actually calls the **wrapper inside the decorator**.

---
