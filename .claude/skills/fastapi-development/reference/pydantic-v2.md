# Pydantic v2 Reference

## Model Definition

```python
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator, model_validator

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, strict=False)

    id: int
    email: EmailStr
    username: str
    created_at: datetime

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    password: str = Field(..., min_length=8)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()

class UserUpdate(BaseModel):
    """All fields optional for partial updates."""
    email: EmailStr | None = None
    username: str | None = Field(None, min_length=3, max_length=100)
    password: str | None = Field(None, min_length=8)
```

## Key Migrations from v1

| v1 (dead) | v2 (use this) |
|-----------|---------------|
| `class Config:` | `model_config = ConfigDict(...)` |
| `orm_mode = True` | `from_attributes = True` |
| `.dict()` | `.model_dump()` |
| `.dict(exclude_unset=True)` | `.model_dump(exclude_unset=True)` |
| `.json()` | `.model_dump_json()` |
| `.schema()` | `.model_json_schema()` |
| `@validator` | `@field_validator` (with `@classmethod`) |
| `@root_validator` | `@model_validator(mode="before"` or `"after")` |
| `Optional[str]` | `str \| None` |
| `List[int]` | `list[int]` |
| `constr(min_length=1)` | `Field(..., min_length=1)` |

## Settings with pydantic-settings

```python
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    DATABASE_URL: str
    SECRET_KEY: str = Field(..., min_length=32)
    ENVIRONMENT: str = "development"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v
```

## model_validator Example

```python
class DateRange(BaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_range(self) -> "DateRange":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self
```

## Pydantic v2.12 Features

- `exclude_if` at field level for conditional serialization
- `ValidateAs` annotation helper
- PEP 728: TypedDict with typed extra items
- Python 3.14 support
