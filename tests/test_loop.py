"""Tests for agent/loop.py — conversation turn persistence and agent loop."""

import json
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.loop import save_conversation_turn, run_agent, format_sse_event
from tests.conftest import TEST_SPACE_ID, TEST_USER_ID, _make_entity, MockSupabaseClient


class TestSaveConversationTurn:
    """save_conversation_turn creates hidden conversation_turn entities."""

    async def test_save_user_turn_creates_hidden_entity(self, mock_supabase, make_entity):
        """Saving a user turn should insert an entity with type='conversation_turn',
        presentation='hidden', and state={'role': 'user', 'content': '...'}."""
        expected = make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Hello agent"},
            created_by="agent",
        )
        mock_supabase.set_table_response("entities", [expected])

        result = await save_conversation_turn(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID, "user", "Hello agent"
        )

        assert result["type"] == "conversation_turn"
        assert result["presentation"] == "hidden"
        assert result["state"]["role"] == "user"
        assert result["state"]["content"] == "Hello agent"
        assert result["created_by"] == "agent"

    async def test_save_assistant_turn_creates_hidden_entity(self, mock_supabase, make_entity):
        """Saving an assistant turn should insert an entity with type='conversation_turn',
        presentation='hidden', and state={'role': 'assistant', 'content': '...'}."""
        expected = make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "assistant", "content": "Hi! How can I help?"},
            created_by="agent",
        )
        mock_supabase.set_table_response("entities", [expected])

        result = await save_conversation_turn(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID, "assistant", "Hi! How can I help?"
        )

        assert result["type"] == "conversation_turn"
        assert result["presentation"] == "hidden"
        assert result["state"]["role"] == "assistant"
        assert result["state"]["content"] == "Hi! How can I help?"
        assert result["created_by"] == "agent"

    async def test_turns_have_correct_state_shape(self, mock_supabase, make_entity):
        """The state dict should have exactly 'role' and 'content' keys."""
        expected = make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "test message"},
            created_by="agent",
        )
        mock_supabase.set_table_response("entities", [expected])

        result = await save_conversation_turn(
            mock_supabase, TEST_SPACE_ID, TEST_USER_ID, "user", "test message"
        )

        assert set(result["state"].keys()) == {"role", "content"}


# ---------------------------------------------------------------------------
# Helpers for mocking Anthropic responses
# ---------------------------------------------------------------------------


def _make_text_response(text):
    """Create a mock Anthropic Message with text-only content."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    message = MagicMock()
    message.content = [text_block]
    message.stop_reason = "end_turn"
    return message


def _make_tool_response(tool_name, tool_id, tool_input):
    """Create a mock Anthropic Message with a tool_use block."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.id = tool_id
    tool_block.input = tool_input

    message = MagicMock()
    message.content = [tool_block]
    message.stop_reason = "tool_use"
    return message


def _make_text_and_tool_response(text, tool_name, tool_id, tool_input):
    """Create a mock Anthropic Message with both text and tool_use blocks."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = tool_name
    tool_block.id = tool_id
    tool_block.input = tool_input

    message = MagicMock()
    message.content = [text_block, tool_block]
    message.stop_reason = "tool_use"
    return message


# ---------------------------------------------------------------------------
# Task 3.4 — SSE event formatting
# ---------------------------------------------------------------------------


class TestSSEEvents:
    """SSE event formatting produces valid SSE data lines."""

    def test_text_delta_event_format(self):
        """format_sse_event with text_delta produces correct SSE format."""
        event = {"type": "text_delta", "content": "Hello!"}
        result = format_sse_event(event)
        assert result == f"data: {json.dumps(event)}\n\n"
        # Verify it's valid JSON inside the data: prefix
        payload = result.removeprefix("data: ").strip()
        parsed = json.loads(payload)
        assert parsed["type"] == "text_delta"
        assert parsed["content"] == "Hello!"

    def test_tool_call_start_event_format(self):
        """format_sse_event with tool_call_start includes tool name and id."""
        event = {"type": "tool_call_start", "tool": "create_entity", "id": "tool_abc"}
        result = format_sse_event(event)
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["type"] == "tool_call_start"
        assert parsed["tool"] == "create_entity"
        assert parsed["id"] == "tool_abc"

    def test_tool_call_result_event_format(self):
        """format_sse_event with tool_call_result includes the result dict."""
        event = {
            "type": "tool_call_result",
            "id": "tool_abc",
            "result": {"id": "entity-1", "type": "note"},
        }
        result = format_sse_event(event)
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["type"] == "tool_call_result"
        assert parsed["id"] == "tool_abc"
        assert parsed["result"]["type"] == "note"

    def test_done_event_format(self):
        """format_sse_event with done event."""
        event = {"type": "done"}
        result = format_sse_event(event)
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["type"] == "done"

    def test_error_event_format(self):
        """format_sse_event with error event includes the message."""
        event = {"type": "error", "message": "Something went wrong"}
        result = format_sse_event(event)
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["type"] == "error"
        assert parsed["message"] == "Something went wrong"

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "entity-1", "type": "note"})
    async def test_event_sequence_for_tool_call_flow(
        self, mock_execute, mock_prompt, mock_supabase
    ):
        """Full agent run with one tool call should emit events in order:
        tool_call_start -> tool_call_result -> text_delta -> done."""
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[
                _make_tool_response("create_entity", "tool_1", {"type": "note"}),
                _make_text_response("Done! I created a note."),
            ]
        )

        events = []

        async def collect_event(event):
            events.append(event)

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Create a note",
            on_event=collect_event,
        )

        event_types = [e["type"] for e in events]
        assert event_types == [
            "tool_call_start",
            "tool_call_result",
            "text_delta",
            "done",
        ]


# ---------------------------------------------------------------------------
# Task 3.2 — Single-turn agent (text-only response)
# ---------------------------------------------------------------------------


class TestSingleTurnAgent:
    """run_agent with a text-only Claude response (no tool calls)."""

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_single_turn_saves_user_turn(self, mock_prompt, mock_supabase, make_entity):
        """run_agent should save a conversation_turn entity for the user message."""
        expected_turn = make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Hello"},
            created_by="agent",
        )
        mock_supabase.set_table_response("entities", [expected_turn])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Hello!")
        )

        # Spy on save_conversation_turn by tracking table inserts
        original_table = mock_supabase.table

        insert_calls = []

        class SpyQueryBuilder:
            def __init__(self, builder):
                self._builder = builder

            def insert(self, row):
                insert_calls.append(row)
                return self._builder.insert(row)

            def __getattr__(self, name):
                return getattr(self._builder, name)

        def spy_table(name):
            builder = original_table(name)
            return SpyQueryBuilder(builder)

        mock_supabase.table = spy_table

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Hello",
        )

        # Should have saved user turn (first insert) and assistant turn (second insert)
        user_inserts = [c for c in insert_calls if c.get("state", {}).get("role") == "user"]
        assert len(user_inserts) == 1
        assert user_inserts[0]["state"]["content"] == "Hello"
        assert user_inserts[0]["type"] == "conversation_turn"

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_single_turn_saves_assistant_turn(self, mock_prompt, mock_supabase, make_entity):
        """run_agent should save a conversation_turn entity for Claude's text response."""
        expected_turn = make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "assistant", "content": "Hi there!"},
            created_by="agent",
        )
        mock_supabase.set_table_response("entities", [expected_turn])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Hi there!")
        )

        insert_calls = []
        original_table = mock_supabase.table

        class SpyQueryBuilder:
            def __init__(self, builder):
                self._builder = builder

            def insert(self, row):
                insert_calls.append(row)
                return self._builder.insert(row)

            def __getattr__(self, name):
                return getattr(self._builder, name)

        def spy_table(name):
            builder = original_table(name)
            return SpyQueryBuilder(builder)

        mock_supabase.table = spy_table

        result = await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Hello",
        )

        assert result == "Hi there!"

        assistant_inserts = [
            c for c in insert_calls if c.get("state", {}).get("role") == "assistant"
        ]
        assert len(assistant_inserts) == 1
        assert assistant_inserts[0]["state"]["content"] == "Hi there!"
        assert assistant_inserts[0]["type"] == "conversation_turn"

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_single_turn_streams_text_events(self, mock_prompt, mock_supabase):
        """run_agent should stream text_delta and done events via on_event callback."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Hi"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Hey there!")
        )

        events = []

        async def collect_event(event):
            events.append(event)

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Hi",
            on_event=collect_event,
        )

        event_types = [e["type"] for e in events]
        assert "text_delta" in event_types
        assert event_types[-1] == "done"

        text_events = [e for e in events if e["type"] == "text_delta"]
        assert text_events[0]["content"] == "Hey there!"


# ---------------------------------------------------------------------------
# Task 3.3 — Multi-turn agent (tool calls)
# ---------------------------------------------------------------------------


class TestMultiTurnAgent:
    """run_agent with tool calls — multi-turn loop."""

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "entity-1", "type": "note"})
    async def test_multi_turn_executes_tool_calls(
        self, mock_execute, mock_prompt, mock_supabase
    ):
        """When Claude returns a tool_use block, run_agent should call execute_tool."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Create a note"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[
                _make_tool_response("create_entity", "tool_1", {"type": "note", "state": {"title": "My Note"}}),
                _make_text_response("I created a note for you."),
            ]
        )

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Create a note",
        )

        mock_execute.assert_called_once_with(
            mock_supabase,
            "create_entity",
            {"type": "note", "state": {"title": "My Note"}},
            TEST_SPACE_ID,
            TEST_USER_ID,
        )

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "entity-1", "type": "note"})
    async def test_multi_turn_loops_until_text_response(
        self, mock_execute, mock_prompt, mock_supabase
    ):
        """Agent should keep looping when Claude returns tool_use, stopping on text."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Complex task"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[
                _make_tool_response("create_entity", "tool_1", {"type": "note"}),
                _make_tool_response("update_entity", "tool_2", {"id": "entity-1", "state": {"title": "Updated"}}),
                _make_text_response("All done! I created and updated the note."),
            ]
        )

        result = await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Complex task",
        )

        # Claude was called 3 times: tool_use, tool_use, text
        assert mock_anthropic.messages.create.call_count == 3
        assert result == "All done! I created and updated the note."

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "entity-1", "type": "note"})
    async def test_multi_turn_saves_all_turns(
        self, mock_execute, mock_prompt, mock_supabase
    ):
        """run_agent saves user turn at start and assistant turn at end (not intermediate tool turns)."""
        insert_calls = []
        original_table = mock_supabase.table

        class SpyQueryBuilder:
            def __init__(self, builder):
                self._builder = builder

            def insert(self, row):
                insert_calls.append(row)
                return self._builder.insert(row)

            def __getattr__(self, name):
                return getattr(self._builder, name)

        def spy_table(name):
            builder = original_table(name)
            return SpyQueryBuilder(builder)

        mock_supabase.table = spy_table

        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Create a note"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[
                _make_tool_response("create_entity", "tool_1", {"type": "note"}),
                _make_text_response("Done!"),
            ]
        )

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Create a note",
        )

        # Should have exactly 2 conversation_turn inserts: user + assistant
        turn_inserts = [c for c in insert_calls if c.get("type") == "conversation_turn"]
        assert len(turn_inserts) == 2

        roles = [c["state"]["role"] for c in turn_inserts]
        assert roles == ["user", "assistant"]
        assert turn_inserts[0]["state"]["content"] == "Create a note"
        assert turn_inserts[1]["state"]["content"] == "Done!"


# ---------------------------------------------------------------------------
# Logging in loop.py
# ---------------------------------------------------------------------------


class TestLoopLogging:
    """loop.py should use structured logging for key events."""

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_agent_turn_logged(self, mock_prompt, mock_supabase, capfd):
        """run_agent should log the start of an agent turn."""
        from agent.logging import setup_logging

        setup_logging()

        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Hi"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Hello!")
        )

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Hi",
        )

        captured = capfd.readouterr()
        log_lines = [
            json.loads(line)
            for line in captured.err.strip().split("\n")
            if line.strip()
        ]
        # Should have at least one log line from the agent loop
        agent_logs = [l for l in log_lines if l.get("logger", "").startswith("agent.")]
        assert len(agent_logs) >= 1, f"Expected agent.* log lines, got: {log_lines}"
