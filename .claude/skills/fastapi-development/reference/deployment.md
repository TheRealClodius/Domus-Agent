# Deployment Reference

## Health Checks

Every project needs two endpoints:

```python
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

health_router = APIRouter(tags=["health"])

@health_router.get("/health")
async def liveness():
    """Liveness probe — is the process running?"""
    return {"status": "ok"}

@health_router.get("/ready")
async def readiness(db: AsyncSession = Depends(get_db)):
    """Readiness probe — can we serve traffic?"""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "db": "unreachable"},
        )
```

- `/health` — always returns 200 if process is alive. No dependency checks. Used by orchestrators to know if process crashed.
- `/ready` — checks dependencies (DB, Redis, etc). Returns 503 if not ready. Used by load balancers to route traffic.

## Gunicorn + Uvicorn (Production)

```bash
gunicorn app.main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 4 \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile -
```

Worker count: `2 * CPU_CORES + 1` for CPU-bound. For I/O-bound (most FastAPI apps), 2-4 workers is fine — async handles concurrency within each worker.

## Dockerfile

```dockerfile
FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8000"]
```

## Lifespan Setup

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    setup_logging(json_output=settings.ENVIRONMENT == "production")
    yield
    # Shutdown
    await engine.dispose()

app = FastAPI(
    title="My API",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
)
```

## Environment-Based Config

```python
# Disable Swagger in production
docs_url = "/docs" if settings.ENVIRONMENT != "production" else None
redoc_url = "/redoc" if settings.ENVIRONMENT != "production" else None
```
