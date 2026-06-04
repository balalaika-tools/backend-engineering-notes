# Pydantic — Data Validation for FastAPI

Pydantic is a **data validation library**, not a serializer. It guarantees that if your code receives a model instance, the data inside it is **already valid**. This guide covers Pydantic v2 with FastAPI.

---

## 1. The Mental Model

### What Pydantic Actually Does

Pydantic does **three things**:

1. **Parses** incoming data into Python types
2. **Validates** that the data satisfies your constraints
3. **Serializes** model instances back to dicts/JSON

The critical insight:

> **If you have a model instance, the data is valid. Period.**

You never need to check fields after instantiation. Validation happens at construction time or it raises `ValidationError`.

### Pydantic Is Not

| Pydantic is NOT | Pydantic IS |
|-----------------|-------------|
| A serialization library | A validation library that can serialize |
| An ORM | A data contract layer |
| A schema generator | A runtime type enforcer |
| Optional decoration | Your first line of defense against bad data |

---

## 2. BaseModel Basics

### Defining a Model

```python
from pydantic import BaseModel

class User(BaseModel):
    name: str
    email: str
    age: int
    is_active: bool = True
```

Key rules:

- **Type annotations are required** — they define both the type and validation
- **Fields without defaults are required**
- **Fields with defaults are optional** (callers can omit them)
- **Order matters** — required fields must come before fields with defaults

### Creating Instances

```python
# From keyword arguments
user = User(name="Alice", email="alice@example.com", age=30)

# From a dict
data = {"name": "Alice", "email": "alice@example.com", "age": 30}
user = User(**data)

# From JSON string
user = User.model_validate_json('{"name": "Alice", "email": "alice@example.com", "age": 30}')
```

### Type Coercion

Pydantic coerces compatible types by default:

```python
user = User(name="Alice", email="alice@example.com", age="30", is_active="true")
print(user.age)        # 30 (int, not str)
print(user.is_active)  # True (bool, not str)
```

To disable coercion and require exact types, use strict mode:

```python
from pydantic import ConfigDict

class StrictUser(BaseModel):
    model_config = ConfigDict(strict=True)

    name: str
    age: int

StrictUser(name="Alice", age="30")  # ValidationError: age must be int
```

---

## 3. Field Types and Constraints

### Common Types

```python
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, EmailStr, HttpUrl

class Product(BaseModel):
    id: UUID
    name: str
    price: Decimal
    tags: list[str]
    metadata: dict[str, str]
    created_at: datetime
    release_date: date | None = None
    url: HttpUrl | None = None
```

### The `Field()` Function

`Field()` adds constraints and metadata to individual fields:

```python
from pydantic import BaseModel, Field

class Product(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price: float = Field(gt=0, description="Price in USD")
    quantity: int = Field(ge=0, le=10000, default=0)
    sku: str = Field(pattern=r"^[A-Z]{2}-\d{4}$")
```

### Constraint Reference

| Constraint | Applies to | Meaning |
|------------|-----------|---------|
| `gt`, `ge` | numbers | Greater than / greater or equal |
| `lt`, `le` | numbers | Less than / less or equal |
| `min_length`, `max_length` | str, list | Length bounds |
| `pattern` | str | Regex match |
| `multiple_of` | numbers | Must be a multiple of |
| `default` | any | Default value |
| `default_factory` | any | Callable that produces default |
| `alias` | any | Alternative field name for input |

### Optional vs Required vs Default

This is a common source of confusion:

```python
from pydantic import BaseModel

class Example(BaseModel):
    # Required — must be provided, cannot be None
    a: str

    # Required — must be provided, CAN be None
    b: str | None

    # Optional — has a default, cannot be None
    c: str = "default"

    # Optional — has a default of None, CAN be None
    d: str | None = None
```

The critical distinction:

| Declaration | Required? | Can be None? |
|-------------|-----------|-------------|
| `x: str` | Yes | No |
| `x: str \| None` | Yes | Yes |
| `x: str = "hi"` | No | No |
| `x: str \| None = None` | No | Yes |

Common mistake:

```python
# ❌ WRONG — this means "required, but can be None"
class Bad(BaseModel):
    name: str | None  # caller MUST provide it, even if the value is None

# ✅ CORRECT — this means "optional, defaults to None"
class Good(BaseModel):
    name: str | None = None  # caller can omit it entirely
```

---

## 4. Validators

### Field Validators

Validate a single field's value:

```python
from pydantic import BaseModel, field_validator

class User(BaseModel):
    name: str
    email: str
    age: int

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name cannot be empty or whitespace")
        return v.strip()

    @field_validator("email")
    @classmethod
    def email_must_contain_at(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("invalid email format")
        return v.lower()

    @field_validator("age")
    @classmethod
    def age_must_be_reasonable(cls, v: int) -> int:
        if not 0 <= v <= 150:
            raise ValueError("age must be between 0 and 150")
        return v
```

Key rules:

- Always use `@classmethod` decorator
- Return the (possibly transformed) value
- Raise `ValueError` or `AssertionError` to signal invalid data
- The validator receives the **already-parsed** value (after type coercion)

### Validating Multiple Fields

Apply one validator to multiple fields:

```python
class Address(BaseModel):
    city: str
    state: str
    country: str

    @field_validator("city", "state", "country")
    @classmethod
    def must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field cannot be empty")
        return v.strip()
```

### Mode: Before vs After

```python
from pydantic import field_validator

class Flexible(BaseModel):
    tags: list[str]

    # BEFORE validation — receives raw input (could be anything)
    @field_validator("tags", mode="before")
    @classmethod
    def split_tags(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",")]
        return v
```

| Mode | Input | Use case |
|------|-------|----------|
| `"after"` (default) | Already-parsed Python type | Enforce business rules |
| `"before"` | Raw input (any type) | Accept flexible input formats |
| `"wrap"` | Raw input + handler | Full control over parsing pipeline |

### Model Validators

Validate the **entire model** — useful for cross-field validation:

```python
from pydantic import BaseModel, model_validator

class DateRange(BaseModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def end_after_start(self) -> "DateRange":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        return self

class DiscountRule(BaseModel):
    discount_type: str  # "percentage" or "fixed"
    value: float

    @model_validator(mode="after")
    def validate_discount(self) -> "DiscountRule":
        if self.discount_type == "percentage" and not 0 <= self.value <= 100:
            raise ValueError("percentage discount must be 0-100")
        if self.discount_type == "fixed" and self.value < 0:
            raise ValueError("fixed discount cannot be negative")
        return self
```

### Pre Model Validator

Runs **before** fields are parsed — useful for transforming raw input:

```python
class APIPayload(BaseModel):
    user_name: str
    user_email: str

    @model_validator(mode="before")
    @classmethod
    def normalize_keys(cls, data: dict) -> dict:
        """Accept camelCase input and convert to snake_case fields."""
        if isinstance(data, dict):
            mapped = {}
            key_map = {"userName": "user_name", "userEmail": "user_email"}
            for k, v in data.items():
                mapped[key_map.get(k, k)] = v
            return mapped
        return data
```

### Validator Execution Order

```
1. mode="before" model validator  (runs on the raw input dict)
2. mode="before" field validators (in field declaration order)
3. Type parsing / coercion
4. mode="after"  field validators (in field declaration order)
5. mode="after"  model validator  (runs on the fully-built model)
```

Important: `mode="after"` field validators run in **field declaration order**, not alphabetical order. If field `b` depends on field `a`, declare `a` first.

```python
# ❌ WRONG — password_confirm validator runs before password is validated
class Bad(BaseModel):
    password_confirm: str
    password: str

    @field_validator("password_confirm")
    @classmethod
    def passwords_match(cls, v, info):
        # info.data["password"] may not exist yet!
        ...

# ✅ CORRECT — password is validated first
class Good(BaseModel):
    password: str
    password_confirm: str

    @field_validator("password_confirm")
    @classmethod
    def passwords_match(cls, v, info):
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("passwords do not match")
        return v
```

---

## 5. ConfigDict

`ConfigDict` controls model-wide behavior. Set it as `model_config` on your class.

### Essential Settings

```python
from pydantic import BaseModel, ConfigDict

class MyModel(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,     # strip leading/trailing whitespace from strings
        from_attributes=True,          # allow creating from ORM objects (obj.attr access)
        extra="forbid",                # reject unknown fields
        str_min_length=1,              # no empty strings allowed anywhere
        populate_by_name=True,         # allow both alias and field name
    )
```

### Config Reference

| Setting | Default | Effect |
|---------|---------|--------|
| `str_strip_whitespace` | `False` | Auto-strip whitespace from all string fields |
| `from_attributes` | `False` | Read data from object attributes (ORM mode) |
| `extra` | `"ignore"` | `"forbid"` rejects unknown fields, `"allow"` stores them |
| `str_min_length` | `None` | Minimum length for all string fields |
| `str_max_length` | `None` | Maximum length for all string fields |
| `populate_by_name` | `False` | Accept both alias and Python field name |
| `use_enum_values` | `False` | Store enum values instead of enum members |
| `strict` | `False` | Disable type coercion globally |
| `frozen` | `False` | Make instances immutable (hashable) |

### ORM Mode (`from_attributes`)

Allows constructing a Pydantic model from an ORM object (SQLAlchemy, Tortoise, etc.):

```python
# SQLAlchemy 2.0 model
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class UserORM(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    email: Mapped[str]

# Pydantic model
class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str

# Usage (async SQLAlchemy)
result = await db.execute(select(UserORM).where(UserORM.id == user_id))
orm_user = result.scalar_one()
response = UserResponse.model_validate(orm_user)  # reads orm_user.id, orm_user.name, etc.
```

### Forbidding Extra Fields

Critical for APIs — prevents clients from sending unexpected data:

```python
# ❌ DANGEROUS — silently ignores unknown fields
class UserCreate(BaseModel):
    name: str
    email: str

UserCreate(name="Alice", email="a@b.com", is_admin=True)
# Works! is_admin is silently ignored. Caller thinks they set admin.

# ✅ SAFE — rejects unknown fields immediately
class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    email: str

UserCreate(name="Alice", email="a@b.com", is_admin=True)
# ValidationError: extra inputs are not permitted
```

### Alias Generator

Map between naming conventions (e.g., Python snake_case and JSON camelCase):

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

class UserResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,  # accept both "user_name" and "userName"
    )

    user_name: str
    email_address: str

# Input accepts camelCase
user = UserResponse(userName="Alice", emailAddress="a@b.com")

# Also accepts snake_case (because populate_by_name=True)
user = UserResponse(user_name="Alice", email_address="a@b.com")

# Serialization uses alias (camelCase) by default with by_alias=True
print(user.model_dump(by_alias=True))
# {"userName": "Alice", "emailAddress": "a@b.com"}
```

---

## 6. Nested Models and Complex Types

### Nested Models

```python
class Address(BaseModel):
    street: str
    city: str
    country: str
    zip_code: str = Field(pattern=r"^\d{5}(-\d{4})?$")

class Company(BaseModel):
    name: str
    address: Address

class Employee(BaseModel):
    name: str
    email: str
    company: Company
    previous_companies: list[Company] = []
```

Usage:

```python
employee = Employee(
    name="Alice",
    email="alice@acme.com",
    company={
        "name": "Acme Corp",
        "address": {
            "street": "123 Main St",
            "city": "Springfield",
            "country": "US",
            "zip_code": "62704",
        },
    },
)

# Nested models are fully validated
print(employee.company.address.city)  # "Springfield"
```

Pydantic validates the **entire tree**. If `zip_code` is invalid, you get a clear error with the full path: `company.address.zip_code`.

### Complex Collection Types

```python
class Dashboard(BaseModel):
    # List of models
    widgets: list[Widget]

    # Dict with model values
    user_preferences: dict[str, Preference]

    # Nested collections
    tag_groups: dict[str, list[str]]

    # Set (auto-deduplicates)
    unique_tags: set[str] = set()

    # Tuple with fixed structure
    coordinates: tuple[float, float]
```

### Discriminated Unions

When a field can be one of several model types, use a discriminator for efficient parsing:

```python
from typing import Literal, Annotated
from pydantic import BaseModel, Field

class EmailNotification(BaseModel):
    type: Literal["email"]
    email_address: str
    subject: str

class SMSNotification(BaseModel):
    type: Literal["sms"]
    phone_number: str

class PushNotification(BaseModel):
    type: Literal["push"]
    device_token: str

Notification = Annotated[
    EmailNotification | SMSNotification | PushNotification,
    Field(discriminator="type"),
]

class Alert(BaseModel):
    message: str
    notification: Notification

# Pydantic picks the right model based on "type"
alert = Alert(
    message="Server down",
    notification={"type": "sms", "phone_number": "+1234567890"},
)
print(type(alert.notification))  # <class 'SMSNotification'>
```

Without a discriminator, Pydantic tries each union member in order and uses the first that validates. With a discriminator, it jumps directly to the right type.

---

## 7. Custom Types and Annotated

### Annotated for Reusable Constraints

Instead of repeating `Field()` constraints everywhere, create reusable types:

```python
from typing import Annotated
from pydantic import Field, BaseModel

# Define once
NonEmptyStr = Annotated[str, Field(min_length=1, max_length=500)]
PositiveFloat = Annotated[float, Field(gt=0)]
Percentage = Annotated[float, Field(ge=0, le=100)]
PageSize = Annotated[int, Field(ge=1, le=100, default=20)]

# Use everywhere
class Product(BaseModel):
    name: NonEmptyStr
    description: NonEmptyStr
    price: PositiveFloat
    discount: Percentage = 0

class Review(BaseModel):
    title: NonEmptyStr
    body: NonEmptyStr
    rating: Percentage
```

### Custom Types with `__get_pydantic_core_schema__`

For complex validation logic that goes beyond `Field()`:

```python
from typing import Annotated, Any
from pydantic import GetCoreSchemaHandler, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

class PhoneNumber:
    """Custom type that validates and normalizes phone numbers."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(cls._validate)

    @classmethod
    def _validate(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("phone number must be a string")
        cleaned = "".join(c for c in v if c.isdigit() or c == "+")
        if len(cleaned) < 10 or len(cleaned) > 15:
            raise ValueError("phone number must be 10-15 digits")
        return cleaned

class Contact(BaseModel):
    name: str
    phone: PhoneNumber
```

### Practical Pattern: Annotated with AfterValidator

The most pragmatic approach for custom validation on types:

```python
from typing import Annotated
from pydantic import AfterValidator, BaseModel

def validate_currency_code(v: str) -> str:
    if len(v) != 3 or not v.isalpha():
        raise ValueError("currency code must be 3 letters (e.g., USD)")
    return v.upper()

def validate_positive(v: float) -> float:
    if v <= 0:
        raise ValueError("must be positive")
    return v

CurrencyCode = Annotated[str, AfterValidator(validate_currency_code)]
PositiveAmount = Annotated[float, AfterValidator(validate_positive)]

class Payment(BaseModel):
    amount: PositiveAmount
    currency: CurrencyCode
    description: str
```

---

## 8. Serialization

### model_dump() — To Dict

```python
class User(BaseModel):
    name: str
    email: str
    password_hash: str
    is_active: bool = True

user = User(name="Alice", email="a@b.com", password_hash="abc123", is_active=True)

# Full dump
user.model_dump()
# {"name": "Alice", "email": "a@b.com", "password_hash": "abc123", "is_active": True}

# Exclude sensitive fields
user.model_dump(exclude={"password_hash"})
# {"name": "Alice", "email": "a@b.com", "is_active": True}

# Include only specific fields
user.model_dump(include={"name", "email"})
# {"name": "Alice", "email": "a@b.com"}

# Exclude defaults (only include fields that were explicitly set)
user.model_dump(exclude_defaults=True)
# {"name": "Alice", "email": "a@b.com", "password_hash": "abc123"}

# Exclude None values
user.model_dump(exclude_none=True)
# all non-None fields

# Exclude unset fields (fields not provided by caller)
user.model_dump(exclude_unset=True)
# only fields explicitly passed during construction
```

### model_dump_json() — To JSON String

```python
# Direct JSON serialization (faster than json.dumps(model_dump()))
json_str = user.model_dump_json()

# Same parameters as model_dump()
json_str = user.model_dump_json(exclude={"password_hash"}, indent=2)
```

### Serialization with Aliases

```python
from pydantic import ConfigDict
from pydantic.alias_generators import to_camel

class APIResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    user_name: str
    created_at: datetime

resp = APIResponse(user_name="Alice", created_at=datetime.now())

# Default: uses Python field names
resp.model_dump()
# {"user_name": "Alice", "created_at": "..."}

# With aliases: uses camelCase
resp.model_dump(by_alias=True)
# {"userName": "Alice", "createdAt": "..."}
```

### Nested Serialization Control

```python
class User(BaseModel):
    name: str
    email: str
    address: Address

user.model_dump(exclude={"address": {"zip_code"}})
# address is included, but without zip_code

user.model_dump(exclude={"address": True})
# address is entirely excluded
```

### Custom Serializers

```python
from pydantic import BaseModel, field_serializer
from decimal import Decimal

class Invoice(BaseModel):
    amount: Decimal
    currency: str

    @field_serializer("amount")
    def serialize_amount(self, v: Decimal, _info) -> str:
        return f"{v:.2f}"

invoice = Invoice(amount=Decimal("19.99"), currency="USD")
invoice.model_dump()
# {"amount": "19.99", "currency": "USD"}
```

---

## 9. FastAPI Integration

### Request Body

Any Pydantic model in an endpoint signature (without `Depends`) becomes a **request body**:

```python
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()

class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price: float = Field(gt=0)
    category: str

@app.post("/products")
def create_product(product: ProductCreate):
    # product is already validated — guaranteed to have:
    # - non-empty name (max 200 chars)
    # - positive price
    # - category string
    return {"id": 1, **product.model_dump()}
```

FastAPI automatically:

1. Reads the request body as JSON
2. Passes it to `ProductCreate(**json_data)`
3. Returns `422 Unprocessable Entity` if validation fails
4. Generates OpenAPI schema from the model

### Response Model

Control what gets sent back to the client:

```python
class UserInDB(BaseModel):
    id: int
    name: str
    email: str
    password_hash: str

class UserResponse(BaseModel):
    id: int
    name: str
    email: str

@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int):
    user = db.get(user_id)  # returns UserInDB
    return user  # FastAPI filters to only UserResponse fields
```

The `response_model` acts as a **security filter** — even if your function returns extra fields (like `password_hash`), they are stripped from the response.

### response_model_exclude_unset

Critical for PATCH endpoints — only return fields that were explicitly set:

```python
class ItemResponse(BaseModel):
    name: str
    description: str | None = None
    price: float
    tax: float | None = None

@app.get("/items/{item_id}", response_model=ItemResponse, response_model_exclude_unset=True)
def get_item(item_id: int):
    # If description and tax were never set in the database,
    # the response won't include them at all (not even as null).
    return items[item_id]
```

Without `response_model_exclude_unset`:

```json
{"name": "Keyboard", "description": null, "price": 99.99, "tax": null}
```

With `response_model_exclude_unset=True`:

```json
{"name": "Keyboard", "price": 99.99}
```

### Validation Error Responses

When Pydantic validation fails, FastAPI returns a structured 422 response:

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "price"],
      "msg": "Value error, must be positive",
      "input": -5
    }
  ]
}
```

The `loc` field tells the client exactly **where** the error is: `["body", "price"]` means "in the request body, the `price` field."

---

## 10. Common Patterns

### Pattern 1: Request/Response Model Separation

Never use the same model for input and output:

```python
# ❌ WRONG — same model for everything
class User(BaseModel):
    id: int
    name: str
    email: str
    password: str
    created_at: datetime

@app.post("/users")
def create_user(user: User):  # client must send id? created_at?
    ...

# ✅ CORRECT — separate models per operation
class UserCreate(BaseModel):
    name: str
    email: str
    password: str

class UserUpdate(BaseModel):
    name: str | None = None
    email: str | None = None

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    created_at: datetime

@app.post("/users", response_model=UserResponse)
def create_user(user: UserCreate):
    ...

@app.patch("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: int, user: UserUpdate):
    ...
```

### Pattern 2: Base Model Inheritance

Share common fields across related models:

```python
class ProductBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    price: float = Field(gt=0)
    category_id: int

class ProductCreate(ProductBase):
    """Fields needed to create a product."""
    pass

class ProductUpdate(BaseModel):
    """All fields optional for partial updates."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    price: float | None = Field(default=None, gt=0)
    category_id: int | None = None

class ProductResponse(ProductBase):
    """Fields returned to the client."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
```

### Pattern 3: Partial Update Models

For PATCH endpoints, you need to distinguish between "not provided" and "set to null":

```python
class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    price: float | None = None
    description: str | None = None

@app.patch("/products/{product_id}")
def update_product(product_id: int, updates: ProductUpdate):
    # Only update fields that were explicitly sent
    update_data = updates.model_dump(exclude_unset=True)

    # update_data only contains keys the client actually sent
    # {"name": "New Name"} — not {"name": "New Name", "price": None, "description": None}

    product = get_product(product_id)
    for field, value in update_data.items():
        setattr(product, field, value)
    db.commit()
    return product
```

### Pattern 4: Pagination Response Wrapper

```python
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int

@app.get("/products", response_model=PaginatedResponse[ProductResponse])
def list_products(page: int = 1, page_size: int = 20):
    ...
```

### Pattern 5: Config Base Class

Create a shared base for all your API models:

```python
class APIModel(BaseModel):
    """Base for all API request/response models."""
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

class UserCreate(APIModel):
    name: str
    email: str

class ProductCreate(APIModel):
    name: str
    price: float
```

Every model inheriting from `APIModel` automatically strips whitespace and rejects unknown fields.

---

## 11. Common Mistakes

### Mistake 1: Mutable Default Values

```python
# ❌ WRONG — shared mutable default
class Bad(BaseModel):
    tags: list[str] = []       # same list instance shared across all models
    metadata: dict = {}        # same dict instance shared across all models

# ✅ CORRECT — use default_factory
from pydantic import Field

class Good(BaseModel):
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
```

Note: Pydantic v2 handles `[]` and `{}` safely for the **default** case — it deep-copies defaults at instantiation, unlike plain Python classes. This does **not** protect you from mutable-object aliasing in general: if a caller passes a list or dict in as a field value (not as a default) and then mutates it after construction, or passes the same object into multiple model instances, those instances share the object unless you also enable `model_config["validate_assignment"] = True` or explicitly copy on input. Using `default_factory` is still the recommended style because it is explicit and avoids confusion.

### Mistake 2: Confusing Optional with = None

```python
# ❌ These are NOT the same
class Model(BaseModel):
    a: str | None         # required, but can be None
    b: str | None = None  # optional, defaults to None

# Client sending {} will fail for 'a' but succeed for 'b'
```

Rule of thumb: if a field should be **omittable by the caller**, it needs a default value.

### Mistake 3: Validator Ordering Issues

```python
# ❌ WRONG — accessing a field that hasn't been validated yet
class Bad(BaseModel):
    password_confirm: str
    password: str

    @field_validator("password_confirm")
    @classmethod
    def check(cls, v, info):
        # info.data["password"] might not exist yet
        # because password is declared AFTER password_confirm
        ...

# ✅ CORRECT — declare dependency first
class Good(BaseModel):
    password: str
    password_confirm: str

    @field_validator("password_confirm")
    @classmethod
    def check(cls, v, info):
        if "password" in info.data and v != info.data["password"]:
            raise ValueError("passwords do not match")
        return v
```

### Mistake 4: Mutating Model Instances

```python
# ❌ WRONG — bypasses validation
user = User(name="Alice", age=30)
user.age = -5  # no validation runs!

# ✅ CORRECT — use model_copy for validated updates
user = user.model_copy(update={"age": 25})

# Or make the model frozen (immutable)
class User(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    age: int

user.age = 25  # TypeError: instance is frozen
```

Note: by default, direct attribute assignment in Pydantic v2 does **not** re-run validators. If you need validation on assignment, use `model_config = ConfigDict(validate_assignment=True)`.

### Mistake 5: Not Using extra="forbid" in API Models

```python
# ❌ DANGEROUS — silently drops unknown fields
class UserCreate(BaseModel):
    name: str

# Client sends {"name": "Alice", "role": "admin"}
# role is silently ignored — client thinks they set it

# ✅ SAFE
class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str

# Client gets a clear 422 error if they send unknown fields
```

### Mistake 6: Returning ORM Objects Without from_attributes

```python
# ❌ FAILS — Pydantic can't read SQLAlchemy attributes
class UserResponse(BaseModel):
    id: int
    name: str

@app.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db)):
    return db.get(UserORM, user_id)  # SQLAlchemy 2.0 style
    # Error: Pydantic can't convert ORM object

# ✅ WORKS — from_attributes enables ORM → Pydantic conversion
class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
```

### Mistake 7: Using dict Instead of Pydantic Models

```python
# ❌ WRONG — no validation, no documentation, no type safety
@app.post("/users")
def create_user(data: dict):
    name = data.get("name")  # could be missing
    age = data.get("age")    # could be a string
    ...

# ✅ CORRECT — validated, documented, type-safe
class UserCreate(BaseModel):
    name: str
    age: int = Field(ge=0)

@app.post("/users")
def create_user(data: UserCreate):
    # data.name is guaranteed to be a str
    # data.age is guaranteed to be a non-negative int
    ...
```

---

## 12. Quick Reference

### Model Methods

| Method | Returns | Purpose |
|--------|---------|---------|
| `Model(**data)` | Model instance | Create with validation |
| `Model.model_validate(obj)` | Model instance | Create from dict or ORM object |
| `Model.model_validate_json(json_str)` | Model instance | Create from JSON string |
| `model.model_dump()` | dict | Serialize to dictionary |
| `model.model_dump_json()` | str | Serialize to JSON string |
| `model.model_copy(update={...})` | Model instance | Clone with modifications |
| `model.model_fields_set` | set[str] | Fields explicitly provided by caller |
| `Model.model_json_schema()` | dict | Generate JSON Schema |

### model_dump() Parameters

| Parameter | Effect |
|-----------|--------|
| `include={...}` | Only include these fields |
| `exclude={...}` | Exclude these fields |
| `exclude_unset=True` | Skip fields not explicitly set by caller |
| `exclude_defaults=True` | Skip fields equal to their default |
| `exclude_none=True` | Skip fields with None value |
| `by_alias=True` | Use alias names instead of Python field names |
| `mode="json"` | Serialize to JSON-compatible types (e.g., datetime → string) |

### Key Insight

> **Pydantic models are contracts.** They define what your API accepts and returns. Treat them as the boundary between untrusted input and your application logic. Everything inside that boundary is guaranteed valid.

---

## Appendix — Pydantic v1 → v2 Migration Cheatsheet

v2 is a ground-up rewrite (the core is now in Rust). Most code ports with **renames**, but a handful of behaviors changed and a handful of APIs were removed. If you are working in a codebase that still has v1 models or are reading an older tutorial, this table saves you a grep session.

### Method renames (model instance)

| Pydantic v1 | Pydantic v2 | Notes |
|-------------|-------------|-------|
| `.dict()` | `.model_dump()` | Same args (`include`, `exclude`, `by_alias`, `exclude_unset`, …) |
| `.json()` | `.model_dump_json()` | |
| `.copy()` | `.model_copy()` | |
| `Model.parse_obj(data)` | `Model.model_validate(data)` | |
| `Model.parse_raw(json_str)` | `Model.model_validate_json(json_str)` | |
| `Model.schema()` | `Model.model_json_schema()` | |
| `Model.construct(...)` | `Model.model_construct(...)` | Skip validation |
| `Model.update_forward_refs()` | `Model.model_rebuild()` | |

### Validators

| Pydantic v1 | Pydantic v2 | Notes |
|-------------|-------------|-------|
| `@validator("x")` | `@field_validator("x")` | **Must** also add `@classmethod` below it in v2 |
| `@validator("x", pre=True)` | `@field_validator("x", mode="before")` | |
| `@validator("x", always=True)` | `@field_validator("x", mode="before")` + handle missing | "always" removed |
| `@root_validator` | `@model_validator(mode="before")` or `mode="after"` | |
| `@root_validator(pre=True)` | `@model_validator(mode="before")` | Receives raw dict |
| `@root_validator(pre=False)` | `@model_validator(mode="after")` | Receives the constructed model; return `self` |
| `values` arg (`def validate(cls, v, values)`) | `info.data` on the `ValidationInfo` arg | `def validate(cls, v, info): info.data["other"]` |

### Config

| Pydantic v1 | Pydantic v2 | Notes |
|-------------|-------------|-------|
| `class Config:` inside the model | `model_config = ConfigDict(...)` | Class-level assignment, not a nested class |
| `Config.allow_population_by_field_name = True` | `populate_by_name=True` | Renamed |
| `Config.orm_mode = True` | `from_attributes=True` | Renamed; same behavior |
| `Config.anystr_strip_whitespace = True` | `str_strip_whitespace=True` | |
| `Config.anystr_lower` / `anystr_upper` | `str_to_lower` / `str_to_upper` | |
| `Config.extra = "forbid"` | `extra="forbid"` | Same values: `ignore` / `allow` / `forbid` |
| `Config.json_encoders = {...}` | Removed — use field-level serializers (`@field_serializer`) or `@model_serializer` | Breaking change |

### Field

| Pydantic v1 | Pydantic v2 | Notes |
|-------------|-------------|-------|
| `Field(..., regex="^[a-z]+$")` | `Field(..., pattern="^[a-z]+$")` | Renamed to match JSON Schema |
| `Field(const=x)` | Use a `Literal[x]` type | `const` removed |
| `Field(allow_mutation=False)` | `Field(frozen=True)` | |

### Settings

| Pydantic v1 | Pydantic v2 |
|-------------|-------------|
| `from pydantic import BaseSettings` | `from pydantic_settings import BaseSettings` (separate package, `pip install pydantic-settings`) |

### Behavior changes that silently break

- **`strict` mode** — v1 happily coerced `"1"` → `1`. v2 still does by default, but strict-mode fields raise; be deliberate about `Field(..., strict=True)` or `model_config = ConfigDict(strict=True)` on security-sensitive parsing.
- **`Optional[X]` without a default is still required in both versions.** No change — just noting because people re-learn this during migrations.
- **`float` vs `Decimal`** — v2 is stricter about not losing precision. Code that passed a `Decimal` into a `float` field may now need an explicit type change.
- **Custom types** — `__get_validators__` and `__modify_schema__` are gone. Use `__get_pydantic_core_schema__` and `__get_pydantic_json_schema__`.

### Migration strategy

1. Upgrade dependency: `pip install 'pydantic>=2'` and `pip install pydantic-settings` if you use `BaseSettings`.
2. Run with `bump-pydantic` (official migration tool) — it handles the mechanical renames.
3. Grep for what the tool cannot infer: `parse_obj`, `.dict(`, `.json(`, `Config`, `@root_validator`, `@validator`, `BaseSettings`, `json_encoders`. Fix each.
4. Re-run your test suite; most remaining breaks are validator signatures (`info` arg) and removed `json_encoders`.

v1 is in security-maintenance only; new code should be v2.
