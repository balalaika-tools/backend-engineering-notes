
# Python Exceptions — A Practical Engineering Guide

This guide explains **how Python exceptions actually behave** and how to use them safely in real systems.

---

## 1. What an Exception Is

An exception is a **non-local control transfer**:

* execution **stops immediately**
* Python **unwinds the call stack**
* it searches upward for a matching `except`
* if none is found → program terminates

If an exception is **caught and not re-raised**, Python assumes **success**.

---

## 2. `try / except / else / finally`

```python
try:
    risky()
except ValueError:
    handle_error()
else:
    on_success()
finally:
    cleanup()
```

* `except` runs only on failure
* `else` runs only on success
* `finally` always runs (cleanup only)

### Prefer Context Managers

```python
with open("data.txt") as f:
    data = f.read()
```

Use `finally` only when `with` is unavailable.

---

## 3. Catching Exceptions Correctly

### Specific first, general last

```python
try:
    work()
except ValueError:
    ...
except Exception:
    logger.exception("Unexpected failure")
```

⚠️ `except Exception` should exist **only at system boundaries**
(API handlers, worker loops, schedulers).

---

## 4. Nested Exceptions & Propagation

### Key Rule

> **Catching an exception stops propagation unless you `raise`.**

```python
def inner():
    try:
        1 / 0
    except ZeroDivisionError:
        print("Handled")

def outer():
    inner()
    print("Outer thinks this succeeded")
```

Why?

* `inner()` caught the error
* no exception escaped
* `outer()` sees success

### When is this acceptable?

Only if `inner()`:

* fully recovers
* applies a valid fallback
* or explicitly signals failure (return value / Result)

Otherwise → **silent failure bug**.

---

## 5. The `raise` Keyword (Critical)

`raise` either **propagates** an error or **creates a new one**.

---

### A. `raise` (bare) — propagate

```python
except ValueError:
    raise
```

* re-throws the **current exception**
* preserves the **original traceback**
* **no new exception is created**

✅ Correct way to re-raise

---

### B. `raise X(...)` — create

```python
raise ValueError("Invalid input")
```

* creates a **new exception**
* traceback starts **here**

Use when **you are the source** of the error.

---

### C. `raise e` — avoid

```python
except ValueError as e:
    raise e
```

* re-throws the same object
* but traceback may restart
* origin can be obscured

❌ Use bare `raise` instead.

---

### D. `raise X(...) from e` — translate

```python
except ZeroDivisionError as e:
    raise CalculationError("Calculation failed") from e
```

* raises a new error
* links the original as the cause
* preserves root cause

✅ Best practice for layered systems.

---

### E. `raise X(...) from None` — suppress context

```python
except KeyError:
    raise ConfigError("Bad config") from None
```

* hides original exception
* use **very sparingly**

---

## 6. Outer `except Exception as e` (Correct Usage)

Outer catch-alls are normal **only if they enforce policy**:

```python
while True:
    try:
        process_item()
    except Exception:
        logger.exception("Item failed")
        continue  # or retry / abort
```

Inner layers must **not** swallow exceptions unless failure is acceptable.

---

## 7. Logging (Production Rule)

```python
except Exception:
    logger.exception("Operation failed")
```

* captures message + full stack trace
* works only inside `except`

Never `print()` exceptions in production code.

---

## 8. Library / Module Pattern

```python
class MyProjectError(Exception): pass
class ValidationError(MyProjectError): pass
class DatabaseError(MyProjectError): pass
```

Allows users to safely catch **all domain failures**.

---

## 9. Never Swallow Exceptions

```python
# ❌
except Exception:
    pass

# ✅
except Exception:
    logger.warning("Failed but continuing")
```

Silent failure = corrupted state + lost debugging signal.

---

## 10. Exceptions Are Not Control Flow

Exceptions are slow and semantically wrong for expected cases.

```python
# ❌
try:
    int(x)
except ValueError:
    ...

# ✅
if x.isdigit():
    int(x)
```

---

## Core Mental Model

* **Catch = stop propagation**
* **No raise = success**
* **Bare `raise` = continue failure**
* **`raise X from e` = translate failure**
* **Outer layers decide policy**

---

## Final Checklist

* Re-raise with **bare `raise`**
* Catch specific errors low, broad errors high
* Never swallow silently
* Use `with` for resources
* Log with `logger.exception()`
* Translate errors explicitly
* Use return values when failure is expected

---
