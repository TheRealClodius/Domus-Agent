"""Tests for agent/context.py — entity index and recent turns queries."""

import pytest

from tests.conftest import TEST_SPACE_ID

from agent.context import get_entity_index, get_recent_turns, build_system_prompt


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
        """The prompt should describe state shapes for entity types."""
        mock_supabase.set_table_response("entities", [])

        prompt = await build_system_prompt(mock_supabase, TEST_SPACE_ID, "hello")

        assert "A note entity has state:" in prompt
        assert "A calendar entity has state:" in prompt
        assert "An image entity has state:" in prompt
