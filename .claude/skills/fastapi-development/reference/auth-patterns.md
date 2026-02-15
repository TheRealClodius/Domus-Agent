# Authentication Patterns

Choose the pattern that fits your architecture. Not every project needs JWT.

## Pattern 1: JWT with PyJWT (Self-Managed Auth)

Use when: you own the user database and manage authentication yourself.

```python
# security.py
from datetime import datetime, timedelta, timezone
import jwt  # PyJWT, not python-jose
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

pwd_hash = PasswordHash((Argon2Hasher(),))

def hash_password(password: str) -> str:
    return pwd_hash.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_hash.verify(password=plain, hash=hashed)

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=30))
    payload.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")

def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])

# dependencies.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer = HTTPBearer()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    user = await db.scalar(select(User).where(User.id == user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return user
```

**Key:** Use `PyJWT` (`import jwt`), NOT `python-jose` (`from jose import jwt`). python-jose is abandoned since 2021.

**Key:** Use `pwdlib` with Argon2, NOT `passlib`. passlib breaks on Python 3.13+.

## Pattern 2: Service Token (Backend-to-Backend)

Use when: a proxy (Vercel, nginx) authenticates users and forwards trusted requests.

```python
from fastapi import Depends, HTTPException, Request, status

async def verify_service_token(request: Request) -> None:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if token != settings.SERVICE_TOKEN:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

async def extract_caller(request: Request) -> dict:
    """Extract user_id/space_id from payload — proxy already authenticated."""
    body = await request.json()
    return {"user_id": body["user_id"], "space_id": body["space_id"]}
```

## Pattern 3: External Auth Provider (Supabase, Auth0, Firebase)

Use when: auth is handled by an external service. Your API validates their tokens.

```python
import jwt  # PyJWT
from jwt import PyJWKClient

# Fetch JWKS from provider (cached automatically)
jwks_client = PyJWKClient(settings.JWKS_URL)  # e.g., https://your-project.supabase.co/auth/v1/.well-known/jwks.json

async def get_current_user_from_supabase(
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
) -> dict:
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(credentials.credentials)
        payload = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            audience="authenticated",
        )
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
```

## CORS — Common Mistake

```python
# ❌ WRONG: wildcard + credentials is invalid per spec
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True)

# ✅ RIGHT: explicit origins when using credentials
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,  # ["https://app.example.com"]
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ✅ ALSO RIGHT: wildcard without credentials (public API)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False)
```
