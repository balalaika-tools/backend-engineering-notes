# HTTP Requests & FastAPI Parameter Mapping

This guide explains **how HTTP requests work** and **how FastAPI maps request data into function arguments**.

---

## Part 0: Endpoint Structure & Conventions

### HTTP Method Semantics

| Method   | Purpose                                  | Request body?                      | Idempotent? |
|----------|------------------------------------------|------------------------------------|-------------|
| `GET`    | Retrieve a resource representation        | Avoid; no generally defined meaning | Yes         |
| `POST`   | Submit data to a resource; often create   | Yes                                | Not by default |
| `PUT`    | Replace or create the target resource     | Yes                                | Yes         |
| `PATCH`  | Apply a partial modification              | Yes                                | Not guaranteed |
| `DELETE` | Remove the target resource association    | Avoid; no generally defined meaning | Yes         |

Idempotent means that repeating the same request has the same intended server-side effect as sending it once. The response can still differ; for example, the first `DELETE` might return `204`, while a repeated one might return `404`.

`PATCH` is commonly treated as non-idempotent because a patch document can express instructions like "append this item." It can be designed to be idempotent, especially when it sets fields to explicit values and uses conditional requests such as `If-Match`.

### URL Naming Conventions

```
/users                  → collection of users
/users/{user_id}        → specific user
/users/{user_id}/orders → orders belonging to a user
```

- For resource-oriented APIs, use **plural nouns** for collections (`/users`, `/orders`)
- Use **kebab-case** for multi-word segments (`/payment-methods`)
- Prefer **no verbs** in the path — the HTTP method already expresses the action
- If an operation truly is not resource-shaped, make that exception obvious and document it

### Status Code Conventions

| Code | Meaning                   | Typical use                              |
|------|---------------------------|------------------------------------------|
| 200  | OK                        | Successful GET, PUT, PATCH               |
| 201  | Created                   | Successful POST that created a resource  |
| 202  | Accepted                  | Accepted for async/background processing |
| 204  | No Content                | Successful DELETE/update with no body    |
| 400  | Bad Request               | Malformed syntax or generic client error |
| 401  | Unauthorized              | Missing or invalid credentials           |
| 403  | Forbidden                 | Authenticated but not permitted          |
| 404  | Not Found                 | Resource does not exist                  |
| 422  | Unprocessable Entity      | FastAPI's default for validation errors  |
| 500  | Internal Server Error     | Unhandled server-side failure            |

`401 Unauthorized` is the historical status phrase, but in application terms it usually means "not authenticated." Use `403 Forbidden` when the user is authenticated but lacks permission.

### FastAPI Decorator Syntax

```python
from fastapi import FastAPI, status
from pydantic import BaseModel

app = FastAPI()

class ItemOut(BaseModel):
    id: int
    name: str

@app.post(
    "/users/{user_id}/items",
    response_model=ItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an item for a user",
    tags=["items"],
)
def create_item(user_id: int, ...):
    ...
```

Key decorator fields:

- `response_model` — Pydantic model that filters, documents, and validates the response
- `status_code` — default HTTP status code returned on success
- `summary` / `description` — show up in the auto-generated OpenAPI docs
- `tags` — group endpoints in the docs UI

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
- Avoid in `GET` requests; GET request content has no generally defined HTTP semantics

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

## 5. Cookies

### What They Are

Small pieces of data **stored by the browser or client cookie jar** and sent back with matching requests.

```
Cookie: session_id=abc123; theme=dark
```

### Characteristics

- Sent automatically by browsers when the domain, path, security, and SameSite rules match
- Can also be managed manually by non-browser API clients
- Used for browser sessions, user preferences, and sometimes tracking
- Scoped by domain and path; can expire with `Max-Age` or `Expires`
- Security flags matter: `HttpOnly`, `Secure`, and `SameSite`

### FastAPI Syntax

```python
from typing import Annotated
from fastapi import Cookie

@app.get("/profile")
def get_profile(session_id: Annotated[str | None, Cookie()] = None):
    ...
```

Required cookie:

```python
def endpoint(session_id: Annotated[str, Cookie()]):
    ...
```

> Prefer `Authorization` headers for API-to-API auth. Cookies shine for browser sessions, but session cookies usually need CSRF protection.

---

## 6. Form Data

### What It Is

Data submitted as a **URL-encoded or multipart form** — the encoding used by HTML `<form>` elements.

```
Content-Type: application/x-www-form-urlencoded

username=alice&password=secret
```

### Characteristics

- Not JSON — individual form fields arrive as strings/files, then FastAPI/Pydantic can coerce types
- Cannot mix `Form` fields and a JSON body in the same endpoint
- `Content-Type` must be `application/x-www-form-urlencoded` or `multipart/form-data`

### FastAPI Syntax

```python
from typing import Annotated
from fastapi import Form

@app.post("/login")
def login(username: Annotated[str, Form()], password: Annotated[str, Form()]):
    ...
```

Install dependency if not present:

```bash
pip install python-multipart
```

Modern FastAPI also supports Pydantic models for form fields:

```python
from typing import Annotated
from fastapi import Form
from pydantic import BaseModel

class LoginForm(BaseModel):
    username: str
    password: str

@app.post("/login")
def login(data: Annotated[LoginForm, Form()]):
    ...
```

---

## 7. File Uploads

### What They Are

Binary files sent as part of a `multipart/form-data` request.

### Characteristics

- FastAPI `File` parameters expect `multipart/form-data` encoding
- Can be combined with `Form` fields in the same endpoint
- Cannot be combined with a JSON body

### Two ways to receive files

| Type         | Description                                    |
|--------------|------------------------------------------------|
| `bytes`      | Entire file loaded into memory at once         |
| `UploadFile` | Spooled file-like object; async reads, metadata |

Prefer `UploadFile` for anything beyond tiny files.

### FastAPI Syntax

```python
from typing import Annotated
from fastapi import File, UploadFile

@app.post("/upload")
async def upload_file(file: Annotated[UploadFile, File()]):
    contents = await file.read()
    return {"filename": file.filename, "size": len(contents)}
```

`UploadFile` attributes: `.filename`, `.content_type`, `.size`, `.headers`, `.file` (a `SpooledTemporaryFile`). Async methods include `.read()`, `.write()`, `.seek()`, and `.close()`.

### Mixed file + form fields

```python
@app.post("/upload-with-meta")
async def upload_with_meta(
    file: Annotated[UploadFile, File()],
    description: Annotated[str, Form()],
):
    ...
```

### Multiple files

```python
from typing import Annotated

@app.post("/upload-many")
async def upload_many(files: Annotated[list[UploadFile], File()]):
    return [f.filename for f in files]
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

Current FastAPI docs prefer `typing.Annotated` for these annotations. The older default-value style shown in the quick reference still works and is common in existing codebases.

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
| `x: str = Cookie(None)`    | Cookie          | No       |
| `x: str = Form(...)`       | Form field      | Yes      |
| `x: UploadFile = File(...)` | File upload    | Yes      |
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
    item: Item,                            # body
    authorization: str = Header(...),      # header
    q: Optional[str] = None,               # query (optional)
):
    ...
```

Non-default arguments must come before defaulted ones (a general Python rule), so place `q` last.

FastAPI correctly routes each parameter based on the rules above.

---

## Key Takeaway

FastAPI's parameter mapping is **deterministic**:

1. Check for explicit annotations (`Header`, `Query`, `Path`, `Body`, `Cookie`, `Form`, `File`)
2. If Pydantic model → body
3. If in path template → path parameter
4. If has default → query parameter
5. Otherwise → required query parameter

**Mutual exclusions to remember:**

- `Form` / `File` fields and a JSON `Body` cannot coexist in the same endpoint
- `GET` requests should not have a body

Understanding these rules eliminates confusion about where your data comes from.
