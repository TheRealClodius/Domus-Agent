# Project Scaffold

## Directory Structure

```
project-name/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app, lifespan, middleware, router includes
│   ├── config.py               # pydantic-settings BaseSettings
│   ├── database.py             # Engine, session factory, get_db dependency
│   ├── exceptions.py           # Domain exceptions + FastAPI exception handlers
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py             # Shared dependencies (auth, pagination)
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py       # v1 router aggregating all endpoint modules
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           ├── users.py
│   │           └── health.py
│   ├── models/                 # SQLAlchemy models
│   │   ├── __init__.py
│   │   └── user.py
│   ├── schemas/                # Pydantic schemas (create/update/response)
│   │   ├── __init__.py
│   │   └── user.py
│   └── services/               # Business logic
│       ├── __init__.py
│       └── user_service.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_users.py
├── alembic/
│   ├── env.py
│   └── versions/
├── alembic.ini
├── pyproject.toml
├── requirements.txt
├── Dockerfile
├── .env.example
└── .gitignore
```

## pyproject.toml

```toml
[project]
name = "project-name"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "pyjwt>=2.10",
    "pwdlib[argon2]>=0.3",
    "httpx>=0.28",
    "structlog>=24.4",
    "asgi-correlation-id>=4.3",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=1.0",
    "pytest-cov>=6.0",
    "aiosqlite>=0.20",
    "ruff>=0.9",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.ruff]
target-version = "py312"
line-length = 100
```

## requirements.txt (if not using pyproject.toml)

```
fastapi>=0.115
uvicorn[standard]>=0.34
pydantic>=2.10
pydantic-settings>=2.7
sqlalchemy[asyncio]>=2.0.36
asyncpg>=0.30
alembic>=1.14
pyjwt>=2.10
pwdlib[argon2]>=0.3
httpx>=0.28
structlog>=24.4
asgi-correlation-id>=4.3
```

## .env.example

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/dbname
SECRET_KEY=generate-with-openssl-rand-hex-32
ENVIRONMENT=development
ALLOWED_ORIGINS=http://localhost:3000
LOG_LEVEL=INFO
```
