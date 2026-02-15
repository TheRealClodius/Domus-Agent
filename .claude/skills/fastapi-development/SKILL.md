---
name: fastapi-development
description: Use when building, scaffolding, or modifying FastAPI applications. Use when writing async Python APIs, setting up Pydantic models, configuring SQLAlchemy async sessions, adding authentication, writing pytest-asyncio tests, or deploying with Uvicorn. Covers common mistakes with deprecated libraries (python-jose, passlib, declarative_base).
---

# FastAPI Development (2026)

Production-ready FastAPI patterns for Python 3.12+, FastAPI 0.115+, Pydantic v2, SQLAlchemy 2.0+ async.

## Deprecated — Do Not Use

| Dead Library / Pattern | Replacement | Why |
|----------------------|-------------|-----|
| `python-jose` | `PyJWT` | Abandoned since 2021, security vulnerabilities |
| `passlib` | `pwdlib` + Argon2 | Breaks on Python 3.13+, unmaintained |
| `declarative_base()` | `class Base(DeclarativeBase)` | SQLAlchemy 2.0 legacy |
| `sessionmaker(class_=AsyncSession)` | `async_sessionmaker(...)` | Dedicated async factory |
| `.dict()` / `.schema()` | `.model_dump()` / `.model_json_schema()` | Pydantic v1 removed |
| `class Config:` in models | `model_config = ConfigDict(...)` | Pydantic v2 pattern |
| `@app.on_event("startup")` | `@asynccontextmanager` lifespan | Deprecated in FastAPI |
| `datetime.utcnow()` | `datetime.now(timezone.utc)` | Deprecated in Python 3.12 |
| `event_loop` fixture | `asyncio_mode = "auto"` in pyproject.toml | Removed in pytest-asyncio 1.0 |
| `Optional[X]`, `List[X]` | `X \| None`, `list[X]` | Python 3.10+ built-in |

## Core Rules

1. **Pydantic v2 only.** `ConfigDict`, `field_validator`, `model_validator`. Never v1 syntax. See [reference/pydantic-v2.md](reference/pydantic-v2.md).
2. **Separate schemas.** `UserCreate`, `UserUpdate`, `UserResponse` — never expose ORM models as response.
3. **Domain exceptions, not HTTPException.** Business logic raises `UserNotFoundError`. Exception handlers map to HTTP. See [templates/endpoint-patterns.md](templates/endpoint-patterns.md).
4. **`Depends()` for everything.** DB sessions, settings, services, auth — never global state.
5. **`async def` for I/O, `def` for CPU.** Plain `def` routes auto-threadpool. Never block the event loop with sync DB drivers.
6. **Structured JSON logging.** `structlog` + `asgi-correlation-id`. See [reference/logging.md](reference/logging.md).
7. **Type hints on all functions and return types.** No exceptions.
8. **Health checks on every project.** `/health` (liveness) + `/ready` (readiness with dependency checks).

## Quick Reference

| Topic | Reference |
|-------|-----------|
| Pydantic v2 patterns | [reference/pydantic-v2.md](reference/pydantic-v2.md) |
| SQLAlchemy 2.0 async | [reference/sqlalchemy-async.md](reference/sqlalchemy-async.md) |
| Auth (JWT, service tokens, external) | [reference/auth-patterns.md](reference/auth-patterns.md) |
| Testing (pytest-asyncio 1.0) | [reference/testing.md](reference/testing.md) |
| Structured logging | [reference/logging.md](reference/logging.md) |
| Deployment & health checks | [reference/deployment.md](reference/deployment.md) |
| New project scaffold | [templates/project-scaffold.md](templates/project-scaffold.md) |
| Endpoint & error patterns | [templates/endpoint-patterns.md](templates/endpoint-patterns.md) |

## Settings Pattern

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    SECRET_KEY: str
    ENVIRONMENT: str = "development"
```

## Common Commands

```bash
uvicorn app.main:app --reload              # Dev server
pytest tests/ -v --tb=short                # Run tests
alembic upgrade head                       # Apply migrations
alembic revision --autogenerate -m "msg"   # Generate migration
```
