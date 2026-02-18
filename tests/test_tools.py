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
            # SPIKE: entity-as-mcp
            "get_entity_schema", "call_entity_tool",
        ]
        assert names == expected

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


class TestCreateEntityFunction:
    """Tests for the create_entity tool function."""

    async def test_create_entity_inserts_row(self, mock_supabase, make_entity):
        """create_entity calls table('entities').insert(row).execute()."""
        expected = make_entity(entity_type="note", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        # Spy on insert to verify the row
        original_table = mock_supabase.table

        insert_called_with = {}

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args, "kwargs": kwargs})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        result = await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "note", "summary": "A test note"},
        )

        # Verify insert was called
        assert "args" in insert_called_with
        row = insert_called_with["args"][0]
        assert row["type"] == "note"
        assert row["space_id"] == TEST_SPACE_ID
        assert row["user_id"] == TEST_USER_ID

    async def test_create_entity_sets_created_by_agent(self, mock_supabase, make_entity):
        """created_by is always 'agent', regardless of params."""
        expected = make_entity(entity_type="note", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        original_table = mock_supabase.table
        insert_called_with = {}

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "note"},
        )

        row = insert_called_with["args"][0]
        assert row["created_by"] == "agent"

    async def test_create_entity_uses_default_position_and_size(self, mock_supabase, make_entity):
        """When position/size not provided, sensible defaults are used."""
        expected = make_entity(entity_type="note", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        original_table = mock_supabase.table
        insert_called_with = {}

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "note"},
        )

        row = insert_called_with["args"][0]
        assert "x" in row["position"]
        assert "y" in row["position"]
        assert "width" in row["size"]
        assert "height" in row["size"]

    async def test_create_entity_returns_full_entity(self, mock_supabase, make_entity):
        """Return dict has all expected entity fields."""
        expected = make_entity(
            entity_type="note",
            created_by="agent",
            summary="A test note",
            state={"title": "Hello"},
        )
        mock_supabase.set_table_response("entities", [expected])

        result = await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "note", "summary": "A test note", "state": {"title": "Hello"}},
        )

        assert result["id"] == expected["id"]
        assert result["type"] == expected["type"]
        assert result["summary"] == expected["summary"]
        assert result["state"] == expected["state"]
        assert result["space_id"] == expected["space_id"]


    async def test_create_entity_includes_content(self, mock_supabase, make_entity):
        """content field is passed through to the inserted row."""
        expected = make_entity(entity_type="note", content="# Hello World")
        mock_supabase.set_table_response("entities", [expected])

        original_table = mock_supabase.table
        insert_called_with = {}

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        result = await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "note", "content": "# Hello World"},
        )

        row = insert_called_with["args"][0]
        assert row["content"] == "# Hello World"
        assert result["content"] == "# Hello World"


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
    """Tests for the update_entity tool function."""

    async def test_update_entity_merges_state_adds_new_keys(self, mock_supabase, make_entity):
        """Patching with a new key adds it to state."""
        entity = make_entity(state={"title": "Original"})
        mock_supabase.set_table_response("entities", entity)

        result = await update_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"], "state": {"color": "blue"}},
        )

        # The merged state should have both keys
        assert result["state"]["title"] == "Original"
        assert result["state"]["color"] == "blue"

    async def test_update_entity_merges_state_preserves_existing(self, mock_supabase, make_entity):
        """Patching doesn't remove keys that aren't in the patch."""
        entity = make_entity(state={"title": "Keep", "body": "Also keep"})
        mock_supabase.set_table_response("entities", entity)

        result = await update_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"], "state": {"title": "Changed"}},
        )

        assert result["state"]["title"] == "Changed"
        assert result["state"]["body"] == "Also keep"

    async def test_update_entity_merges_state_null_deletes_key(self, mock_supabase, make_entity):
        """Setting a key to None in the patch removes it (RFC 7396)."""
        entity = make_entity(state={"title": "Keep", "obsolete": "Remove me"})
        mock_supabase.set_table_response("entities", entity)

        result = await update_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"], "state": {"obsolete": None}},
        )

        assert result["state"]["title"] == "Keep"
        assert "obsolete" not in result["state"]

    async def test_update_entity_updates_summary(self, mock_supabase, make_entity):
        """summary field is updated when provided."""
        entity = make_entity(summary="Old summary")
        mock_supabase.set_table_response("entities", entity)

        result = await update_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"], "summary": "New summary"},
        )

        assert result["summary"] == "New summary"

    async def test_update_entity_updates_content(self, mock_supabase, make_entity):
        """content field is updated when provided (full replacement, not merge)."""
        entity = make_entity(content="Old content")
        mock_supabase.set_table_response("entities", entity)

        result = await update_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"], "content": "New content"},
        )

        assert result["content"] == "New content"

    async def test_update_entity_returns_updated_row(self, mock_supabase, make_entity):
        """Return value has all entity fields after update."""
        entity = make_entity(
            entity_type="note",
            state={"title": "Hello"},
            summary="A note",
        )
        mock_supabase.set_table_response("entities", entity)

        result = await update_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"id": entity["id"], "state": {"title": "Updated"}, "summary": "Updated note"},
        )

        # Should have core entity fields
        assert "id" in result
        assert "type" in result
        assert "state" in result
        assert "summary" in result
        assert result["state"]["title"] == "Updated"


# ---------------------------------------------------------------------------
# execute_tool dispatcher tests (task 1.6)
# ---------------------------------------------------------------------------


class TestExecuteTool:
    """Tests for the execute_tool dispatcher."""

    async def test_dispatch_create_entity(self, mock_supabase, make_entity):
        """execute_tool('create_entity', ...) routes to create_entity and returns entity data."""
        expected = make_entity(entity_type="note", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        result = await execute_tool(
            mock_supabase, "create_entity", {"type": "note"},
            TEST_SPACE_ID, TEST_USER_ID,
        )

        assert result["id"] == expected["id"]
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

    async def test_dispatch_update_entity(self, mock_supabase, make_entity):
        """execute_tool('update_entity', ...) routes to update_entity and returns updated data."""
        entity = make_entity(state={"title": "Original"})
        mock_supabase.set_table_response("entities", entity)

        result = await execute_tool(
            mock_supabase, "update_entity",
            {"id": entity["id"], "state": {"title": "Updated"}},
            TEST_SPACE_ID, TEST_USER_ID,
        )

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

    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_calls_generate_image(self, mock_gen, mock_supabase, make_entity):
        """create_entity with type='image' + generation_prompt triggers image generation."""
        mock_gen.return_value = {
            "storage_path": f"{TEST_SPACE_ID}/abc.png",
            "public_url": "https://test.supabase.co/storage/v1/object/public/images/abc.png",
            "width": 1024,
            "height": 1024,
        }
        expected = make_entity(
            entity_type="image",
            presentation="card",
            state={
                "generation_prompt": "a sunset",
                "image_url": "https://test.supabase.co/storage/v1/object/public/images/abc.png",
                "width": 1024,
                "height": 1024,
            },
            created_by="agent",
        )
        mock_supabase.set_table_response("entities", [expected])

        result = await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "image", "state": {"generation_prompt": "a sunset"}},
        )

        mock_gen.assert_called_once_with("a sunset", TEST_SPACE_ID, mock_supabase)

    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_enriches_state(self, mock_gen, mock_supabase, make_entity):
        """State should include image_url, width, height from generate_image result."""
        mock_gen.return_value = {
            "storage_path": f"{TEST_SPACE_ID}/abc.png",
            "public_url": "https://example.com/image.png",
            "width": 512,
            "height": 768,
        }

        insert_called_with = {}
        original_table = mock_supabase.table

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        expected = make_entity(entity_type="image", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "image", "state": {"generation_prompt": "a cat"}},
        )

        row = insert_called_with["args"][0]
        assert row["state"]["image_url"] == "https://example.com/image.png"
        assert row["state"]["width"] == 512
        assert row["state"]["height"] == 768
        assert row["state"]["generation_prompt"] == "a cat"

    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_defaults_presentation_to_card(self, mock_gen, mock_supabase, make_entity):
        """Image entities default to presentation='card' (not 'window')."""
        mock_gen.return_value = {
            "storage_path": f"{TEST_SPACE_ID}/abc.png",
            "public_url": "https://example.com/image.png",
            "width": 1024,
            "height": 1024,
        }

        insert_called_with = {}
        original_table = mock_supabase.table

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        expected = make_entity(entity_type="image", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "image", "state": {"generation_prompt": "landscape"}},
        )

        row = insert_called_with["args"][0]
        assert row["presentation"] == "card"

    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_defaults_size(self, mock_gen, mock_supabase, make_entity):
        """Image entities default to size 232x300."""
        mock_gen.return_value = {
            "storage_path": f"{TEST_SPACE_ID}/abc.png",
            "public_url": "https://example.com/image.png",
            "width": 1024,
            "height": 1024,
        }

        insert_called_with = {}
        original_table = mock_supabase.table

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        expected = make_entity(entity_type="image", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "image", "state": {"generation_prompt": "abstract art"}},
        )

        row = insert_called_with["args"][0]
        assert row["size"] == {"width": 232, "height": 300}

    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_entity_failure_sets_generation_error(self, mock_gen, mock_supabase, make_entity):
        """On generate_image failure, entity is still created with generation_error in state."""
        mock_gen.side_effect = RuntimeError("Gemini API failed")

        insert_called_with = {}
        original_table = mock_supabase.table

        def table_spy(name):
            builder = original_table(name)
            original_insert = builder.insert

            def insert_capture(*args, **kwargs):
                insert_called_with.update({"args": args})
                return original_insert(*args, **kwargs)

            builder.insert = insert_capture
            return builder

        mock_supabase.table = table_spy

        expected = make_entity(entity_type="image", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        result = await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "image", "state": {"generation_prompt": "failing prompt"}},
        )

        row = insert_called_with["args"][0]
        assert "generation_error" in row["state"]
        assert "Gemini API failed" in row["state"]["generation_error"]
        # image_url should NOT be in state on failure
        assert "image_url" not in row["state"]

    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_non_image_entity_does_not_trigger_generation(self, mock_gen, mock_supabase, make_entity):
        """create_entity with type='note' should not call generate_image."""
        expected = make_entity(entity_type="note", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "note", "state": {"title": "Hello"}},
        )

        mock_gen.assert_not_called()

    @patch("agent.image_gen.generate_image", new_callable=AsyncMock)
    async def test_image_without_generation_prompt_skips_generation(self, mock_gen, mock_supabase, make_entity):
        """Image entity without generation_prompt should not trigger generation."""
        expected = make_entity(entity_type="image", created_by="agent")
        mock_supabase.set_table_response("entities", [expected])

        await create_entity(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID,
            {"type": "image", "state": {"image_url": "https://existing.com/img.png"}},
        )

        mock_gen.assert_not_called()


# ---------------------------------------------------------------------------
# compute_group_positions tests
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
# check_batch_image_quota stub tests
# ---------------------------------------------------------------------------


from agent.tools import check_batch_image_quota


class TestCheckBatchImageQuota:
    """check_batch_image_quota is a stub that always allows."""

    async def test_always_returns_allowed(self):
        """Stub should return (True, '') for any count."""
        allowed, reason = await check_batch_image_quota(None, "user-123", 10)
        assert allowed is True
        assert reason == ""

    async def test_returns_tuple(self):
        """Return type is (bool, str)."""
        result = await check_batch_image_quota(None, "user-456", 1)
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# SPIKE: entity-as-mcp — tool definition and handler tests
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
