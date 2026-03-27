# HTTP Requests & FastAPI Parameter Mapping

This guide explains **how HTTP requests work** and **how FastAPI maps request data into function arguments**.

---

## Part 1: The HTTP Request

Every HTTP request consists of these parts:

```
METHOD  PATH?QUERY
HEADERS

BODY
```

### Example Request

```
GET /users/42/products?page=2 HTTP/1.1
Authorization: Bearer abc123
Accept: application/json
```

---

## 1. Path Parameters

### What They Are

Parts of the URL that **identify a specific resource**.

```
/users/42/products/7
```

- `42` → `user_id`
- `7` → `product_id`

### Characteristics

- **Required** — must be present
- Change *which* resource you're requesting
- No default values

### FastAPI Syntax

```python
@app.get("/users/{user_id}/products/{product_id}")
def endpoint(user_id: int, product_id: int):
    ...
```

### Mapping

```
/users/42/products/7
→ user_id=42, product_id=7
```

---

## 2. Query Parameters

### What They Are

Extra information that modifies **how** you want the resource.

```
?page=2&limit=20
```

### Characteristics

- Optional (usually)
- Typically have default values
- Used for filtering, pagination, sorting

### FastAPI Syntax

```python
@app.get("/items")
def endpoint(page: int = 1, limit: int = 20):
    ...
```

### Mapping

```
GET /items?page=2
→ page=2, limit=20
```

---

## 3. Headers

### What They Are

**Metadata** about the request — not business data.

```
Authorization: Bearer xxx
Content-Type: application/json
User-Agent: Chrome
```

### Characteristics

- Do not identify resources
- Accompany every request
- Used for auth tokens, content negotiation, caching

### FastAPI Syntax

```python
from fastapi import Header

@app.get("/secure")
def endpoint(authorization: str = Header(...)):
    ...
```

You can use `alias` for non-Pythonic header names:

```python
def endpoint(token: str = Header(alias="X-Custom-Token")):
    ...
```

---

## 4. Request Body

### What It Is

The **payload** of the request — structured data sent to the server.

```json
{
  "name": "Keyboard",
  "price": 100
}
```

### Characteristics

- Used in `POST`, `PUT`, `PATCH`
- For large or structured data
- **Not used in `GET` requests**

### FastAPI Syntax

```python
from pydantic import BaseModel

class ProductCreate(BaseModel):
    name: str
    price: float

@app.post("/products")
def endpoint(product: ProductCreate):
    ...
```

---

## Part 2: FastAPI Parameter Resolution

FastAPI does **not guess** where data comes from. It follows **strict rules** based on your function signature.

These rules apply to:

- Endpoint functions
- Dependency functions
- Nested dependencies

---

## The Resolution Rules

### Rule 1: No Default → Path or Query

```python
def endpoint(x: int):
    ...
```

- If `{x}` exists in the route path → **path parameter**
- Otherwise → **query parameter** (required)

### Rule 2: Has Default → Query

```python
def endpoint(x: int = 1):
    ...
```

- Always a **query parameter**
- Optional with default value

### Rule 3: Pydantic Model → Body

```python
def endpoint(data: MyModel):
    ...
```

- Treated as **request body**
- FastAPI parses JSON into the model

### Rule 4: Explicit Source Annotations

```python
from fastapi import Header, Cookie, Body, Query, Path

def endpoint(
    user_id: int = Path(...),
    page: int = Query(1),
    token: str = Header(...),
    session: str = Cookie(...),
    payload: dict = Body(...),
):
    ...
```

Explicit annotations **override** the default rules.

---

## Quick Reference Table

| Signature                  | Source          | Required |
| -------------------------- | --------------- | -------- |
| `x: int` (in path)         | Path parameter  | Yes      |
| `x: int` (not in path)     | Query parameter | Yes      |
| `x: int = 1`               | Query parameter | No       |
| `x: Model`                 | Request body    | Yes      |
| `x: str = Header(...)`     | Header          | Yes      |
| `x: str = Header(None)`    | Header          | No       |
| `x: str = Cookie(...)`     | Cookie          | Yes      |
| `x: str = Query(...)`      | Query parameter | Yes      |
| `x: int = Path(...)`       | Path parameter  | Yes      |

---

## Multiple Body Parameters

When you have multiple Pydantic models:

```python
class User(BaseModel):
    name: str

class Item(BaseModel):
    title: str

@app.post("/create")
def endpoint(user: User, item: Item):
    ...
```

FastAPI expects:

```json
{
  "user": {"name": "Alice"},
  "item": {"title": "Laptop"}
}
```

To embed a single model under a key, use `Body(embed=True)`:

```python
def endpoint(user: User = Body(embed=True)):
    ...
```

Expected:

```json
{
  "user": {"name": "Alice"}
}
```

---

## Mixing Parameter Types

A single endpoint can use all parameter types:

```python
@app.put("/users/{user_id}/items/{item_id}")
def update_item(
    user_id: int,                          # path
    item_id: int,                          # path
    q: str = None,                         # query (optional)
    item: Item,                            # body
    authorization: str = Header(...),      # header
):
    ...
```

FastAPI correctly routes each parameter based on the rules above.

---

## Key Takeaway

FastAPI's parameter mapping is **deterministic**:

1. Check for explicit annotations (`Header`, `Query`, `Path`, `Body`, `Cookie`)
2. If Pydantic model → body
3. If in path template → path parameter
4. If has default → query parameter
5. Otherwise → required query parameter

Understanding these rules eliminates confusion about where your data comes from.
