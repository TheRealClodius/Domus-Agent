# Testing Reference (pytest-asyncio 1.0+)

## Critical: pytest-asyncio 1.0 Changes

The `event_loop` fixture was **REMOVED** in pytest-asyncio 1.0 (May 2025). Do not define it.

### pyproject.toml

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"  # All async tests are asyncio-driven automatically

[tool.pytest-asyncio]
loop_scope = "function"  # Fresh event loop per test (default)
```

With `asyncio_mode = "auto"`, you do NOT need `@pytest.mark.asyncio` on every test.

## conftest.py

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
async def client(db_session: AsyncSession):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()
```

## Test Structure

```python
async def test_create_user(client: AsyncClient):
    response = await client.post("/api/v1/users", json={
        "email": "test@example.com",
        "username": "testuser",
        "password": "securepass123",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"
    assert "hashed_password" not in data  # Never exposed

async def test_create_user_duplicate_email(client: AsyncClient):
    payload = {"email": "dup@example.com", "username": "user1", "password": "pass12345"}
    await client.post("/api/v1/users", json=payload)

    payload["username"] = "user2"
    response = await client.post("/api/v1/users", json=payload)
    assert response.status_code == 409  # Conflict, not 400

async def test_get_user_not_found(client: AsyncClient):
    response = await client.get("/api/v1/users/99999")
    assert response.status_code == 404
```

## Dead Patterns

| Don't | Why |
|-------|-----|
| Define `event_loop` fixture | Removed in pytest-asyncio 1.0 |
| `@pytest.mark.asyncio` on every test | Unnecessary with `asyncio_mode = "auto"` |
| `TestClient` (sync) for async apps | Use `httpx.AsyncClient` with `ASGITransport` |
| `scope="session"` event loop | Use `loop_scope` in config instead |

## Dependency Override Pattern

Override any `Depends()` for testing:

```python
from app.dependencies import get_current_user

async def mock_current_user():
    return User(id=1, email="test@test.com", is_active=True)

app.dependency_overrides[get_current_user] = mock_current_user
```
