"""Tests for agent/tools.py — tool definitions and tool function implementations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.tools import TOOL_DEFINITIONS
from agent.tools import create_entity, read_entity, query_entities, update_entity, execute_tool
from tests.conftest import TEST_SPACE_ID, TEST_USER_ID, _make_entity, MockSupabaseClient, MockQueryBuilder


class TestToolDefinitionsStructure:
    """TOOL_DEFINITIONS is a well-formed list of tool dicts."""

    def test_is_list_of_dicts(self):
        assert isinstance(TOOL_DEFINITIONS, list)
        for defn in TOOL_DEFINITIONS:
            assert isinstance(defn, dict)

    def test_each_definition_has_required_keys(self):
        for defn in TOOL_DEFINITIONS:
            assert "name" in defn, f"Missing 'name' in {defn}"
            assert "description" in defn, f"Missing 'description' in {defn}"
            assert "input_schema" in defn, f"Missing 'input_schema' in {defn}"

    def test_tool_names_match_exactly(self):
        names = [defn["name"] for defn in TOOL_DEFINITIONS]
        expected = [
            "create_entity", "update_entity", "query_entities", "read_entity",
            "get_entity_schema", "call_entity_tool", "build_app", "update_app",
            "web_search",
        ]
        assert names == expected

    def test_last_tool_definition_has_cache_control(self):
        """TOOL_DEFINITIONS[-1] must have cache_control={'type':'ephemeral'} for Anthropic KV caching."""
        last_tool = TOOL_DEFINITIONS[-1]
        assert "cache_control" in last_tool
        assert last_tool["cache_control"] == {"type": "ephemeral"}

    def test_each_input_schema_is_object_with_properties(self):
        for defn in TOOL_DEFINITIONS:
            schema = defn["input_schema"]
            assert schema["type"] == "object", f"{defn['name']} schema type is not 'object'"
            assert "properties" in schema, f"{defn['name']} schema missing 'properties'"


class TestCreateEntity:
    """create_entity tool definition."""

    def _get_defn(self):
        return next(d for d in TOOL_DEFINITIONS if d["name"] == "create_entity")

    def test_requires_type(self):
        defn = self._get_defn()
        assert defn["input_schema"]["required"] == ["type"]

    def test_has_expected_properties(self):
        defn = self._get_defn()
        props = defn["input_schema"]["properties"]
        expected_props = {"type", "content", "presentation", "position", "size", "state", "summary"}
        assert set(props.keys()) == expected_props

    def test_presentation_enum_has_folder_not_sidebar(self):
        defn = self._get_defn()
        enum = defn["input_schema"]["properties"]["presentation"]["enum"]
        assert "folder" in enum
        assert "sidebar" not in enum
        assert set(enum) == {"window", "card", "folder", "hidden"}


class TestUpdateEntity:
    """update_entity tool definition."""

    def _get_defn(self):
        return next(d for d in TOOL_DEFINITIONS if d["name"] == "update_entity")

    def test_requires_id(self):
        defn = self._get_defn()
        assert defn["input_schema"]["required"] == ["id"]

    def test_has_expected_properties(self):
        defn = self._get_defn()
        props = defn["input_schema"]["properties"]
        expected_props = {"id", "content", "state", "summary", "position", "size", "presentation"}
        assert set(props.keys()) == expected_props

    def test_presentation_enum_has_folder_not_sidebar(self):
        defn = self._get_defn()
        enum = defn["input_schema"]["properties"]["presentation"]["enum"]
        assert "folder" in enum
        assert "sidebar" not in enum


class TestQueryEntities:
    """query_entities tool definition."""

    def _get_defn(self):
        return next(d for d in TOOL_DEFINITIONS if d["name"] == "query_entities")

    def test_no_required_fields(self):
        defn = self._get_defn()
        assert "required" not in defn["input_schema"]

    def test_has_expected_properties(self):
        defn = self._get_defn()
        props = defn["input_schema"]["properties"]
        expected_props = {
            "type", "search", "presentation",
            "created_after", "created_before",
            "include_archived", "limit",
        }
        assert set(props.keys()) == expected_props

    def test_search_description_says_summary_only(self):
        """Search description should say 'summary' not 'content and summary'."""
        defn = self._get_defn()
        desc = defn["input_schema"]["properties"]["search"]["description"]
        assert "summary" in desc
        assert "content and summary" not in desc


class TestReadEntity:
    """read_entity tool definition."""

    def _get_defn(self):
        return next(d for d in TOOL_DEFINITIONS if d["name"] == "read_entity")

    def test_requires_id(self):
        defn = self._get_defn()
        assert defn["input_schema"]["required"] == ["id"]

    def test_has_expected_properties(self):
        defn = self._get_defn()
        props = defn["input_schema"]["properties"]
        assert set(props.keys()) == {"id"}


# ---------------------------------------------------------------------------
# Tool function tests (tasks 1.2–1.5)
# ---------------------------------------------------------------------------


def _make_http_mock(MockClient, status_code=201, json_data=None, text=""):
    """Helper: wire up a MockClient that returns a canned HTTP response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data if json_data is not None else {}
    mock_resp.text = text
    mock_http = AsyncMock()
    mock_http.post.return_value = mock_resp
    mock_http.patch.return_value = mock_resp
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    MockClient.return_value = mock_http
    return mock_http


class TestCreateEntityFunction:
    """create_entity POSTs to Next.js /api/entities."""

    @patch("httpx.AsyncClient")
    async def test_posts_to_correct_url(self, MockClient):
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1", "type": "note"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-123"):
            await create_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert "/api/entities" in call_args[0][0]
        assert call_args[1]["params"] == {"space_id": TEST_SPACE_ID}

    @patch("httpx.AsyncClient")
    async def test_sends_auth_header(self, MockClient):
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-secret"):
            await create_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        call_args = mock_http.post.call_args
        assert call_args[1]["headers"]["Authorization"] == "Bearer tok-secret"

    @patch("httpx.AsyncClient")
    async def test_request_body_includes_required_fields(self, MockClient):
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"type": "note", "content": "hello", "summary": "s", "state": {"x": 1}},
            )

        body = mock_http.post.call_args[1]["json"]
        assert body["created_by"] == "agent"
        assert body["type"] == "note"
        assert body["content"] == "hello"
        assert body["summary"] == "s"
        assert body["state"] == {"x": 1}

    @patch("httpx.AsyncClient")
    async def test_omits_missing_optional_fields(self, MockClient):
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        body = mock_http.post.call_args[1]["json"]
        assert "content" not in body
        assert "summary" not in body
        assert "state" not in body
        assert "position" not in body
        assert "size" not in body

    @patch("httpx.AsyncClient")
    async def test_returns_entity_from_response(self, MockClient):
        entity = {"id": "ent-42", "type": "note", "state": {}}
        _make_http_mock(MockClient, status_code=201, json_data=entity)

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await create_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        assert result == entity

    @patch("httpx.AsyncClient")
    async def test_returns_error_on_non_200(self, MockClient):
        _make_http_mock(MockClient, status_code=422, text='{"error":"validation_failed"}')

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await create_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        assert result["error"] == "create_failed"
        assert result["status"] == 422


class TestReadEntityFunction:
    """Tests for the read_entity tool function."""

    async def test_read_entity_returns_full_row(self, mock_supabase, make_entity):
        """Reading an entity returns the complete row dict."""
        entity = make_entity(
            entity_type="note",
            state={"title": "My Note"},
            summary="A note",
        )
        mock_supabase.set_table_response("entities", entity)  # maybe_single returns dict, not list

        result = await read_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"]},
        )

        assert result["id"] == entity["id"]
        assert result["type"] == "note"
        assert result["state"] == {"title": "My Note"}
        assert result["summary"] == "A note"

    async def test_read_entity_scoped_to_space_id(self, mock_supabase, make_entity):
        """Verify space_id is passed in the query chain."""
        entity = make_entity()
        mock_supabase.set_table_response("entities", entity)

        original_table = mock_supabase.table
        eq_calls = []

        def table_spy(name):
            builder = original_table(name)
            original_eq = builder.eq

            def eq_capture(*args, **kwargs):
                eq_calls.append(args)
                return original_eq(*args, **kwargs)

            builder.eq = eq_capture
            return builder

        mock_supabase.table = table_spy

        await read_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"]},
        )

        # Verify space_id was used in an eq() call
        space_id_used = any(
            len(args) >= 2 and args[0] == "space_id" and args[1] == TEST_SPACE_ID
            for args in eq_calls
        )
        assert space_id_used, f"space_id not found in eq() calls: {eq_calls}"

    async def test_read_entity_not_found_returns_error(self, mock_supabase):
        """When no entity is found, return an error dict."""
        mock_supabase.set_table_response("entities", None)  # maybe_single returns None

        result = await read_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": "nonexistent-id"},
        )

        assert result["error"] == "not_found"
        assert result["id"] == "nonexistent-id"


class TestQueryEntitiesFunction:
    """Tests for the query_entities tool function."""

    async def test_query_entities_filters_by_type(self, mock_supabase, make_entity):
        """When type is provided, an eq('type', ...) filter is applied."""
        entities = [make_entity(entity_type="calendar")]
        mock_supabase.set_table_response("entities", entities)

        original_table = mock_supabase.table
        eq_calls = []

        def table_spy(name):
            builder = original_table(name)
            original_eq = builder.eq

            def eq_capture(*args, **kwargs):
                eq_calls.append(args)
                return original_eq(*args, **kwargs)

            builder.eq = eq_capture
            return builder

        mock_supabase.table = table_spy

        result = await query_entities(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "calendar"},
        )

        type_filter_used = any(
            len(args) >= 2 and args[0] == "type" and args[1] == "calendar"
            for args in eq_calls
        )
        assert type_filter_used, f"type filter not found in eq() calls: {eq_calls}"
        assert "entities" in result

    async def test_query_entities_full_text_search(self, mock_supabase, make_entity):
        """When search is provided, text_search is called."""
        mock_supabase.set_table_response("entities", [])

        original_table = mock_supabase.table
        text_search_calls = []

        def table_spy(name):
            builder = original_table(name)
            original_ts = builder.text_search

            def ts_capture(*args, **kwargs):
                text_search_calls.append(args)
                return original_ts(*args, **kwargs)

            builder.text_search = ts_capture
            return builder

        mock_supabase.table = table_spy

        await query_entities(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"search": "meeting notes"},
        )

        assert len(text_search_calls) == 1
        assert text_search_calls[0][0] == "summary"
        assert text_search_calls[0][1] == "meeting notes"

    async def test_query_entities_excludes_archived_by_default(self, mock_supabase, make_entity):
        """By default, archived entities are excluded."""
        mock_supabase.set_table_response("entities", [])

        original_table = mock_supabase.table
        eq_calls = []

        def table_spy(name):
            builder = original_table(name)
            original_eq = builder.eq

            def eq_capture(*args, **kwargs):
                eq_calls.append(args)
                return original_eq(*args, **kwargs)

            builder.eq = eq_capture
            return builder

        mock_supabase.table = table_spy

        await query_entities(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {},
        )

        archived_filter = any(
            len(args) >= 2 and args[0] == "archived" and args[1] is False
            for args in eq_calls
        )
        assert archived_filter, f"archived=False filter not found in eq() calls: {eq_calls}"

    async def test_query_entities_respects_limit(self, mock_supabase, make_entity):
        """limit param is forwarded to the query builder."""
        mock_supabase.set_table_response("entities", [])

        original_table = mock_supabase.table
        limit_calls = []

        def table_spy(name):
            builder = original_table(name)
            original_limit = builder.limit

            def limit_capture(*args, **kwargs):
                limit_calls.append(args)
                return original_limit(*args, **kwargs)

            builder.limit = limit_capture
            return builder

        mock_supabase.table = table_spy

        await query_entities(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"limit": 5},
        )

        assert len(limit_calls) == 1
        assert limit_calls[0][0] == 5

    async def test_query_entities_returns_lightweight_summaries(self, mock_supabase, make_entity):
        """Verify only id, type, summary, presentation, created_at are selected."""
        mock_supabase.set_table_response("entities", [])

        original_table = mock_supabase.table
        select_calls = []

        def table_spy(name):
            builder = original_table(name)
            original_select = builder.select

            def select_capture(*args, **kwargs):
                select_calls.append(args)
                return original_select(*args, **kwargs)

            builder.select = select_capture
            return builder

        mock_supabase.table = table_spy

        await query_entities(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {},
        )

        assert len(select_calls) == 1
        select_str = select_calls[0][0]
        for field in ("id", "type", "summary", "presentation", "created_at"):
            assert field in select_str, f"'{field}' not in select string: {select_str}"
        # Should NOT select 'state' (that's what read_entity is for)
        assert "state" not in select_str


class TestUpdateEntityFunction:
    """update_entity PATCHes Next.js /api/entities/{id}."""

    @patch("httpx.AsyncClient")
    async def test_patches_correct_url(self, MockClient):
        mock_http = _make_http_mock(MockClient, status_code=200, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-123"):
            await update_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"id": "ent-1", "summary": "new"})

        mock_http.patch.assert_called_once()
        call_args = mock_http.patch.call_args
        assert "/api/entities/ent-1" in call_args[0][0]
        assert call_args[1]["params"] == {"space_id": TEST_SPACE_ID}

    @patch("httpx.AsyncClient")
    async def test_sends_auth_header(self, MockClient):
        mock_http = _make_http_mock(MockClient, status_code=200, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-secret"):
            await update_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"id": "ent-1", "summary": "x"})

        call_args = mock_http.patch.call_args
        assert call_args[1]["headers"]["Authorization"] == "Bearer tok-secret"

    @patch("httpx.AsyncClient")
    async def test_request_body_includes_only_provided_fields(self, MockClient):
        mock_http = _make_http_mock(MockClient, status_code=200, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await update_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"id": "ent-1", "summary": "new", "state": {"x": 1}},
            )

        body = mock_http.patch.call_args[1]["json"]
        assert body == {"summary": "new", "state": {"x": 1}}
        assert "id" not in body

    @patch("httpx.AsyncClient")
    async def test_returns_entity_from_response(self, MockClient):
        entity = {"id": "ent-1", "type": "note", "state": {"x": 1}}
        _make_http_mock(MockClient, status_code=200, json_data=entity)

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await update_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"id": "ent-1", "summary": "new"})

        assert result == entity

    @patch("httpx.AsyncClient")
    async def test_returns_error_on_non_200(self, MockClient):
        _make_http_mock(MockClient, status_code=500, text="Internal Server Error")

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await update_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"id": "ent-1"})

        assert result["error"] == "update_failed"
        assert result["status"] == 500

    @patch("httpx.AsyncClient")
    async def test_returns_409_error_on_folder_has_children(self, MockClient):
        conflict = {"error": "folder_has_children", "child_ids": ["c1", "c2"], "count": 2}
        _make_http_mock(MockClient, status_code=409, json_data=conflict)

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await update_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"id": "ent-1", "presentation": "hidden"},
            )

        assert result["error"] == "folder_has_children"
        assert result["count"] == 2


# ---------------------------------------------------------------------------
# execute_tool dispatcher tests (task 1.6)
# ---------------------------------------------------------------------------


class TestExecuteTool:
    """Tests for the execute_tool dispatcher."""

    @patch("agent.tools.create_entity", new_callable=AsyncMock)
    async def test_dispatch_create_entity(self, mock_fn):
        """execute_tool('create_entity', ...) routes to create_entity."""
        mock_fn.return_value = {"id": "ent-1", "type": "note"}

        result = await execute_tool(
            None, "create_entity", {"type": "note"},
            TEST_SPACE_ID, TEST_USER_ID,
        )

        mock_fn.assert_called_once_with(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})
        assert result["type"] == "note"

    async def test_dispatch_read_entity(self, mock_supabase, make_entity):
        """execute_tool('read_entity', ...) routes to read_entity and returns entity data."""
        entity = make_entity(entity_type="note", state={"title": "Read me"})
        mock_supabase.set_table_response("entities", entity)  # maybe_single returns dict

        result = await execute_tool(
            mock_supabase, "read_entity", {"id": entity["id"]},
            TEST_SPACE_ID, TEST_USER_ID,
        )

        assert result["id"] == entity["id"]
        assert result["state"] == {"title": "Read me"}

    async def test_dispatch_query_entities(self, mock_supabase, make_entity):
        """execute_tool('query_entities', ...) routes to query_entities and returns results."""
        entities = [make_entity(entity_type="calendar")]
        mock_supabase.set_table_response("entities", entities)

        result = await execute_tool(
            mock_supabase, "query_entities", {"type": "calendar"},
            TEST_SPACE_ID, TEST_USER_ID,
        )

        assert "entities" in result

    @patch("agent.tools.update_entity", new_callable=AsyncMock)
    async def test_dispatch_update_entity(self, mock_fn):
        """execute_tool('update_entity', ...) routes to update_entity."""
        mock_fn.return_value = {"id": "ent-1", "state": {"title": "Updated"}}

        result = await execute_tool(
            None, "update_entity",
            {"id": "ent-1", "state": {"title": "Updated"}},
            TEST_SPACE_ID, TEST_USER_ID,
        )

        mock_fn.assert_called_once()
        assert result["state"]["title"] == "Updated"

    async def test_dispatch_unknown_tool_returns_error(self, mock_supabase):
        """Calling execute_tool with an unknown tool name returns an error dict."""
        result = await execute_tool(
            mock_supabase, "nonexistent_tool", {},
            TEST_SPACE_ID, TEST_USER_ID,
        )

        assert result == {"error": "unknown_tool", "tool": "nonexistent_tool"}


# ---------------------------------------------------------------------------
# Image generation wiring in create_entity
# ---------------------------------------------------------------------------


class TestCreateEntityImageWiring:
    """When type='image' and state.generation_prompt is set, create_entity
    calls generate_image and enriches the entity state."""

    @patch("httpx.AsyncClient")
    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_calls_generate_image(self, mock_gen, MockClient):
        """create_entity with type='image' + generation_prompt triggers image generation."""
        mock_gen.return_value = {
            "storage_path": f"{TEST_SPACE_ID}/abc.png",
            "public_url": "https://test.supabase.co/storage/v1/object/public/images/abc.png",
            "width": 1024,
            "height": 1024,
        }
        _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1", "type": "image"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"type": "image", "state": {"generation_prompt": "a sunset"}},
            )

        mock_gen.assert_called_once_with("a sunset", TEST_SPACE_ID, None, TEST_USER_ID)

    @patch("httpx.AsyncClient")
    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_enriches_state(self, mock_gen, MockClient):
        """State POSTed to frontend includes image_url, width, height from generate_image."""
        mock_gen.return_value = {
            "storage_path": f"{TEST_SPACE_ID}/abc.png",
            "public_url": "https://example.com/image.png",
            "width": 512,
            "height": 768,
        }
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"type": "image", "state": {"generation_prompt": "a cat"}},
            )

        body = mock_http.post.call_args[1]["json"]
        assert body["state"]["image_url"] == "https://example.com/image.png"
        assert body["state"]["width"] == 512
        assert body["state"]["height"] == 768
        assert body["state"]["generation_prompt"] == "a cat"

    @patch("httpx.AsyncClient")
    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_failure_sets_generation_error(self, mock_gen, MockClient):
        """On generate_image failure, entity is still created with generation_error in state."""
        mock_gen.side_effect = RuntimeError("Gemini API failed")
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"type": "image", "state": {"generation_prompt": "failing prompt"}},
            )

        body = mock_http.post.call_args[1]["json"]
        assert "generation_error" in body["state"]
        assert "Gemini API failed" in body["state"]["generation_error"]
        assert "image_url" not in body["state"]

    @patch("httpx.AsyncClient")
    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_non_image_entity_does_not_trigger_generation(self, mock_gen, MockClient):
        """create_entity with type='note' should not call generate_image."""
        _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"type": "note", "state": {"title": "Hello"}},
            )

        mock_gen.assert_not_called()

    @patch("httpx.AsyncClient")
    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_without_generation_prompt_skips_generation(self, mock_gen, MockClient):
        """Image entity without generation_prompt should not trigger generation."""
        _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"type": "image", "state": {"image_url": "https://existing.com/img.png"}},
            )

        mock_gen.assert_not_called()


class TestCreateEntityInternalTypesBypass:
    """Internal entity types bypass the frontend and insert directly to Supabase."""

    def _make_supabase_with_insert(self):
        """Return a mock Supabase client that records insert calls."""
        inserted = []

        class _QB:
            def insert(self, row):
                inserted.append(row)
                return self

            def eq(self, *a, **kw):
                return self

            async def execute(self):
                result = MagicMock()
                result.data = [{"id": "ent-new", **inserted[-1]}] if inserted else []
                return result

        class _Client:
            def table(self, name):
                return _QB()

        return _Client(), inserted

    @patch("httpx.AsyncClient")
    async def test_internal_type_inserts_direct_to_supabase(self, MockClient):
        """conversation_turn → Supabase insert is called, httpx is NOT called."""
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})
        client, inserted = self._make_supabase_with_insert()

        await create_entity(client, TEST_SPACE_ID, TEST_USER_ID, {"type": "conversation_turn"})

        mock_http.post.assert_not_called()
        assert len(inserted) == 1

    @patch("httpx.AsyncClient")
    async def test_internal_type_always_sets_presentation_hidden(self, MockClient):
        """Inserted row for internal type always has presentation='hidden'."""
        _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})
        client, inserted = self._make_supabase_with_insert()

        await create_entity(
            client, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "fact", "presentation": "window"},  # caller tries to override — must be ignored
        )

        assert inserted[0]["presentation"] == "hidden"

    @patch("httpx.AsyncClient")
    async def test_user_facing_type_posts_to_frontend(self, MockClient):
        """note type → httpx POST is called, Supabase insert is NOT called."""
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})
        client, inserted = self._make_supabase_with_insert()

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(client, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        mock_http.post.assert_called_once()
        assert len(inserted) == 0

    @pytest.mark.parametrize("entity_type", [
        "conversation_turn",
        "conversation_summary",
        "fact",
        "personality_trait",
        "edge",
    ])
    @patch("httpx.AsyncClient")
    async def test_all_internal_types_bypass_frontend(self, MockClient, entity_type):
        """All 5 internal types insert directly to Supabase without calling httpx."""
        mock_http = _make_http_mock(MockClient, status_code=201, json_data={"id": "ent-1"})
        client, inserted = self._make_supabase_with_insert()

        await create_entity(client, TEST_SPACE_ID, TEST_USER_ID, {"type": entity_type})

        mock_http.post.assert_not_called()
        assert len(inserted) == 1
        assert inserted[0]["presentation"] == "hidden"


# ---------------------------------------------------------------------------


from agent.tools import compute_group_positions


class TestComputeGroupPositions:
    """compute_group_positions tiles N entities in a grid centered at (50, 50)."""

    def test_single_entity_centered(self):
        """1 entity → single position at {x: 50, y: 50}."""
        positions = compute_group_positions(1)
        assert len(positions) == 1
        assert positions[0]["x"] == 50.0
        assert positions[0]["y"] == 50.0
        assert positions[0]["locked"] is False

    def test_four_entities_2x2_grid(self):
        """4 entities → 2x2 grid, all positions distinct, centered around 50/50."""
        positions = compute_group_positions(4)
        assert len(positions) == 4
        # All positions should be distinct
        coords = [(p["x"], p["y"]) for p in positions]
        assert len(set(coords)) == 4
        # Average should be near center
        avg_x = sum(p["x"] for p in positions) / 4
        avg_y = sum(p["y"] for p in positions) / 4
        assert abs(avg_x - 50) < 1
        assert abs(avg_y - 50) < 1

    def test_nine_entities_3x3_grid(self):
        """9 entities → 3x3 grid."""
        positions = compute_group_positions(9)
        assert len(positions) == 9
        coords = [(p["x"], p["y"]) for p in positions]
        assert len(set(coords)) == 9

    def test_ten_entities_grid(self):
        """10 entities → 4 cols (ceil(sqrt(10))=4), 3 rows."""
        positions = compute_group_positions(10)
        assert len(positions) == 10
        coords = [(p["x"], p["y"]) for p in positions]
        assert len(set(coords)) == 10

    def test_positions_clamped_to_valid_range(self):
        """All positions should be within [5, 95] range."""
        # Large batch that could push positions outside bounds
        positions = compute_group_positions(25)
        for p in positions:
            assert 5 <= p["x"] <= 95, f"x={p['x']} out of range"
            assert 5 <= p["y"] <= 95, f"y={p['y']} out of range"

    def test_custom_viewport_changes_spacing(self):
        """Custom viewport dimensions should change percentage spacing."""
        pos_default = compute_group_positions(4)
        pos_wide = compute_group_positions(4, viewport={"width": 2880, "height": 900})
        # Wider viewport → smaller percentage spacing between cards
        # So positions should be closer together on x-axis
        x_spread_default = max(p["x"] for p in pos_default) - min(p["x"] for p in pos_default)
        x_spread_wide = max(p["x"] for p in pos_wide) - min(p["x"] for p in pos_wide)
        assert x_spread_wide < x_spread_default

    def test_none_viewport_uses_defaults(self):
        """None viewport should use 1440x900 defaults (same as no viewport)."""
        pos_none = compute_group_positions(4, viewport=None)
        pos_default = compute_group_positions(4)
        assert pos_none == pos_default

    def test_all_positions_have_locked_false(self):
        """Every position dict should have locked=False."""
        positions = compute_group_positions(6)
        for p in positions:
            assert p["locked"] is False


# ---------------------------------------------------------------------------
# Entity schema discovery and tool call handler tests
# ---------------------------------------------------------------------------


from agent.tools import get_entity_schema, call_entity_tool


class TestGetEntitySchemaDefinition:
    """get_entity_schema tool definition."""

    def _get_defn(self):
        return next(d for d in TOOL_DEFINITIONS if d["name"] == "get_entity_schema")

    def test_requires_entity_id(self):
        defn = self._get_defn()
        assert defn["input_schema"]["required"] == ["entity_id"]

    def test_has_expected_properties(self):
        defn = self._get_defn()
        props = defn["input_schema"]["properties"]
        assert set(props.keys()) == {"entity_id"}

    def test_has_description(self):
        defn = self._get_defn()
        assert "discover" in defn["description"].lower() or "schema" in defn["description"].lower()


class TestCallEntityToolDefinition:
    """call_entity_tool tool definition."""

    def _get_defn(self):
        return next(d for d in TOOL_DEFINITIONS if d["name"] == "call_entity_tool")

    def test_requires_entity_id_and_tool_name(self):
        defn = self._get_defn()
        assert set(defn["input_schema"]["required"]) == {"entity_id", "tool_name"}

    def test_has_expected_properties(self):
        defn = self._get_defn()
        props = defn["input_schema"]["properties"]
        assert set(props.keys()) == {"entity_id", "tool_name", "params"}

    def test_params_is_optional(self):
        defn = self._get_defn()
        assert "params" not in defn["input_schema"]["required"]


class TestGetEntitySchemaHandler:
    """get_entity_schema handler calls frontend HTTP endpoint."""

    @patch("httpx.AsyncClient")
    async def test_calls_correct_url_with_auth(self, MockClient):
        """Handler GETs /api/entities/{id}/schema with Bearer token and space_id."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "entity_id": "ent-1",
            "type": "calendar",
            "tools": [{"name": "set_view"}],
        }

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-123"):
            result = await get_entity_schema(None, TEST_SPACE_ID, TEST_USER_ID, {"entity_id": "ent-1"})

        mock_http.get.assert_called_once()
        call_args = mock_http.get.call_args
        assert "/api/entities/ent-1/schema" in call_args[0][0]
        assert call_args[1]["params"] == {"space_id": TEST_SPACE_ID}
        assert call_args[1]["headers"]["Authorization"] == "Bearer tok-123"
        assert result["entity_id"] == "ent-1"

    @patch("httpx.AsyncClient")
    async def test_returns_error_on_non_200(self, MockClient):
        """Non-200 responses produce an error dict."""
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = '{"error":"no_schema"}'

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-123"):
            result = await get_entity_schema(None, TEST_SPACE_ID, TEST_USER_ID, {"entity_id": "ent-1"})

        assert result["error"] == "schema_fetch_failed"
        assert result["status"] == 422


class TestCallEntityToolHandler:
    """call_entity_tool handler calls frontend HTTP endpoint."""

    @patch("httpx.AsyncClient")
    async def test_calls_correct_url_with_auth_and_body(self, MockClient):
        """Handler POSTs /api/entities/{id}/call with Bearer token, space_id, and body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "result": {"view": "week"},
            "summary": "Calendar — week",
            "schema": [],
        }

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-123"):
            result = await call_entity_tool(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"entity_id": "ent-1", "tool_name": "set_view", "params": {"view": "week"}},
            )

        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert "/api/entities/ent-1/call" in call_args[0][0]
        assert call_args[1]["params"] == {"space_id": TEST_SPACE_ID}
        assert call_args[1]["json"] == {"tool_name": "set_view", "params": {"view": "week"}}
        assert result["ok"] is True

    @patch("httpx.AsyncClient")
    async def test_returns_error_on_non_200(self, MockClient):
        """Non-200 responses produce an error dict."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = '{"error":"not_found"}'

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-123"):
            result = await call_entity_tool(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"entity_id": "ent-1", "tool_name": "set_view", "params": {}},
            )

        assert result["error"] == "tool_call_failed"
        assert result["status"] == 404

    @patch("httpx.AsyncClient")
    async def test_defaults_params_to_empty_dict(self, MockClient):
        """When params not provided, sends empty dict."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": True}

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok-123"):
            await call_entity_tool(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"entity_id": "ent-1", "tool_name": "toggle_play"},
            )

        call_args = mock_http.post.call_args
        assert call_args[1]["json"]["params"] == {}


class TestExecuteToolDispatchesNewTools:
    """execute_tool dispatches to new entity-as-mcp handlers."""

    @patch("agent.tools.get_entity_schema", new_callable=AsyncMock)
    async def test_dispatch_get_entity_schema(self, mock_fn):
        mock_fn.return_value = {"entity_id": "ent-1", "tools": []}
        result = await execute_tool(
            None, "get_entity_schema", {"entity_id": "ent-1"},
            TEST_SPACE_ID, TEST_USER_ID,
        )
        mock_fn.assert_called_once_with(None, TEST_SPACE_ID, TEST_USER_ID, {"entity_id": "ent-1"})
        assert result["entity_id"] == "ent-1"

    @patch("agent.tools.call_entity_tool", new_callable=AsyncMock)
    async def test_dispatch_call_entity_tool(self, mock_fn):
        mock_fn.return_value = {"ok": True}
        result = await execute_tool(
            None, "call_entity_tool",
            {"entity_id": "ent-1", "tool_name": "set_view", "params": {"view": "week"}},
            TEST_SPACE_ID, TEST_USER_ID,
        )
        mock_fn.assert_called_once()
        assert result["ok"] is True


class TestWebSearch:
    """web_search tool — definition, implementation, and dispatcher."""

    def test_definition_present_in_tool_definitions(self):
        names = [d["name"] for d in TOOL_DEFINITIONS]
        assert "web_search" in names

    def test_definition_has_required_keys(self):
        defn = next(d for d in TOOL_DEFINITIONS if d["name"] == "web_search")
        assert "description" in defn
        assert "input_schema" in defn

    def test_definition_requires_query(self):
        defn = next(d for d in TOOL_DEFINITIONS if d["name"] == "web_search")
        assert defn["input_schema"]["required"] == ["query"]

    def test_definition_has_focus_enum(self):
        defn = next(d for d in TOOL_DEFINITIONS if d["name"] == "web_search")
        props = defn["input_schema"]["properties"]
        assert "focus" in props
        assert set(props["focus"]["enum"]) == {"general", "academic", "news"}

    @patch("httpx.AsyncClient")
    async def test_successful_search_returns_answer_and_citations(self, MockClient):
        """Happy path: Perplexity returns answer + citations list."""
        from agent.tools import web_search

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "The answer is 42."}}],
            "citations": ["https://example.com/source1", "https://example.com/source2"],
        }

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.PERPLEXITY_API_KEY", "pplx-test-key"):
            result = await web_search(
                None, TEST_SPACE_ID, TEST_USER_ID, {"query": "meaning of life"}
            )

        assert result["answer"] == "The answer is 42."
        assert result["citations"] == [
            {"url": "https://example.com/source1"},
            {"url": "https://example.com/source2"},
        ]

    @patch("httpx.AsyncClient")
    async def test_missing_api_key_returns_error(self, MockClient):
        """When PERPLEXITY_API_KEY is empty, return unavailable error without HTTP call."""
        from agent.tools import web_search

        with patch("config.PERPLEXITY_API_KEY", ""):
            result = await web_search(
                None, TEST_SPACE_ID, TEST_USER_ID, {"query": "test"}
            )

        assert result["error"] == "web_search_unavailable"
        assert "message" in result
        MockClient.assert_not_called()

    @patch("httpx.AsyncClient")
    async def test_non_200_response_returns_error(self, MockClient):
        """Non-200 HTTP response returns web_search_failed error with status."""
        from agent.tools import web_search

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.PERPLEXITY_API_KEY", "pplx-test-key"):
            result = await web_search(
                None, TEST_SPACE_ID, TEST_USER_ID, {"query": "test"}
            )

        assert result["error"] == "web_search_failed"
        assert result["status"] == 429

    @patch("httpx.AsyncClient")
    async def test_academic_focus_uses_sonar_pro_model(self, MockClient):
        """focus='academic' selects sonar-pro model."""
        from agent.tools import web_search

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Academic answer."}}],
            "citations": [],
        }

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.PERPLEXITY_API_KEY", "pplx-test-key"):
            await web_search(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"query": "quantum entanglement", "focus": "academic"},
            )

        call_kwargs = mock_http.post.call_args[1]
        assert call_kwargs["json"]["model"] == "sonar-pro"

    @patch("httpx.AsyncClient")
    async def test_general_focus_uses_sonar_model(self, MockClient):
        """focus='general' (default) selects sonar model."""
        from agent.tools import web_search

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "General answer."}}],
            "citations": [],
        }

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.PERPLEXITY_API_KEY", "pplx-test-key"):
            await web_search(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"query": "weather today"},
            )

        call_kwargs = mock_http.post.call_args[1]
        assert call_kwargs["json"]["model"] == "sonar"

    @patch("httpx.AsyncClient")
    async def test_execute_tool_dispatches_web_search(self, MockClient):
        """execute_tool('web_search', ...) returns a result, not 'unknown_tool'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Dispatched."}}],
            "citations": [],
        }

        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.PERPLEXITY_API_KEY", "pplx-test-key"):
            result = await execute_tool(
                None, "web_search", {"query": "test dispatch"},
                TEST_SPACE_ID, TEST_USER_ID,
            )

        assert result.get("error") != "unknown_tool"
        assert "answer" in result


# ---------------------------------------------------------------------------
# TestPerplexityValidation
# ---------------------------------------------------------------------------


class TestPerplexityValidation:
    """web_search validates Perplexity response shape before accessing nested keys."""

    @patch("httpx.AsyncClient")
    async def test_web_search_handles_empty_choices(self, MockClient):
        """Returns error dict when Perplexity responds with an empty choices list."""
        from agent.tools import web_search

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [], "citations": []}
        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.PERPLEXITY_API_KEY", "pplx-test-key"):
            result = await web_search(
                None, TEST_SPACE_ID, TEST_USER_ID, {"query": "test"}
            )

        assert result["error"] == "web_search_failed"
        assert "message" in result

    @patch("httpx.AsyncClient")
    async def test_web_search_handles_missing_choices_key(self, MockClient):
        """Returns error dict when Perplexity responds without a 'choices' key."""
        from agent.tools import web_search

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}  # no choices key at all
        mock_http = AsyncMock()
        mock_http.post.return_value = mock_resp
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.PERPLEXITY_API_KEY", "pplx-test-key"):
            result = await web_search(
                None, TEST_SPACE_ID, TEST_USER_ID, {"query": "test"}
            )

        assert result["error"] == "web_search_failed"

    @patch("httpx.AsyncClient")
    async def test_create_entity_handles_timeout(self, MockClient):
        """create_entity returns a clean error dict on httpx.TimeoutException."""
        import httpx as _httpx

        mock_http = AsyncMock()
        mock_http.post.side_effect = _httpx.TimeoutException("Connection timed out")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await create_entity(
                None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"}
            )

        assert result["error"] == "frontend_timeout"
        assert result["tool"] == "create_entity"


class TestGetEntitySchemaHttpxErrors:
    """get_entity_schema handles httpx network errors gracefully."""

    @patch("httpx.AsyncClient")
    async def test_get_entity_schema_handles_timeout(self, MockClient):
        """get_entity_schema returns a clean error dict on httpx.TimeoutException."""
        import httpx as _httpx
        from agent.tools import get_entity_schema

        mock_http = AsyncMock()
        mock_http.get.side_effect = _httpx.TimeoutException("timed out")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await get_entity_schema(
                None, TEST_SPACE_ID, TEST_USER_ID, {"entity_id": "ent-1"}
            )

        assert result["error"] == "frontend_timeout"
        assert result["tool"] == "get_entity_schema"

    @patch("httpx.AsyncClient")
    async def test_get_entity_schema_handles_connect_error(self, MockClient):
        """get_entity_schema returns a clean error dict on httpx.ConnectError."""
        import httpx as _httpx
        from agent.tools import get_entity_schema

        mock_http = AsyncMock()
        mock_http.get.side_effect = _httpx.ConnectError("connection refused")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await get_entity_schema(
                None, TEST_SPACE_ID, TEST_USER_ID, {"entity_id": "ent-1"}
            )

        assert result["error"] == "frontend_unreachable"
        assert result["tool"] == "get_entity_schema"


class TestCallEntityToolHttpxErrors:
    """call_entity_tool handles httpx network errors gracefully."""

    @patch("httpx.AsyncClient")
    async def test_call_entity_tool_handles_timeout(self, MockClient):
        """call_entity_tool returns a clean error dict on httpx.TimeoutException."""
        import httpx as _httpx
        from agent.tools import call_entity_tool

        mock_http = AsyncMock()
        mock_http.post.side_effect = _httpx.TimeoutException("timed out")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await call_entity_tool(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"entity_id": "ent-1", "tool_name": "add_item", "params": {}}
            )

        assert result["error"] == "frontend_timeout"
        assert result["tool"] == "call_entity_tool"

    @patch("httpx.AsyncClient")
    async def test_call_entity_tool_handles_connect_error(self, MockClient):
        """call_entity_tool returns a clean error dict on httpx.ConnectError."""
        import httpx as _httpx
        from agent.tools import call_entity_tool

        mock_http = AsyncMock()
        mock_http.post.side_effect = _httpx.ConnectError("connection refused")
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_http

        with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            result = await call_entity_tool(
                None, TEST_SPACE_ID, TEST_USER_ID,
                {"entity_id": "ent-1", "tool_name": "add_item", "params": {}}
            )

        assert result["error"] == "frontend_unreachable"
        assert result["tool"] == "call_entity_tool"


# ---------------------------------------------------------------------------
# Cache invalidation tests
# ---------------------------------------------------------------------------


from agent.tools import build_app, update_app, call_entity_tool

_INVALIDATE_PATH = "agent.tools._cache_invalidate"


class TestEntityIndexCacheInvalidation:
    """Mutating tools call _cache_invalidate(entity_index:<space_id>) so the next
    prompt build sees the updated entity list immediately — no 60-second stale window."""

    EXPECTED_KEY = f"entity_index:{TEST_SPACE_ID}"

    # --- create_entity (internal type → Supabase) ---

    @patch("httpx.AsyncClient")
    async def test_create_internal_type_invalidates_cache(self, MockClient):
        """create_entity for internal types (fact, etc.) invalidates the entity index cache."""
        _make_http_mock(MockClient, status_code=201, json_data={})

        class _QB:
            def insert(self, row): return self
            async def execute(self): return MagicMock(data=[{"id": "new-1"}])

        class _Client:
            def table(self, _): return _QB()

        with patch(_INVALIDATE_PATH) as mock_inv:
            await create_entity(_Client(), TEST_SPACE_ID, TEST_USER_ID, {"type": "fact"})

        mock_inv.assert_called_once_with(self.EXPECTED_KEY)

    # --- create_entity (user-facing type → frontend POST) ---

    @patch("httpx.AsyncClient")
    async def test_create_user_facing_type_invalidates_cache(self, MockClient):
        """create_entity for user-facing types (note, image, etc.) invalidates on success."""
        _make_http_mock(MockClient, status_code=201, json_data={"id": "new-2"})

        with patch(_INVALIDATE_PATH) as mock_inv, \
             patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        mock_inv.assert_called_once_with(self.EXPECTED_KEY)

    @patch("httpx.AsyncClient")
    async def test_create_user_facing_type_failure_does_not_invalidate(self, MockClient):
        """create_entity on non-200 response does NOT invalidate the cache."""
        _make_http_mock(MockClient, status_code=500, json_data={})

        with patch(_INVALIDATE_PATH) as mock_inv, \
             patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await create_entity(None, TEST_SPACE_ID, TEST_USER_ID, {"type": "note"})

        mock_inv.assert_not_called()

    # --- update_entity (covers deletion: archived=True) ---

    @patch("httpx.AsyncClient")
    async def test_update_entity_invalidates_cache(self, MockClient):
        """update_entity on success (including archived=True / deletion) invalidates cache."""
        _make_http_mock(MockClient, status_code=200, json_data={"id": "ent-1"})

        with patch(_INVALIDATE_PATH) as mock_inv, \
             patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await update_entity(None, TEST_SPACE_ID, TEST_USER_ID,
                                {"id": "ent-1", "archived": True})

        mock_inv.assert_called_once_with(self.EXPECTED_KEY)

    @patch("httpx.AsyncClient")
    async def test_update_entity_failure_does_not_invalidate(self, MockClient):
        """update_entity on non-200 response does NOT invalidate the cache."""
        _make_http_mock(MockClient, status_code=500, json_data={})

        with patch(_INVALIDATE_PATH) as mock_inv, \
             patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await update_entity(None, TEST_SPACE_ID, TEST_USER_ID,
                                {"id": "ent-1", "summary": "new"})

        mock_inv.assert_not_called()

    # --- build_app ---

    async def test_build_app_invalidates_cache(self):
        """build_app invalidates the entity index cache after Supabase insert."""
        class _QB:
            def insert(self, row): return self
            async def execute(self): return MagicMock(data=[{"id": "app-1"}])

        class _Client:
            def table(self, _): return _QB()

        with patch(_INVALIDATE_PATH) as mock_inv:
            await build_app(_Client(), TEST_SPACE_ID, TEST_USER_ID, {
                "name": "Test", "icon": "box", "description": "d",
                "code": "function App(){}", "schema": [], "initial_state": {},
            })

        mock_inv.assert_called_once_with(self.EXPECTED_KEY)

    # --- update_app ---

    async def test_update_app_invalidates_cache(self):
        """update_app invalidates the entity index cache after Supabase update."""
        class _QB:
            def __init__(self): self._data = {"id": "app-1", "state": {}}
            def select(self, *a): return self
            def update(self, *a): return self
            def eq(self, *a): return self
            def maybe_single(self): return self
            async def execute(self): return MagicMock(data=self._data)

        class _Client:
            def table(self, _): return _QB()

        with patch(_INVALIDATE_PATH) as mock_inv:
            await update_app(_Client(), TEST_SPACE_ID, TEST_USER_ID,
                             {"entity_id": "app-1", "code": "function App(){return null}"})

        mock_inv.assert_called_once_with(self.EXPECTED_KEY)

    # --- call_entity_tool ---

    @patch("httpx.AsyncClient")
    async def test_call_entity_tool_invalidates_cache(self, MockClient):
        """call_entity_tool on success invalidates the entity index cache."""
        _make_http_mock(MockClient, status_code=200, json_data={"state": {}})

        with patch(_INVALIDATE_PATH) as mock_inv, \
             patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await call_entity_tool(None, TEST_SPACE_ID, TEST_USER_ID,
                                   {"entity_id": "ent-1", "tool_name": "do_thing"})

        mock_inv.assert_called_once_with(self.EXPECTED_KEY)

    @patch("httpx.AsyncClient")
    async def test_call_entity_tool_failure_does_not_invalidate(self, MockClient):
        """call_entity_tool on non-200 does NOT invalidate the cache."""
        _make_http_mock(MockClient, status_code=500, json_data={})

        with patch(_INVALIDATE_PATH) as mock_inv, \
             patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
             patch("config.DOMUS_SERVICE_TOKEN", "tok"):
            await call_entity_tool(None, TEST_SPACE_ID, TEST_USER_ID,
                                   {"entity_id": "ent-1", "tool_name": "do_thing"})

        mock_inv.assert_not_called()


# ---------------------------------------------------------------------------
# UI Action Mirroring — execute_tool with bridge
# ---------------------------------------------------------------------------


class TestExecuteToolWithBridge:
    """Test execute_tool behaviour when an ActionBridge is provided."""

    @pytest.mark.asyncio
    @patch("agent.loop._bg")
    async def test_emits_ui_action_for_create_entity(self, _mock_bg):
        """When bridge is active, create_entity emits a ui_action event."""
        from agent.action_bridge import ActionBridge

        bridge = ActionBridge()
        events = []

        async def on_event(e):
            events.append(e)
            if e.get("type") == "ui_action":
                bridge.resolve(e["action_id"], {
                    "success": True,
                    "result": {"id": "ent_new", "type": "note"},
                })

        result = await execute_tool(
            None, "create_entity", {"type": "note", "content": "hello"},
            TEST_SPACE_ID, TEST_USER_ID,
            bridge=bridge, on_event=on_event,
        )

        ui_actions = [e for e in events if e["type"] == "ui_action"]
        assert len(ui_actions) == 1
        assert ui_actions[0]["action"] == "create_entity"
        assert result == {"id": "ent_new", "type": "note"}

    @pytest.mark.asyncio
    @patch("agent.loop._bg")
    async def test_bridge_skips_internal_types(self, _mock_bg):
        """Internal types (conversation_turn) bypass mirroring even with bridge."""
        from agent.action_bridge import ActionBridge

        client = MockSupabaseClient()
        turn_row = _make_entity(entity_type="conversation_turn")
        client.set_table_response("entities", [turn_row])

        bridge = ActionBridge()
        events = []

        async def on_event(e):
            events.append(e)

        result = await execute_tool(
            client, "create_entity",
            {"type": "conversation_turn", "state": {"role": "user", "content": "hi"}},
            TEST_SPACE_ID, TEST_USER_ID,
            bridge=bridge, on_event=on_event,
        )

        ui_actions = [e for e in events if e.get("type") == "ui_action"]
        assert len(ui_actions) == 0
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("agent.loop._bg")
    async def test_bridge_timeout_falls_back_to_direct(self, _mock_bg):
        """When frontend doesn't respond, falls back to direct execution."""
        from agent.action_bridge import ActionBridge

        bridge = ActionBridge()
        events = []

        async def on_event(e):
            events.append(e)

        original_wait = bridge.wait_for_result

        async def fast_timeout(action_id, timeout=15.0):
            return await original_wait(action_id, timeout=0.01)

        bridge.wait_for_result = fast_timeout

        client = MockSupabaseClient()
        entity = _make_entity(entity_type="note")
        client.set_table_response("entities", [entity])

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = entity
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_http

            with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
                 patch("config.DOMUS_SERVICE_TOKEN", "tok"):
                result = await execute_tool(
                    client, "create_entity", {"type": "note", "content": "test"},
                    TEST_SPACE_ID, TEST_USER_ID,
                    bridge=bridge, on_event=on_event,
                )

        ui_actions = [e for e in events if e.get("type") == "ui_action"]
        assert len(ui_actions) == 1
        assert "error" not in result

    @pytest.mark.asyncio
    @patch("agent.loop._bg")
    async def test_no_bridge_unchanged_behavior(self, _mock_bg):
        """Without bridge, execute_tool behaves exactly as before."""
        client = MockSupabaseClient()
        entity = _make_entity(entity_type="note")
        client.set_table_response("entities", [entity])

        with patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = entity
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.post = AsyncMock(return_value=mock_resp)
            MockClient.return_value = mock_http

            with patch("config.DOMUS_FRONTEND_URL", "http://test:3000"), \
                 patch("config.DOMUS_SERVICE_TOKEN", "tok"):
                result = await execute_tool(
                    client, "create_entity", {"type": "note", "content": "test"},
                    TEST_SPACE_ID, TEST_USER_ID,
                    bridge=None, on_event=None,
                )

        assert "error" not in result

    @pytest.mark.asyncio
    @patch("agent.loop._bg")
    async def test_frontend_error_propagated(self, _mock_bg):
        """When frontend responds with success=False, error is propagated."""
        from agent.action_bridge import ActionBridge

        bridge = ActionBridge()

        async def on_event(e):
            if e.get("type") == "ui_action":
                bridge.resolve(e["action_id"], {
                    "success": False,
                    "error": "folder_has_children",
                })

        result = await execute_tool(
            None, "update_entity", {"id": "ent_1", "state": {"archived": True}},
            TEST_SPACE_ID, TEST_USER_ID,
            bridge=bridge, on_event=on_event,
        )

        assert result["error"] == "ui_action_failed"
        assert result["message"] == "folder_has_children"
