"""Tests for main.py — FastAPI app scaffold, health endpoint, auth, CORS."""

from unittest.mock import patch

import httpx
import pytest


@pytest.fixture()
def app():
    """Import the FastAPI app with config mocked so env vars aren't required."""
    with patch.dict(
        "os.environ",
        {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "DOMUS_SERVICE_TOKEN": "test-token",
        },
        clear=True,
    ), patch("dotenv.load_dotenv"):
        import importlib
        import config
        importlib.reload(config)

        import main
        importlib.reload(main)
        yield main.app


@pytest.fixture()
def client(app):
    """Async test client wired to the FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /health should return 200 with {"status": "ok"} and require no auth."""

    async def test_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Service auth dependency
# ---------------------------------------------------------------------------


class TestServiceAuth:
    """verify_service_auth dependency should gate protected routes."""

    async def test_rejects_missing_authorization_header(self, client):
        resp = await client.get("/protected-test")
        assert resp.status_code == 401
        body = resp.json()
        assert "detail" in body

    async def test_rejects_wrong_token(self, client):
        resp = await client.get(
            "/protected-test",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401

    async def test_passes_correct_token(self, client):
        resp = await client.get(
            "/protected-test",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


class TestCORS:
    """CORS middleware should allow configured origins."""

    async def test_allows_localhost_origin(self, client):
        resp = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"

    async def test_rejects_unknown_origin(self, client):
        resp = await client.options(
            "/health",
            headers={
                "Origin": "http://evil.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Unknown origin should NOT be reflected back
        assert resp.headers.get("access-control-allow-origin") != "http://evil.com"

    async def test_allows_vercel_app_origin(self, client):
        resp = await client.options(
            "/health",
            headers={
                "Origin": "https://domus-web.vercel.app",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "https://domus-web.vercel.app"


# ---------------------------------------------------------------------------
# Phase 22.1 — JWT verification
# ---------------------------------------------------------------------------


class TestVerifyUserJWT:
    """verify_user_jwt decodes and validates Supabase HS256 JWTs."""

    _SECRET = "test-jwt-secret-at-least-32-chars!!"

    @staticmethod
    def _make_token(secret: str, user_id: str, *, expired: bool = False) -> str:
        import jwt as pyjwt
        from datetime import datetime, timezone, timedelta

        exp = datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 1)
        return pyjwt.encode(
            {"sub": user_id, "exp": int(exp.timestamp())},
            secret,
            algorithm="HS256",
        )

    def _get_verify_fn(self, secret: str):
        """Reload config+main with the given secret and return verify_user_jwt."""
        import importlib
        with patch.dict("os.environ", {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "DOMUS_SERVICE_TOKEN": "test-token",
            "SUPABASE_JWT_SECRET": secret,
        }, clear=True), patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
            import main
            importlib.reload(main)
            return main.verify_user_jwt

    def test_valid_token_returns_sub(self):
        verify_fn = self._get_verify_fn(self._SECRET)
        token = self._make_token(self._SECRET, "user-abc")
        result = verify_fn(token)
        assert result == "user-abc"

    def test_expired_token_raises_401(self):
        from fastapi import HTTPException
        verify_fn = self._get_verify_fn(self._SECRET)
        token = self._make_token(self._SECRET, "user-abc", expired=True)
        with pytest.raises(HTTPException) as exc_info:
            verify_fn(token)
        assert exc_info.value.status_code == 401

    def test_wrong_secret_raises_401(self):
        from fastapi import HTTPException
        verify_fn = self._get_verify_fn(self._SECRET)
        token = self._make_token("wrong-secret-also-at-least-32-chars", "user-abc")
        with pytest.raises(HTTPException) as exc_info:
            verify_fn(token)
        assert exc_info.value.status_code == 401

    def test_missing_sub_raises_401(self):
        import jwt as pyjwt
        from fastapi import HTTPException
        from datetime import datetime, timezone, timedelta

        exp = datetime.now(timezone.utc) + timedelta(hours=1)
        token = pyjwt.encode({"exp": int(exp.timestamp())}, self._SECRET, algorithm="HS256")
        verify_fn = self._get_verify_fn(self._SECRET)
        with pytest.raises(HTTPException) as exc_info:
            verify_fn(token)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Phase 22.4 — AgentRequest context_items validator
# ---------------------------------------------------------------------------


class TestAgentRequestContextItemsValidator:
    """AgentRequest.context_items Pydantic validator enforces size limits."""

    def _load_model(self):
        import importlib
        with patch.dict("os.environ", {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key",
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "DOMUS_SERVICE_TOKEN": "test-token",
        }, clear=True), patch("dotenv.load_dotenv"):
            import config
            importlib.reload(config)
            import main
            importlib.reload(main)
            return main.AgentRequest

    def test_none_context_items_allowed(self):
        AgentRequest = self._load_model()
        req = AgentRequest(space_id="s", message="hi", user_id="u", context_items=None)
        assert req.context_items is None

    def test_ten_items_allowed(self):
        AgentRequest = self._load_model()
        items = [{"data": "abc"} for _ in range(10)]
        req = AgentRequest(space_id="s", message="hi", user_id="u", context_items=items)
        assert len(req.context_items) == 10

    def test_eleven_items_raises_422(self):
        from pydantic import ValidationError
        AgentRequest = self._load_model()
        items = [{"data": "abc"} for _ in range(11)]
        with pytest.raises(ValidationError):
            AgentRequest(space_id="s", message="hi", user_id="u", context_items=items)

    def test_oversized_item_data_raises_422(self):
        from pydantic import ValidationError
        AgentRequest = self._load_model()
        items = [{"data": "x" * 10_000_001}]
        with pytest.raises(ValidationError):
            AgentRequest(space_id="s", message="hi", user_id="u", context_items=items)
