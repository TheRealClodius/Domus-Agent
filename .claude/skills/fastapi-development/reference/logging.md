# Structured Logging Reference

## Stack: structlog + asgi-correlation-id

```bash
pip install structlog asgi-correlation-id
```

## Setup

```python
# core/logging.py
import logging
import structlog

def setup_logging(log_level: str = "INFO", json_output: bool = True) -> None:
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

## Middleware Integration

```python
# main.py
from asgi_correlation_id import CorrelationIdMiddleware
from asgi_correlation_id.context import correlation_id

app.add_middleware(
    CorrelationIdMiddleware,
    header_name="X-Request-ID",
    generator=lambda: uuid4().hex,
)

@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=correlation_id.get(),
        method=request.method,
        path=request.url.path,
    )
    logger = structlog.get_logger()
    logger.info("request_started")

    response = await call_next(request)

    logger.info("request_completed", status_code=response.status_code)
    response.headers["X-Request-ID"] = correlation_id.get() or ""
    return response
```

## Usage in Application Code

```python
import structlog

logger = structlog.get_logger()

async def create_user(db: AsyncSession, user_in: UserCreate) -> User:
    logger.info("creating_user", email=user_in.email)
    # ... create user ...
    logger.info("user_created", user_id=user.id)
    return user
```

Bound context (request_id, method, path) is automatically included in every log within the request lifecycle — no need to pass it around.
