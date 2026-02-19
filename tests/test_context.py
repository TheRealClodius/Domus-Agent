"""Tests for agent/context.py — entity index, recent turns, and context enrichment."""

from datetime import datetime, timezone

import pytest

from tests.conftest import TEST_SPACE_ID, TEST_USER_ID

from agent.context import (
    get_entity_index,
    get_focused_entity,
    get_recent_turns,
    get_space_info,
    get_user_profile,
    build_system_prompt,
)


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

    async def test_entity_index_includes_hidden(self, mock_supabase, make_entity):
        """Hidden entities (presentation='hidden') should be included in the index."""
        entities = [
            make_entity(entity_type="note", presentation="window", summary="Visible"),
            make_entity(entity_type="fact", presentation="hidden", summary="A fact"),
            make_entity(entity_type="edge", presentation="hidden", summary="An edge"),
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_entity_index(mock_supabase, TEST_SPACE_ID)

        # All three should be present — hidden entities are included
        assert len(result) == 3
        presentations = {e["presentation"] for e in result}
        assert "hidden" in presentations
        assert "window" in presentations

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

    async def test_system_prompt_contains_base_instructions(self, mock_supabase):
        """The prompt must include agent identity text."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "You are Domus" in prompt

    async def test_system_prompt_contains_entity_index(self, mock_supabase, make_entity):
        """Entity summaries from the space should appear in the prompt."""
        entities = [
            make_entity(entity_type="note", presentation="window", summary="Shopping list"),
            make_entity(entity_type="image", presentation="card", summary="Vacation photo"),
        ]
        mock_supabase.set_table_response("entities", entities)

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "Shopping list" in prompt
        assert "Vacation photo" in prompt

    async def test_system_prompt_contains_recent_turns(self, mock_supabase, make_entity):
        """Recent conversation content should appear in the prompt."""
        # build_system_prompt calls get_entity_index first, then get_recent_turns.
        # Both hit client.table("entities") so the mock returns the same data for both.
        # We set up turn-shaped data — get_entity_index will also return it but that's fine
        # since we only care that the prompt contains the turn content.
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

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "What is the weather?" in prompt
        assert "It is sunny today." in prompt

    async def test_system_prompt_contains_entity_state_shapes(self, mock_supabase):
        """The prompt should describe state shapes for note, calendar, calendar_event, and image."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        # Note uses entity-level content field, NOT state.content
        assert "note:" in prompt
        assert "content field" in prompt.lower() or "`content` field" in prompt
        # Calendar is its own type — events are separate
        assert "calendar:" in prompt
        assert "calendar_event:" in prompt
        # Image type documented
        assert "image:" in prompt
        # Old incorrect shapes must NOT be present
        assert "A note entity has state:" not in prompt
        assert "A calendar entity has state:" not in prompt
        assert "{ events:" not in prompt
        assert "{ title: string, content: string }" not in prompt

    async def test_system_prompt_calendar_event_hidden(self, mock_supabase):
        """calendar_event should be documented with presentation='hidden'."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        # calendar_event presentation must be hidden
        assert "calendar_event:" in prompt
        assert "hidden" in prompt

    async def test_system_prompt_calendar_event_has_attendees(self, mock_supabase):
        """calendar_event state shape should include attendees field."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "attendees" in prompt

    async def test_system_prompt_singleton_awareness(self, mock_supabase):
        """Prompt should warn about singleton apps (chat, settings, sounds)."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "singleton" in prompt.lower() or "chat" in prompt.lower()

    async def test_system_prompt_presentation_modes(self, mock_supabase):
        """Prompt should list correct presentation modes: window, card, folder, hidden."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "window" in prompt
        assert "card" in prompt
        assert "folder" in prompt
        assert "hidden" in prompt
        # sidebar should NOT be mentioned
        assert "sidebar" not in prompt.lower()

    async def test_image_state_shape_includes_generation_prompt(self, mock_supabase):
        """Image entity state shape should reference generation_prompt."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "generation_prompt" in prompt
        # Should NOT reference the old url/alt shape
        assert "{ url: string, alt: string }" not in prompt

    async def test_system_prompt_includes_image_creation_guidance(self, mock_supabase):
        """Prompt should instruct agent to use type='image' with generation_prompt and presentation='card'."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "generation_prompt" in prompt
        assert "card" in prompt

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

        prompt = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            viewport={"width": 1920, "height": 1080},
            focused_entity_id="ent-abc",
            visible_entity_ids=["ent-abc"],
        )

        assert "Canvas Context" in prompt
        assert "1920" in prompt
        assert "1080" in prompt
        assert "Focused note" in prompt

    async def test_system_prompt_omits_canvas_when_none(self, mock_supabase):
        """When no canvas context is provided, no Canvas Context section appears."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "Canvas Context" not in prompt

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

        prompt = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            focused_entity_id="ent-123",
        )

        assert "Canvas Context" in prompt
        assert "My Calendar" in prompt

    async def test_system_prompt_visible_count(self, mock_supabase, make_entity):
        """visible_entity_ids should show count of visible vs total entities."""
        entities = [
            make_entity(entity_id="ent-1", entity_type="note", summary="Note 1"),
            make_entity(entity_id="ent-2", entity_type="note", summary="Note 2"),
            make_entity(entity_id="ent-3", entity_type="note", summary="Note 3"),
        ]
        mock_supabase.set_table_response("entities", entities)

        prompt = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            visible_entity_ids=["ent-1", "ent-2"],
        )

        assert "2 of 3" in prompt

    async def test_system_prompt_contains_user_name(self, mock_supabase, make_entity):
        """When user_id is provided and profile has a name, prompt includes it."""
        mock_supabase.set_table_response("entities", [])
        mock_supabase.set_table_response(
            "users", [{"name": "Alice", "username": "alice", "avatar_url": None}]
        )
        mock_supabase.set_table_response("spaces", [])

        prompt = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello", user_id=TEST_USER_ID
        )

        assert "Alice" in prompt
        assert "User" in prompt

    async def test_system_prompt_omits_user_when_no_id(self, mock_supabase):
        """When user_id is None, no User section appears."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "=== User ===" not in prompt

    async def test_system_prompt_contains_temporal_context(self, mock_supabase):
        """Prompt should include current date/time."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "Current User Date & Time" in prompt
        now = datetime.now(timezone.utc)
        assert str(now.year) in prompt

    async def test_system_prompt_with_timezone_shows_local_time(self, mock_supabase):
        """When timezone is provided, prompt should show local time."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            user_timezone="Europe/Bucharest",
        )

        assert "Current User Date & Time" in prompt
        now = datetime.now(timezone.utc)
        assert str(now.year) in prompt

    async def test_system_prompt_without_timezone_defaults_to_utc(self, mock_supabase):
        """When no timezone is provided, prompt should still show a time."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "Current User Date & Time" in prompt

    async def test_system_prompt_does_not_duplicate_user_message(self, mock_supabase):
        """The user's message should NOT appear in the system prompt — it arrives via the messages array."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "edit this note please"
        )

        assert "Current Request" not in prompt
        assert "edit this note please" not in prompt

    async def test_system_prompt_contains_space_name(self, mock_supabase):
        """When space has a name, prompt includes it."""
        mock_supabase.set_table_response("entities", [])
        mock_supabase.set_table_response("spaces", [{"name": "My Workspace"}])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "My Workspace" in prompt
        assert "Space:" in prompt

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

        prompt = await build_system_prompt(
            mock_supabase, TEST_SPACE_ID, "hello",
            focused_entity_id="ent-focused",
        )

        assert "Shopping list" in prompt
        assert "# Shopping" in prompt
        assert "Milk" in prompt
        assert "checked" in prompt


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
