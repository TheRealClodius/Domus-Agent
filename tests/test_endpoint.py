"""Tests for POST /agent SSE endpoint (task 4.1)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tests.conftest import TEST_SPACE_ID, TEST_USER_ID, MockSupabaseClient, MockQueryBuilder

_VALID_ENV = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-key",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "DOMUS_SERVICE_TOKEN": "test-token",
}


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
    """Import the FastAPI app and pre-set mock clients on app.state.

    Tests that don't trigger the lifespan need app.state.supabase and
    app.state.anthropic set directly — the lifespan runs on ASGI startup
    (e.g. starlette TestClient), not on bare httpx requests.
    """
    with patch.dict(
        "os.environ",
        _VALID_ENV,
        clear=True,
    ), patch("dotenv.load_dotenv"):
        import importlib
        import config

        importlib.reload(config)

        import main

        importlib.reload(main)

        # Pre-set mock clients so request handlers can access app.state.
        # MockSupabaseClient is used for supabase so that get_user_tier / check_quota
        # (added in Phase 12) can await execute() without TypeError.
        main.app.state.supabase = MockSupabaseClient()
        main.app.state.anthropic = MagicMock()

        yield main.app


@pytest.fixture()
def client(app):
    """Async test client (no lifespan — app.state is pre-set by the app fixture)."""
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
    async def test_post_agent_returns_sse_stream(self, mock_run_agent, client):
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
    async def test_post_agent_calls_run_agent(self, mock_run_agent, client):
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
        assert (
            call_kwargs[1].get("space_id") == TEST_SPACE_ID
            or (len(call_kwargs[0]) > 2 and call_kwargs[0][2] == TEST_SPACE_ID)
        )


class TestLifespan:
    """FastAPI lifespan creates shared clients exactly once at startup."""

    async def test_lifespan_sets_app_state(self):
        """The lifespan function sets supabase and anthropic on app.state."""
        with patch.dict("os.environ", _VALID_ENV, clear=True), patch("dotenv.load_dotenv"):
            import importlib
            import config as cfg_mod
            importlib.reload(cfg_mod)

            with patch("config.acreate_client", new_callable=AsyncMock) as mock_sb, \
                 patch("config.create_anthropic_client") as mock_ant:
                mock_supabase = MagicMock()
                mock_anthropic = MagicMock()
                mock_sb.return_value = mock_supabase
                mock_ant.return_value = mock_anthropic

                import main as main_mod
                importlib.reload(main_mod)

                # Invoke the lifespan directly — no need for ASGI lifecycle
                async with main_mod.lifespan(main_mod.app):
                    assert hasattr(main_mod.app.state, "supabase")
                    assert hasattr(main_mod.app.state, "anthropic")
                    assert main_mod.app.state.supabase is mock_supabase
                    assert main_mod.app.state.anthropic is mock_anthropic

    async def test_lifespan_calls_client_factories_once(self):
        """Lifespan calls acreate_client and create_anthropic_client exactly once."""
        with patch.dict("os.environ", _VALID_ENV, clear=True), patch("dotenv.load_dotenv"):
            import importlib
            import config as cfg_mod
            importlib.reload(cfg_mod)

            with patch("config.acreate_client", new_callable=AsyncMock) as mock_sb, \
                 patch("config.create_anthropic_client") as mock_ant:
                mock_sb.return_value = MagicMock()
                mock_ant.return_value = MagicMock()

                import main as main_mod
                importlib.reload(main_mod)

                async with main_mod.lifespan(main_mod.app):
                    pass  # lifespan ran

                mock_sb.assert_called_once()
                mock_ant.assert_called_once()

    @patch("main.run_agent", new_callable=AsyncMock, return_value="")
    async def test_agent_endpoint_uses_state_clients(self, mock_run_agent, app, client):
        """The endpoint must pass the clients from app.state to run_agent."""
        await client.post(
            "/agent",
            json={
                "space_id": TEST_SPACE_ID,
                "message": "Hi",
                "user_id": TEST_USER_ID,
            },
            headers={"Authorization": "Bearer test-token"},
        )

        mock_run_agent.assert_called_once()
        # First two positional args must be the state clients
        call_args = mock_run_agent.call_args[0]
        assert call_args[0] is app.state.supabase
        assert call_args[1] is app.state.anthropic


class TestAdminDomusContext:
    """GET /admin/domus-context returns a Domus agent context snapshot."""

    async def test_requires_auth(self, client):
        resp = await client.get(
            f"/admin/domus-context?space_id={TEST_SPACE_ID}&user_id={TEST_USER_ID}"
        )
        assert resp.status_code == 401

    async def test_returns_snapshot(self, app, client):
        """Returns 200 with the expected top-level structure."""
        app.state.supabase = MockSupabaseClient()
        app.state.supabase.set_table_response("entities", [])
        app.state.supabase.set_table_response("spaces", [])
        app.state.supabase.set_table_response("users", [])

        with (
            patch("main.get_entity_index", new=AsyncMock(return_value=[])),
            patch("main.get_recent_turns", new=AsyncMock(return_value=[])),
            patch("main.get_space_info", new=AsyncMock(return_value={"name": "Test Space"})),
            patch("main.get_user_profile", new=AsyncMock(return_value={"name": "Andrei", "username": "andrei"})),
            patch("main.get_conversation_summaries", new=AsyncMock(return_value=[])),
            patch("main.get_personality_traits", new=AsyncMock(return_value=[])),
            patch("main.get_user_facts", new=AsyncMock(return_value=[])),
        ):
            resp = await client.get(
                f"/admin/domus-context?space_id={TEST_SPACE_ID}&user_id={TEST_USER_ID}",
                headers={"Authorization": "Bearer test-token"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "domus"
        assert data["space_id"] == TEST_SPACE_ID
        assert data["user_id"] == TEST_USER_ID
        assert "assembled_at" in data

        blocks = data["blocks"]
        assert "static" in blocks
        assert "semi_static" in blocks
        assert "dynamic" in blocks

        assert blocks["static"]["cached"] is True
        assert "base_instructions" in blocks["static"]["sections"]
        assert "iframe_builder_context" in blocks["static"]["sections"]
        assert blocks["static"]["chars"] > 0

        assert blocks["semi_static"]["cached"] is True
        assert blocks["semi_static"]["space"]["name"] == "Test Space"
        assert blocks["semi_static"]["entity_index"]["total"] == 0

        assert blocks["dynamic"]["cached"] is False
        assert blocks["dynamic"]["session_window_minutes"] == 90

        ps = data["prompt_structure"]
        assert ps["total_chars"] > 0
        assert ps["token_estimate"] == ps["total_chars"] // 4
        assert "model" in ps
        assert ps["blocks"]["static"]["cached"] is True
        assert ps["blocks"]["semi_static"]["cached"] is True
        assert ps["blocks"]["dynamic"]["cached"] is False

        health = data["health"]
        assert "active_turn_count" in health
        assert "compaction_threshold" in health
        assert health["compaction_threshold"] == 40
        assert health["compaction_needed"] is False

    async def test_invalid_token_rejected(self, client):
        resp = await client.get(
            f"/admin/domus-context?space_id={TEST_SPACE_ID}&user_id={TEST_USER_ID}",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401


class TestAdminBuilderContext:
    """GET /admin/builder-context returns a Builder agent prompt + app snapshot."""

    async def test_requires_auth(self, client):
        resp = await client.get(
            f"/admin/builder-context?space_id={TEST_SPACE_ID}&user_id={TEST_USER_ID}"
        )
        assert resp.status_code == 401

    async def test_returns_prompt_structure(self, app, client):
        """Returns 200 with prompt sections and empty app lists when no apps exist."""
        app.state.supabase = MockSupabaseClient()
        app.state.supabase.set_table_response("entities", [])

        resp = await client.get(
            f"/admin/builder-context?space_id={TEST_SPACE_ID}&user_id={TEST_USER_ID}",
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"] == "builder"
        assert data["space_id"] == TEST_SPACE_ID
        assert data["user_id"] == TEST_USER_ID
        assert "assembled_at" in data

        ps = data["prompt_structure"]
        assert ps["total_chars"] > 0
        assert ps["token_estimate"] == ps["total_chars"] // 4
        sections = ps["sections"]
        assert "role_and_process" in sections
        assert "component_catalog" in sections
        assert "action_dsl" in sections
        assert "app_examples" in sections
        assert "design_context" in sections
        assert ps["tools"] == ["define_app", "finish_build"]

        assert data["declarative_apps"] == []
        assert data["iframe_apps"] == []

    async def test_categorizes_declarative_apps(self, app, client):
        """Apps with state._def are listed under declarative_apps."""
        app_entity = {
            "id": "app-123",
            "summary": "Habit tracker",
            "state": {
                "_def": {
                    "view": [{"type": "heading"}, {"type": "checklist"}],
                    "actions": {"toggle_habit": {}, "add_habit": {}},
                },
                "_meta": {"name": "Habits", "description": "Track daily habits"},
            },
            "updated_at": "2026-03-01T12:00:00Z",
        }
        app.state.supabase = MockSupabaseClient()
        app.state.supabase.set_table_response("entities", [app_entity])

        resp = await client.get(
            f"/admin/builder-context?space_id={TEST_SPACE_ID}&user_id={TEST_USER_ID}",
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["declarative_apps"]) == 1
        da = data["declarative_apps"][0]
        assert da["entity_id"] == "app-123"
        assert da["name"] == "Habits"
        assert da["component_count"] == 2
        assert da["action_count"] == 2
        assert da["has_error"] is False
        assert data["iframe_apps"] == []

    async def test_categorizes_iframe_apps(self, app, client):
        """Apps with state._code are listed under iframe_apps."""
        app_entity = {
            "id": "app-456",
            "summary": "Calculator",
            "state": {
                "_code": "function App() { return <div>Calculator</div> }",
                "_meta": {"name": "Calc"},
            },
            "updated_at": "2026-03-01T10:00:00Z",
        }
        app.state.supabase = MockSupabaseClient()
        app.state.supabase.set_table_response("entities", [app_entity])

        resp = await client.get(
            f"/admin/builder-context?space_id={TEST_SPACE_ID}&user_id={TEST_USER_ID}",
            headers={"Authorization": "Bearer test-token"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["declarative_apps"] == []
        assert len(data["iframe_apps"]) == 1
        ia = data["iframe_apps"][0]
        assert ia["entity_id"] == "app-456"
        assert ia["name"] == "Calc"


# ---------------------------------------------------------------------------
# TestConcurrentTurnLimit
# ---------------------------------------------------------------------------


class TestConcurrentTurnLimit:
    """POST /agent returns 429 when a user's concurrent turn slot is exhausted."""

    async def test_concurrent_limit_returns_429(self, app, client):
        """Pre-acquiring all FREE tier slots forces the next request to 429."""
        import uuid as _uuid
        from agent.usage import acquire_turn_slot, release_turn_slot, Tier

        # Use a fresh user_id to avoid state pollution from other tests
        user_id = str(_uuid.uuid4())

        # FREE tier allows 1 concurrent turn — acquire it
        await acquire_turn_slot(user_id, Tier.FREE)

        try:
            resp = await client.post(
                "/agent",
                json={
                    "space_id": TEST_SPACE_ID,
                    "message": "Hello",
                    "user_id": user_id,
                },
                headers={"Authorization": "Bearer test-token"},
            )
            assert resp.status_code == 429
            data = resp.json()
            assert data["error"] == "concurrent_limit"
        finally:
            await release_turn_slot(user_id)
