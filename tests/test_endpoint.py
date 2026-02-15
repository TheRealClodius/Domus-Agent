"""Tests for POST /agent SSE endpoint (task 4.1)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.conftest import TEST_SPACE_ID, TEST_USER_ID


def _make_text_response(text):
    """Create a mock Anthropic Message with text-only content."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    message = MagicMock()
    message.content = [text_block]
    message.stop_reason = "end_turn"
    return message


@pytest.fixture()
def app():
    """Import the FastAPI app with mocked config and clients."""
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
    """Async test client."""
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


class TestPostAgentEndpoint:
    """POST /agent should accept a message and stream SSE events."""

    async def test_post_agent_requires_auth(self, client):
        resp = await client.post(
            "/agent",
            json={
                "space_id": TEST_SPACE_ID,
                "message": "Hello",
                "user_id": TEST_USER_ID,
            },
        )
        assert resp.status_code == 401

    async def test_post_agent_validates_payload(self, client):
        resp = await client.post(
            "/agent",
            json={},  # Missing required fields
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 422

    @patch("main.run_agent", new_callable=AsyncMock, return_value="Hello!")
    @patch("main.acreate_client", new_callable=AsyncMock)
    @patch("main.create_anthropic_client")
    async def test_post_agent_returns_sse_stream(
        self, mock_anthropic_factory, mock_supabase_factory, mock_run_agent, client
    ):
        mock_supabase_factory.return_value = MagicMock()
        mock_anthropic_factory.return_value = MagicMock()

        resp = await client.post(
            "/agent",
            json={
                "space_id": TEST_SPACE_ID,
                "message": "Create a note",
                "user_id": TEST_USER_ID,
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @patch("main.run_agent", new_callable=AsyncMock, return_value="Hello!")
    @patch("main.acreate_client", new_callable=AsyncMock)
    @patch("main.create_anthropic_client")
    async def test_post_agent_calls_run_agent(
        self, mock_anthropic_factory, mock_supabase_factory, mock_run_agent, client
    ):
        mock_supabase_factory.return_value = MagicMock()
        mock_anthropic_factory.return_value = MagicMock()

        await client.post(
            "/agent",
            json={
                "space_id": TEST_SPACE_ID,
                "message": "Create a note",
                "user_id": TEST_USER_ID,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        mock_run_agent.assert_called_once()
        call_kwargs = mock_run_agent.call_args
        assert call_kwargs[1]["space_id"] == TEST_SPACE_ID or call_kwargs[0][2] == TEST_SPACE_ID
