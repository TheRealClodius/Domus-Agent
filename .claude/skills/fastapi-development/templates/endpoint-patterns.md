# Endpoint & Error Patterns

## Domain Exception Decoupling

Business logic should NOT raise `HTTPException`. Decouple domain from HTTP:

```python
# app/exceptions.py
class DomainError(Exception):
    """Base for all domain errors."""
    pass

class NotFoundError(DomainError):
    def __init__(self, resource: str, identifier: str | int):
        self.resource = resource
        self.identifier = identifier
        super().__init__(f"{resource} {identifier} not found")

class ConflictError(DomainError):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)

class ForbiddenError(DomainError):
    pass

# Register handlers in main.py
from fastapi import Request
from fastapi.responses import JSONResponse

@app.exception_handler(NotFoundError)
async def not_found_handler(request: Request, exc: NotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})

@app.exception_handler(ConflictError)
async def conflict_handler(request: Request, exc: ConflictError):
    return JSONResponse(status_code=409, content={"detail": exc.message})

@app.exception_handler(ForbiddenError)
async def forbidden_handler(request: Request, exc: ForbiddenError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})
```

## Service Layer (raises domain exceptions)

```python
# app/services/user_service.py
class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_user(self, user_in: UserCreate) -> User:
        existing = await self.db.scalar(
            select(User).where(User.email == user_in.email)
        )
        if existing:
            raise ConflictError(f"Email {user_in.email} already registered")

        user = User(
            email=user_in.email,
            username=user_in.username,
            hashed_password=hash_password(user_in.password),
        )
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def get_user(self, user_id: int) -> User:
        user = await self.db.scalar(select(User).where(User.id == user_id))
        if not user:
            raise NotFoundError("User", user_id)
        return user
```

## Endpoint (thin — delegates to service)

```python
from fastapi import APIRouter, Depends, status
from app.schemas.user import UserCreate, UserResponse, UserUpdate
from app.services.user_service import UserService
from app.database import get_db

router = APIRouter(prefix="/users")

def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(db)

@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Email already registered"}},
)
async def create_user(
    user_in: UserCreate,
    svc: UserService = Depends(get_user_service),
) -> User:
    return await svc.create_user(user_in)

@router.get(
    "/{user_id}",
    response_model=UserResponse,
    responses={404: {"description": "User not found"}},
)
async def get_user(
    user_id: int,
    svc: UserService = Depends(get_user_service),
) -> User:
    return await svc.get_user(user_id)

@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    responses={404: {"description": "User not found"}, 409: {"description": "Conflict"}},
)
async def update_user(
    user_id: int,
    user_in: UserUpdate,
    svc: UserService = Depends(get_user_service),
) -> User:
    return await svc.update_user(user_id, user_in)

@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: int,
    svc: UserService = Depends(get_user_service),
) -> None:
    await svc.delete_user(user_id)
```

## Key Points

1. **`responses={}`** — document error codes in OpenAPI. Every endpoint should declare possible errors.
2. **Service layer** — all business logic. Raises `DomainError` subclasses, never `HTTPException`.
3. **Thin routes** — parse input, call service, return response. No logic in routes.
4. **Status codes** — `201` for create, `204` for delete, `409` for conflicts (not `400`).
