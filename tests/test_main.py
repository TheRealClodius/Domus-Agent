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
