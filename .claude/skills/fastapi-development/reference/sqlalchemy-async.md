# SQLAlchemy 2.0 Async Reference

## Engine & Session Setup

```python
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

engine = create_async_engine(
    "postgresql+asyncpg://user:pass@host/db",
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

## Model Definition

```python
from datetime import datetime
from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )
```

## Dead Patterns

| Don't | Do |
|-------|-----|
| `declarative_base()` | `class Base(DeclarativeBase)` |
| `Column(String)` | `Mapped[str] = mapped_column(String(255))` |
| `Column(Integer, primary_key=True)` | `Mapped[int] = mapped_column(primary_key=True)` |
| `sessionmaker(class_=AsyncSession)` | `async_sessionmaker(engine)` |
| `Optional[str]` in Mapped | `Mapped[str \| None]` |

## AsyncAttrs for Lazy Loading

When relationships need async access:

```python
from sqlalchemy.ext.asyncio import AsyncAttrs

class Base(AsyncAttrs, DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    posts: Mapped[list["Post"]] = relationship(back_populates="author")

# Access lazy-loaded relationships in async context:
posts = await user.awaitable_attrs.posts
```

## Async CRUD Operations

```python
from sqlalchemy import select, func

async def get_by_id(db: AsyncSession, user_id: int) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

async def get_multi(
    db: AsyncSession, skip: int = 0, limit: int = 100
) -> tuple[list[User], int]:
    count = await db.scalar(select(func.count()).select_from(User))
    result = await db.execute(
        select(User).offset(skip).limit(limit).order_by(User.created_at.desc())
    )
    return list(result.scalars().all()), count or 0

async def create(db: AsyncSession, user: User) -> User:
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user
```
