"""Tests for agent/context.py — entity index, recent turns, and context enrichment."""

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TEST_SPACE_ID, TEST_USER_ID

from agent.context import (
    get_entity_index,
    get_focused_entity,
    get_recent_turns,
    get_space_info,
    get_user_profile,
    get_conversation_summaries,
    get_personality_traits,
    get_user_facts,
    build_system_prompt,
    _cache_get,
    _cache_set,
    _cache_invalidate,
    _cache,
    _build_semi_static_block,
    _build_dynamic_block,
    _parse_iso,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _join_blocks(blocks) -> str:
    """Flatten block list to string for assertions — test-only helper."""
    if isinstance(blocks, str):
        return blocks  # fallback for backwards compat during refactor
    return "\n\n".join(b["text"] for b in blocks if b.get("type") == "text")


@pytest.fixture(autouse=True)
def clear_context_cache():
    """Clear the TTL cache between tests to avoid cross-test contamination."""
    _cache.clear()
    yield
    _cache.clear()


# ---------------------------------------------------------------------------
# TestEntityIndex (task 2.1)
# ---------------------------------------------------------------------------


class TestEntityIndex:
    """get_entity_index returns the lightweight entity catalogue for context."""

    async def test_entity_index_returns_non_archived(self, mock_supabase, make_entity):
        """Visible (non-archived) entities should be returned."""
        entities = [
            make_entity(entity_type="note", presentation="window", summary="A note"),
            make_entity(entity_type="image", presentation="window", summary="An image"),
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 2

    async def test_entity_index_excludes_internal_types(self, mock_supabase, make_entity):
        """Internal memory types are always excluded regardless of presentation."""
        entities = [
            make_entity(entity_type="note", presentation="window", summary="Visible note"),
            make_entity(entity_type="fact", presentation="hidden", summary="A fact"),
            make_entity(entity_type="conversation_turn", presentation="hidden", summary="A turn"),
            make_entity(entity_type="conversation_summary", presentation="hidden", summary="A summary"),
            make_entity(entity_type="edge", presentation="hidden", summary="An edge"),
            make_entity(entity_type="personality_trait", presentation="hidden", summary="A trait"),
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 1
        assert result[0]["type"] == "note"

    async def test_entity_index_includes_docked_apps(self, mock_supabase, make_entity):
        """Docked apps (presentation='hidden', type='app') ARE included — agent must see them."""
        entities = [
            make_entity(entity_type="app", presentation="window", summary="Calculator"),
            make_entity(entity_type="app", presentation="hidden", summary="Habit Tracker (docked)"),
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 2
        summaries = {e["summary"] for e in result}
        assert "Habit Tracker (docked)" in summaries

    async def test_get_entity_index_excludes_invalid_hidden(self, mock_supabase, make_entity):
        """Hidden image/folder/note entities are excluded — they have no valid docked state."""
        entities = [
            make_entity(entity_type="image", presentation="hidden", summary="Orphaned image"),
            make_entity(entity_type="folder", presentation="hidden", summary="Orphaned folder"),
            make_entity(entity_type="note", presentation="hidden", summary="Orphaned note"),
            make_entity(entity_type="image", presentation="window", summary="Visible image"),
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 1
        assert result[0]["summary"] == "Visible image"

    async def test_get_entity_index_keeps_valid_hidden(self, mock_supabase, make_entity):
        """Hidden calendar/composed entities ARE kept — docking is valid for app-like types."""
        entities = [
            make_entity(entity_type="calendar", presentation="hidden", summary="Docked calendar"),
            make_entity(entity_type="composed", presentation="hidden", summary="Docked app"),
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 2
        types = {e["type"] for e in result}
        assert types == {"calendar", "composed"}

    async def test_entity_index_excludes_archived(self, mock_supabase, make_entity):
        """Archived entities should NOT appear in the index.

        The mock returns whatever we set, so we simulate the expected DB
        behaviour: only non-archived rows come back from the query.
        """
        # Only non-archived entities should be in the response
        # (the real DB filters via .eq("archived", False))
        non_archived = [
            make_entity(entity_type="note", presentation="window", archived=False),
        ]
        mock_supabase.set_table_response("entities", non_archived)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 1
        # Verify no archived entities snuck in
        for entity in result:
            assert entity.get("archived") is not True or entity.get("archived") is False

    async def test_entity_index_returns_correct_fields(self, mock_supabase, make_entity):
        """Returned dicts should contain exactly id, type, presentation, z_index, summary."""
        entities = [
            make_entity(
                entity_type="calendar",
                presentation="window",
                z_index=3,
                summary="My calendar",
            ),
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 1
        entity = result[0]
        # The entity dict must have the five expected fields
        expected_keys = {"id", "type", "presentation", "z_index", "summary"}
        assert expected_keys.issubset(set(entity.keys()))
        assert entity["type"] == "calendar"
        assert entity["presentation"] == "window"
        assert entity["z_index"] == 3
        assert entity["summary"] == "My calendar"

    async def test_entity_index_empty_space(self, mock_supabase):
        """An empty space returns an empty list."""
        mock_supabase.set_table_response("entities", [])

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert result == []

    async def test_entity_index_served_from_cache_on_second_call(
        self, mock_supabase, make_entity
    ):
        """Second call with empty mock still returns data from cache."""
        entities = [
            make_entity(entity_type="note", presentation="window", summary="A note"),
        ]
        mock_supabase.set_table_response("entities", entities)

        # First call populates cache
        first = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        # Second call with empty mock — should still return from cache
        mock_supabase.set_table_response("entities", [])
        second = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        assert len(first) == 1
        assert second == first  # served from cache


# ---------------------------------------------------------------------------
# TestTTLCache
# ---------------------------------------------------------------------------


class TestTTLCache:
    """In-process TTL cache for entity index and user/space data."""

    async def test_cache_set_and_get(self):
        await _cache_set("k", {"x": 1})
        assert await _cache_get("k") == {"x": 1}

    async def test_cache_expires(self):
        await _cache_set("k", "v", ttl=0.01)
        await asyncio.sleep(0.02)
        assert await _cache_get("k") is None

    async def test_cache_miss_returns_none(self):
        assert await _cache_get("nonexistent-key") is None

    async def test_cache_different_keys_are_independent(self):
        await _cache_set("key1", "val1")
        await _cache_set("key2", "val2")
        assert await _cache_get("key1") == "val1"
        assert await _cache_get("key2") == "val2"

    async def test_cache_invalidate_removes_entry(self):
        """_cache_invalidate removes a key so subsequent get returns None."""
        await _cache_set("inv-key", "inv-val")
        assert await _cache_get("inv-key") == "inv-val"
        await _cache_invalidate("inv-key")
        assert await _cache_get("inv-key") is None

    async def test_cache_invalidate_nonexistent_key_is_safe(self):
        """_cache_invalidate on a missing key should not raise."""
        await _cache_invalidate("key-that-was-never-set")


# ---------------------------------------------------------------------------
# TestRecentTurns (task 2.2)
# ---------------------------------------------------------------------------


class TestRecentTurns:
    """get_recent_turns returns recent conversation_turn entities."""

    def _make_turn(self, make_entity, index: int, **kwargs) -> dict:
        """Helper: create a conversation_turn entity with a sequential timestamp."""
        return make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user" if index % 2 == 0 else "assistant", "content": f"Turn {index}"},
            created_at=f"2026-02-15T00:00:{index:02d}Z",
            **kwargs,
        )

    async def test_recent_turns_returns_last_n(self, mock_supabase, make_entity):
        """Create 7 turns, request 5, get 5 back."""
        turns = [self._make_turn(make_entity, i) for i in range(7)]
        # Simulate DB returning the 5 most recent (newest first)
        newest_five = list(reversed(turns[-5:]))
        mock_supabase.set_table_response("entities", newest_five)

        result = await get_recent_turns(mock_supabase, TEST_SPACE_ID, limit=5)

        assert len(result) == 5

    async def test_recent_turns_ordered_newest_first(self, mock_supabase, make_entity):
        """The returned turns should be ordered newest-first (descending created_at)."""
        turns = [self._make_turn(make_entity, i) for i in range(5)]
        # DB returns newest first
        newest_first = list(reversed(turns))
        mock_supabase.set_table_response("entities", newest_first)

        result = await get_recent_turns(mock_supabase, TEST_SPACE_ID, limit=5)

        # Verify ordering by checking created_at is descending
        timestamps = [r["created_at"] for r in result]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_recent_turns_respects_limit(self, mock_supabase, make_entity):
        """Default limit is 5. If fewer turns exist, return what's there."""
        turns = [self._make_turn(make_entity, i) for i in range(3)]
        newest_first = list(reversed(turns))
        mock_supabase.set_table_response("entities", newest_first)

        # Call without explicit limit — should use default of 5
        result = await get_recent_turns(mock_supabase, TEST_SPACE_ID)

        # Only 3 turns exist, so we get 3
        assert len(result) == 3

    async def test_recent_turns_contain_state(self, mock_supabase, make_entity):
        """Each returned turn should have a 'state' field with role and content."""
        turns = [self._make_turn(make_entity, i) for i in range(2)]
        newest_first = list(reversed(turns))
        mock_supabase.set_table_response("entities", newest_first)

        result = await get_recent_turns(mock_supabase, TEST_SPACE_ID, limit=2)

        for turn in result:
            assert "state" in turn
            assert "role" in turn["state"]
            assert "content" in turn["state"]

    async def test_recent_turns_empty(self, mock_supabase):
        """No conversation turns returns an empty list."""
        mock_supabase.set_table_response("entities", [])

        result = await get_recent_turns(mock_supabase, TEST_SPACE_ID)

        assert result == []


# ---------------------------------------------------------------------------
# TestBuildSystemPrompt (task 2.3)
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    """build_system_prompt assembles the full system prompt from parts."""

    async def test_returns_list_of_blocks(self, mock_supabase):
        """build_system_prompt must return a list of text blocks."""
        mock_supabase.set_table_response("entities", [])

        result = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert isinstance(result, list)
        assert all(b.get("type") == "text" for b in result)

    async def test_static_block_has_cache_control(self, mock_supabase):
        """Block 0 (base instructions + iframe builder) must have cache_control=ephemeral."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert blocks[0]["cache_control"] == {"type": "ephemeral"}

    async def test_semi_static_block_has_cache_control(self, mock_supabase):
        """Block 1 (space + user + entity index) must have cache_control=ephemeral."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert len(blocks) >= 2
        assert blocks[1]["cache_control"] == {"type": "ephemeral"}

    async def test_dynamic_block_has_no_cache_control(self, mock_supabase):
        """Block 2 (canvas + recent turns + time) must NOT have cache_control."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "cache_control" not in blocks[-1]

    async def test_system_prompt_contains_base_instructions(self, mock_supabase):
        """The prompt must include agent identity text."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "You are Domus" in _join_blocks(blocks)

    async def test_system_prompt_contains_entity_index(self, mock_supabase, make_entity):
        """Entity summaries from the space should appear in the prompt."""
        entities = [
            make_entity(entity_type="note", presentation="window", summary="Shopping list"),
            make_entity(entity_type="image", presentation="card", summary="Vacation photo"),
        ]
        mock_supabase.set_table_response("entities", entities)

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        text = _join_blocks(blocks)
        assert "Shopping list" in text
        assert "Vacation photo" in text

    async def test_system_prompt_contains_recent_turns(self, mock_supabase, make_entity):
        """Recent conversation content should appear in the prompt."""
        turns = [
            make_entity(
                entity_type="conversation_turn",
                presentation="hidden",
                state={"role": "user", "content": "What is the weather?"},
                created_at="2026-02-15T00:00:01Z",
            ),
            make_entity(
                entity_type="conversation_turn",
                presentation="hidden",
                state={"role": "assistant", "content": "It is sunny today."},
                created_at="2026-02-15T00:00:00Z",
            ),
        ]
        mock_supabase.set_table_response("entities", turns)

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        text = _join_blocks(blocks)
        assert "What is the weather?" in text
        assert "It is sunny today." in text

    async def test_system_prompt_enforces_discovery_pattern(self, mock_supabase):
        """Prompt should tell the agent to use get_entity_schema + call_entity_tool for discovery."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "get_entity_schema" in text
        assert "call_entity_tool" in text

    async def test_system_prompt_has_no_hardcoded_type_schemas(self, mock_supabase):
        """Prompt must not hardcode per-type state shapes — agent discovers these at runtime."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "generation_prompt" not in text
        assert "calendar_event:" not in text
        assert "attendees" not in text
        assert "{ events:" not in text

    async def test_system_prompt_presentation_modes(self, mock_supabase):
        """Prompt should list correct presentation modes: window, card, folder, hidden."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "window" in text
        assert "card" in text
        assert "folder" in text
        assert "hidden" in text
        assert "sidebar" not in text

    async def test_system_prompt_contains_canvas_context(self, mock_supabase, make_entity):
        """When viewport/focused/visible are provided, Canvas Context section appears."""
        entities = [
            make_entity(
                entity_id="ent-abc",
                entity_type="note",
                presentation="window",
                summary="Focused note",
            ),
        ]
        mock_supabase.set_table_response("entities", entities)

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            viewport={"width": 1920, "height": 1080},
            focused_entity_id="ent-abc",
            visible_entity_ids=["ent-abc"],
        )
        text = _join_blocks(blocks)

        assert "Canvas Context" in text
        assert "1920" in text
        assert "1080" in text
        assert "Focused note" in text

    async def test_system_prompt_omits_canvas_when_none(self, mock_supabase):
        """When no canvas context is provided, no Canvas Context section appears."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "Canvas Context" not in text

    async def test_system_prompt_focused_entity_shows_summary(self, mock_supabase, make_entity):
        """focused_entity_id should resolve to entity summary in the prompt."""
        entities = [
            make_entity(
                entity_id="ent-123",
                entity_type="calendar",
                presentation="window",
                summary="My Calendar",
            ),
            make_entity(
                entity_id="ent-456",
                entity_type="note",
                presentation="window",
                summary="Shopping list",
            ),
        ]
        mock_supabase.set_table_response("entities", entities)

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            focused_entity_id="ent-123",
        )
        text = _join_blocks(blocks)

        assert "Canvas Context" in text
        assert "My Calendar" in text

    async def test_system_prompt_visible_entities_listed(self, mock_supabase, make_entity):
        """visible_entity_ids should list each visible entity by type+id+summary."""
        entities = [
            make_entity(entity_id="ent-1", entity_type="note", summary="Note 1"),
            make_entity(entity_id="ent-2", entity_type="note", summary="Note 2"),
            make_entity(entity_id="ent-3", entity_type="note", summary="Note 3"),
        ]
        mock_supabase.set_table_response("entities", entities)

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            visible_entity_ids=["ent-1", "ent-2"],
        )
        text = _join_blocks(blocks)

        assert "Also visible" in text
        assert "Note 1" in text
        assert "Note 2" in text

    async def test_system_prompt_contains_user_name(self, mock_supabase, make_entity):
        """When user_id is provided and profile has a name, prompt includes it."""
        mock_supabase.set_table_response("entities", [])
        mock_supabase.set_table_response(
            "users", [{"name": "Alice", "username": "alice", "avatar_url": None}]
        )
        mock_supabase.set_table_response("spaces", [])

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello", user_id=TEST_USER_ID
        )
        text = _join_blocks(blocks)

        assert "Alice" in text
        assert "User" in text

    async def test_system_prompt_omits_user_when_no_id(self, mock_supabase):
        """When user_id is None, no User section appears."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "=== User ===" not in text

    async def test_system_prompt_contains_temporal_context(self, mock_supabase):
        """Prompt should include current date/time."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "Current User Date & Time" in text
        now = datetime.now(timezone.utc)
        assert str(now.year) in text

    async def test_system_prompt_with_timezone_shows_local_time(self, mock_supabase):
        """When timezone is provided, prompt should show local time."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            user_timezone="Europe/Bucharest",
        )
        text = _join_blocks(blocks)

        assert "Current User Date & Time" in text
        now = datetime.now(timezone.utc)
        assert str(now.year) in text

    async def test_system_prompt_without_timezone_defaults_to_utc(self, mock_supabase):
        """When no timezone is provided, prompt should still show a time."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "Current User Date & Time" in text

    async def test_system_prompt_does_not_duplicate_user_message(self, mock_supabase):
        """The user's message should NOT appear in the system prompt — it arrives via the messages array."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "edit this note please"
        )
        text = _join_blocks(blocks)

        assert "Current Request" not in text
        assert "edit this note please" not in text

    async def test_system_prompt_contains_space_name(self, mock_supabase):
        """When space has a name, prompt includes it."""
        mock_supabase.set_table_response("entities", [])
        mock_supabase.set_table_response("spaces", [{"name": "My Workspace"}])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        text = _join_blocks(blocks)

        assert "My Workspace" in text
        assert "Space:" in text

    async def test_system_prompt_focused_entity_shows_content(
        self, mock_supabase, make_entity
    ):
        """Focused entity should include its content and state in the prompt."""
        entities = [
            make_entity(
                entity_id="ent-focused",
                entity_type="note",
                presentation="window",
                summary="Shopping list",
                content="# Shopping\n- Milk\n- Eggs",
                state={"checked": True},
            ),
        ]
        mock_supabase.set_table_response("entities", entities)

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            focused_entity_id="ent-focused",
        )
        text = _join_blocks(blocks)

        assert "Shopping list" in text
        assert "# Shopping" in text
        assert "Milk" in text
        assert "checked" in text

    async def test_queries_run_in_parallel(self, mock_supabase):
        """build_system_prompt should use asyncio.gather for parallel queries."""
        mock_supabase.set_table_response("entities", [])

        with patch("agent.context.asyncio.gather", wraps=asyncio.gather) as mock_gather:
            await build_system_prompt(
                mock_supabase, TEST_SPACE_ID, "hello", user_id=TEST_USER_ID
            )

        mock_gather.assert_called()
        # At least 2 coroutines gathered in the main call
        args = mock_gather.call_args[0]
        assert len(args) >= 2


# ---------------------------------------------------------------------------
# TestUserProfile (Phase 19.1)
# ---------------------------------------------------------------------------


class TestUserProfile:
    """get_user_profile fetches the user's name from the users table."""

    async def test_profile_found_returns_name(self, mock_supabase):
        """When user exists, return their profile dict."""
        mock_supabase.set_table_response(
            "users", [{"name": "Alice", "username": "alice", "avatar_url": None}]
        )

        result = await get_user_profile(mock_supabase, TEST_USER_ID)

        assert result is not None
        assert result["name"] == "Alice"

    async def test_profile_not_found_returns_none(self, mock_supabase):
        """When user doesn't exist, return None."""
        mock_supabase.set_table_response("users", [])

        result = await get_user_profile(mock_supabase, "nonexistent-id")

        assert result is None

    async def test_profile_without_name(self, mock_supabase):
        """When user exists but has no name, return the profile (caller decides)."""
        mock_supabase.set_table_response(
            "users", [{"name": None, "username": "ghost", "avatar_url": None}]
        )

        result = await get_user_profile(mock_supabase, TEST_USER_ID)

        assert result is not None
        assert result["name"] is None


# ---------------------------------------------------------------------------
# TestSpaceInfo (Phase 19.2)
# ---------------------------------------------------------------------------


class TestSpaceInfo:
    """get_space_info fetches the space name."""

    async def test_space_found_returns_name(self, mock_supabase):
        """When space exists, return dict with name."""
        mock_supabase.set_table_response("spaces", [{"name": "My Workspace"}])

        result = await get_space_info(mock_supabase, TEST_SPACE_ID)

        assert result is not None
        assert result["name"] == "My Workspace"

    async def test_space_not_found_returns_none(self, mock_supabase):
        """When space doesn't exist, return None."""
        mock_supabase.set_table_response("spaces", [])

        result = await get_space_info(mock_supabase, "nonexistent-id")

        assert result is None


# ---------------------------------------------------------------------------
# TestFocusedEntity (Phase 19 enrichment)
# ---------------------------------------------------------------------------


class TestFocusedEntity:
    """get_focused_entity fetches full entity content and state."""

    async def test_entity_found_returns_content_and_state(
        self, mock_supabase, make_entity
    ):
        """When entity exists, return dict with content and state."""
        entity = make_entity(
            entity_id="ent-abc",
            entity_type="note",
            content="Hello world",
            state={"draft": True},
            summary="A note",
        )
        mock_supabase.set_table_response("entities", [entity])

        result = await get_focused_entity(mock_supabase, TEST_SPACE_ID, "ent-abc")

        assert result is not None
        assert result["content"] == "Hello world"
        assert result["state"] == {"draft": True}

    async def test_entity_not_found_returns_none(self, mock_supabase):
        """When entity doesn't exist, return None."""
        mock_supabase.set_table_response("entities", [])

        result = await get_focused_entity(mock_supabase, TEST_SPACE_ID, "missing-id")

        assert result is None


# ---------------------------------------------------------------------------
# TestGetConversationSummaries (Phase 6.2)
# ---------------------------------------------------------------------------


class TestGetConversationSummaries:
    """get_conversation_summaries returns summaries from conversation_summary entities."""

    async def test_returns_summary_strings(self, mock_supabase):
        """Returns list of summary text strings."""
        entities = [
            {
                "id": "s1",
                "type": "conversation_summary",
                "state": {"summary": "User talked about cooking"},
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "type": "conversation_summary",
                "state": {"summary": "User discussed travel plans"},
                "created_at": "2026-01-01T01:00:00Z",
            },
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_conversation_summaries(mock_supabase, TEST_SPACE_ID)

        assert result == [
            "User talked about cooking",
            "User discussed travel plans",
        ]

    async def test_returns_empty_when_no_summaries(self, mock_supabase):
        """Returns empty list when no summary entities exist."""
        mock_supabase.set_table_response("entities", [])

        result = await get_conversation_summaries(mock_supabase, TEST_SPACE_ID)

        assert result == []

    async def test_skips_entities_without_summary_key(self, mock_supabase):
        """Entities with empty or missing summary in state are excluded."""
        entities = [
            {
                "id": "s1",
                "type": "conversation_summary",
                "state": {"summary": "Real summary"},
                "created_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "s2",
                "type": "conversation_summary",
                "state": {},  # missing summary
                "created_at": "2026-01-01T01:00:00Z",
            },
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_conversation_summaries(mock_supabase, TEST_SPACE_ID)

        assert result == ["Real summary"]


# ---------------------------------------------------------------------------
# TestGetPersonalityTraits (Phase 6.2)
# ---------------------------------------------------------------------------


class TestGetPersonalityTraits:
    """get_personality_traits returns traits from personality_trait entities."""

    async def test_returns_trait_dicts(self, mock_supabase):
        """Returns list of {content, confidence} dicts from personality_trait entities."""
        entities = [
            {
                "id": "t1",
                "type": "personality_trait",
                "state": {"content": "Be concise and direct", "confidence": 0.9},
            },
            {
                "id": "t2",
                "type": "personality_trait",
                "state": {"content": "Prefer bullet points over prose"},
            },
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_personality_traits(mock_supabase, TEST_SPACE_ID)

        assert result == [
            {"content": "Be concise and direct", "confidence": 0.9},
            {"content": "Prefer bullet points over prose", "confidence": 1.0},
        ]

    async def test_returns_empty_when_no_traits(self, mock_supabase):
        """Returns empty list when no personality_trait entities exist."""
        mock_supabase.set_table_response("entities", [])

        result = await get_personality_traits(mock_supabase, TEST_SPACE_ID)

        assert result == []


# ---------------------------------------------------------------------------
# TestGetUserFacts (Phase 19.4)
# ---------------------------------------------------------------------------


class TestGetUserFacts:
    """get_user_facts returns known facts about the user sorted by confidence."""

    async def test_returns_facts_sorted_by_confidence_desc(self, mock_supabase):
        """Facts are returned highest-confidence first."""
        entities = [
            {
                "id": "f1",
                "type": "fact",
                "state": {"content": "User likes coffee", "confidence": 0.6},
            },
            {
                "id": "f2",
                "type": "fact",
                "state": {"content": "User is a developer", "confidence": 0.95},
            },
            {
                "id": "f3",
                "type": "fact",
                "state": {"content": "User is in Bucharest", "confidence": 0.8},
            },
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_user_facts(mock_supabase, TEST_SPACE_ID)

        confidences = [f["confidence"] for f in result]
        assert confidences == sorted(confidences, reverse=True)
        assert result[0]["content"] == "User is a developer"

    async def test_returns_empty_when_no_facts(self, mock_supabase):
        """Returns empty list when no fact entities exist."""
        mock_supabase.set_table_response("entities", [])

        result = await get_user_facts(mock_supabase, TEST_SPACE_ID)

        assert result == []

    async def test_caps_at_20_facts(self, mock_supabase):
        """Returns at most 20 facts."""
        entities = [
            {
                "id": f"f{i}",
                "type": "fact",
                "state": {"content": f"Fact {i}", "confidence": 0.5},
            }
            for i in range(30)
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_user_facts(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 20

    async def test_returns_content_and_confidence(self, mock_supabase):
        """Each returned dict has content and confidence keys."""
        entities = [
            {
                "id": "f1",
                "type": "fact",
                "state": {"content": "User prefers dark mode", "confidence": 0.9},
            }
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_user_facts(mock_supabase, TEST_SPACE_ID)

        assert len(result) == 1
        assert result[0]["content"] == "User prefers dark mode"
        assert result[0]["confidence"] == 0.9


# ---------------------------------------------------------------------------
# TestBuildSystemPromptMemory (Phase 6.2 + 19.4)
# ---------------------------------------------------------------------------


class TestBuildSystemPromptMemory:
    """build_system_prompt includes conversation summaries and user facts in Block 1."""

    async def test_includes_conversation_summaries_in_block1(self, mock_supabase):
        """When summaries exist, Block 1 includes a Conversation History section."""
        entities = [
            {
                "id": "s1",
                "type": "conversation_summary",
                "presentation": "hidden",
                "state": {"summary": "User planned a trip to Tokyo"},
                "summary": None,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_supabase.set_table_response("entities", entities)

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        semi_static = blocks[1]["text"]

        assert "Conversation History" in semi_static
        assert "User planned a trip to Tokyo" in semi_static

    async def test_omits_conversation_history_when_no_summaries(self, mock_supabase):
        """When no summaries exist, Conversation History section is absent."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        semi_static = blocks[1]["text"]

        assert "Conversation History" not in semi_static

    async def test_includes_user_facts_in_user_section(self, mock_supabase):
        """When user facts exist and user_id is provided, facts appear in User section."""
        entities = [
            {
                "id": "f1",
                "type": "fact",
                "presentation": "hidden",
                "state": {"content": "User prefers dark mode", "confidence": 0.9},
                "summary": "User prefers dark mode",
            }
        ]
        mock_supabase.set_table_response("entities", entities)
        mock_supabase.set_table_response(
            "users", [{"name": "Alice", "username": "alice", "avatar_url": None}]
        )
        mock_supabase.set_table_response("spaces", [])

        blocks = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello", user_id=TEST_USER_ID
        )
        semi_static = blocks[1]["text"]

        assert "User prefers dark mode" in semi_static

    async def test_user_facts_absent_without_user_id(self, mock_supabase):
        """User facts section is not shown when no user_id is provided."""
        mock_supabase.set_table_response("entities", [])

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        semi_static = blocks[1]["text"]

        # No user section at all
        assert "=== User ===" not in semi_static

    async def test_personality_traits_in_block1(self, mock_supabase):
        """When personality traits exist, Agent Personality section appears in Block 1."""
        entities = [
            {
                "id": "t1",
                "type": "personality_trait",
                "presentation": "hidden",
                "state": {"content": "Always use bullet points"},
                "summary": None,
            }
        ]
        mock_supabase.set_table_response("entities", entities)

        blocks = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")
        semi_static = blocks[1]["text"]

        assert "Agent Personality" in semi_static
        assert "Always use bullet points" in semi_static


# ---------------------------------------------------------------------------
# TestDynamicBlock (Phase 19.6 — visible entity listing)
# ---------------------------------------------------------------------------


class TestDynamicBlock:
    """Tests for _build_dynamic_block visible entity changes (19.6)."""

    def _make_ent(self, eid, etype, summary):
        return {"id": eid, "type": etype, "summary": summary, "presentation": "window"}

    def test_visible_entities_listed_with_summaries(self):
        """Each visible entity should appear as [type] id: summary."""
        entities = [
            self._make_ent("a", "note", "My note"),
            self._make_ent("b", "image", "Beach photo"),
        ]
        result = _build_dynamic_block(
            entities=entities,
            focused=None,
            turns=[],
            viewport={"width": 1920, "height": 1080},
            visible_entity_ids=["a", "b"],
            user_timezone=None,
        )
        assert "[note] a: My note" in result
        assert "[image] b: Beach photo" in result
        assert "Also visible" in result

    def test_focused_entity_not_duplicated_in_visible(self):
        """The focused entity should not appear in the 'Also visible' section."""
        entities = [
            self._make_ent("a", "note", "Focused note"),
            self._make_ent("b", "note", "Other note"),
        ]
        focused = {"id": "a", "type": "note", "summary": "Focused note"}
        result = _build_dynamic_block(
            entities=entities,
            focused=focused,
            turns=[],
            viewport={"width": 1920, "height": 1080},
            visible_entity_ids=["a", "b"],
            user_timezone=None,
        )
        # "b" should appear in Also visible, but "a" should only appear in Focused line
        lines = result.split("\n")
        also_visible_idx = next(
            (i for i, l in enumerate(lines) if "Also visible" in l), None
        )
        assert also_visible_idx is not None
        also_visible_section = "\n".join(lines[also_visible_idx:])
        assert "Other note" in also_visible_section
        # "a" (focused) must not appear in the also-visible section
        assert "[note] a:" not in also_visible_section

    def test_empty_visible_ids_produces_no_visible_section(self):
        """When visible_entity_ids is empty or None, no 'Also visible' section appears."""
        entities = [self._make_ent("a", "note", "Some note")]
        result = _build_dynamic_block(
            entities=entities,
            focused=None,
            turns=[],
            viewport=None,
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "Also visible" not in result

    def test_visible_entity_not_in_index_skipped_gracefully(self):
        """If a visible entity ID is not in the entity list, it is silently skipped."""
        entities = [self._make_ent("known", "note", "Known note")]
        result = _build_dynamic_block(
            entities=entities,
            focused=None,
            turns=[],
            viewport={"width": 1920, "height": 1080},
            visible_entity_ids=["known", "ghost-id"],
            user_timezone=None,
        )
        # Should not raise; "ghost-id" simply absent
        assert "Known note" in result
        assert "ghost-id" not in result


# ---------------------------------------------------------------------------
# TestRecentlyActive (Phase 19.7 — recency signal in entity index)
# ---------------------------------------------------------------------------


class TestRecentlyActive:
    """Tests for the Recently Active section in _build_semi_static_block (19.7)."""

    def _ent(self, eid, etype, summary, updated_at):
        return {
            "id": eid,
            "type": etype,
            "summary": summary,
            "presentation": "window",
            "updated_at": updated_at,
        }

    def test_recently_active_section_present(self):
        """When entities exist, a 'Recently Active' section appears."""
        entities = [
            self._ent("a", "note", "Note A", "2026-03-02T10:00:00Z"),
            self._ent("b", "note", "Note B", "2026-03-02T09:00:00Z"),
        ]
        result = _build_semi_static_block(space=None, user=None, entities=entities)
        assert "Recently Active" in result

    def test_recently_active_ordered_by_recency(self):
        """The most recently updated entity appears first in the section."""
        entities = [
            self._ent("a", "note", "Older", "2026-03-01T08:00:00Z"),
            self._ent("b", "note", "Newer", "2026-03-02T10:00:00Z"),
            self._ent("c", "note", "Middle", "2026-03-01T12:00:00Z"),
        ]
        result = _build_semi_static_block(space=None, user=None, entities=entities)
        recently_active_start = result.index("Recently Active")
        section = result[recently_active_start:]
        # "Newer" should appear before "Middle" and "Older"
        assert section.index("Newer") < section.index("Middle")
        assert section.index("Newer") < section.index("Older")

    def test_recently_active_omitted_when_no_entities(self):
        """When no entities exist, the Recently Active section is absent."""
        result = _build_semi_static_block(space=None, user=None, entities=[])
        assert "Recently Active" not in result

    def test_recently_active_capped_at_three(self):
        """With 5 entities, only the 3 most recent appear in Recently Active."""
        entities = [
            self._ent(f"e{i}", "note", f"Note {i}", f"2026-03-02T{i:02d}:00:00Z")
            for i in range(5)
        ]
        result = _build_semi_static_block(space=None, user=None, entities=entities)
        recently_active_start = result.index("Recently Active")
        # Find the end of the section (next === or end of string)
        after_section = result[recently_active_start:]
        # The section ends at the next double-newline before the entity index
        section_end = after_section.find("\n\n")
        section = after_section[:section_end] if section_end != -1 else after_section
        # Only 3 bullet points (lines starting with "- [")
        bullets = [l for l in section.split("\n") if l.startswith("- [")]
        assert len(bullets) == 3


# ---------------------------------------------------------------------------
# TestFocusHints (Phase 19.8 — type-aware hints for focused entities)
# ---------------------------------------------------------------------------


class TestFocusHints:
    """Tests for type-aware hints in the focused entity section (19.8)."""

    def test_calendar_focus_appends_hint(self):
        """Focusing a calendar entity shows a tip about query_entities."""
        focused = {"id": "cal-1", "type": "calendar", "summary": "My Calendar"}
        result = _build_dynamic_block(
            entities=[],
            focused=focused,
            turns=[],
            viewport={"width": 1920, "height": 1080},
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "calendar_event" in result
        assert "query_entities" in result

    def test_note_focus_no_hint(self):
        """Focusing a note entity produces no hint."""
        focused = {"id": "note-1", "type": "note", "summary": "My Note"}
        result = _build_dynamic_block(
            entities=[],
            focused=focused,
            turns=[],
            viewport={"width": 1920, "height": 1080},
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "Tip:" not in result

    def test_no_focused_entity_no_hint(self):
        """When focused is None, no hint appears."""
        result = _build_dynamic_block(
            entities=[],
            focused=None,
            turns=[],
            viewport={"width": 1920, "height": 1080},
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "Tip:" not in result


# ---------------------------------------------------------------------------
# TestSessionCreated (Phase 19.9 — session-created entity signal)
# ---------------------------------------------------------------------------


class TestSessionCreated:
    """Tests for the 'Created this session' section in _build_dynamic_block (19.9)."""

    def _ent(self, eid, etype, summary, created_at):
        return {
            "id": eid,
            "type": etype,
            "summary": summary,
            "presentation": "window",
            "created_at": created_at,
        }

    def test_session_entities_appear_when_recently_created(self):
        """Entity created 10 minutes ago should appear in the session section."""
        from datetime import datetime, timedelta, timezone
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        entities = [self._ent("new-1", "note", "Fresh note", recent_ts)]
        result = _build_dynamic_block(
            entities=entities,
            focused=None,
            turns=[],
            viewport=None,
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "Created this session" in result
        assert "Fresh note" in result

    def test_old_entities_excluded(self):
        """Entity created 2 hours ago should NOT appear in the session section."""
        from datetime import datetime, timedelta, timezone
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        entities = [self._ent("old-1", "note", "Old note", old_ts)]
        result = _build_dynamic_block(
            entities=entities,
            focused=None,
            turns=[],
            viewport=None,
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "Created this session" not in result

    def test_session_section_omitted_when_nothing_recent(self):
        """When no entities are within the session window, section is absent."""
        result = _build_dynamic_block(
            entities=[],
            focused=None,
            turns=[],
            viewport=None,
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "Created this session" not in result

    def test_session_section_omitted_when_no_created_at_field(self):
        """Entities missing created_at should be gracefully skipped."""
        entities = [{"id": "x", "type": "note", "summary": "No timestamp", "presentation": "window"}]
        result = _build_dynamic_block(
            entities=entities,
            focused=None,
            turns=[],
            viewport=None,
            visible_entity_ids=None,
            user_timezone=None,
        )
        assert "Created this session" not in result
