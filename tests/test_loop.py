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
        """format_sse_event with tool_call_start includes tool name, id, and args."""
        event = {
            "type": "tool_call_start",
            "tool": "create_entity",
            "id": "tool_abc",
            "args": {"type": "note", "state": {"title": "My Note"}},
        }
        result = format_sse_event(event)
        parsed = json.loads(result.removeprefix("data: ").strip())
        assert parsed["type"] == "tool_call_start"
        assert parsed["tool"] == "create_entity"
        assert parsed["id"] == "tool_abc"
        assert parsed["args"] == {"type": "note", "state": {"title": "My Note"}}

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

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "entity-1", "type": "note"})
    async def test_tool_call_start_includes_args(
        self, mock_execute, mock_prompt, mock_supabase
    ):
        """tool_call_start event should include the tool's input args."""
        tool_input = {"type": "note", "state": {"title": "My Note"}}
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[
                _make_tool_response("create_entity", "tool_1", tool_input),
                _make_text_response("Done!"),
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

        start_events = [e for e in events if e["type"] == "tool_call_start"]
        assert len(start_events) == 1
        assert start_events[0]["args"] == tool_input


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


class TestContextItems:
    """run_agent converts context_items (file attachments) to Claude content blocks."""

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_context_items_image_becomes_image_block(self, mock_prompt, mock_supabase):
        """An image data URL in context_items becomes a Claude image content block."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Describe this"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("It's a cat!")
        )

        context_items = [{
            "id": "file-1",
            "name": "photo.png",
            "type": "image",
            "data": "data:image/png;base64,iVBORw0KGgo=",
        }]

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Describe this",
            context_items=context_items,
        )

        # Check the messages sent to Claude
        call_args = mock_anthropic.messages.create.call_args
        messages = call_args.kwargs["messages"]
        user_content = messages[0]["content"]

        # Should be a list with an image block and a text block
        assert isinstance(user_content, list)
        image_blocks = [b for b in user_content if b.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["media_type"] == "image/png"
        assert image_blocks[0]["source"]["data"] == "iVBORw0KGgo="

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_context_items_pdf_becomes_document_block(self, mock_prompt, mock_supabase):
        """A PDF data URL in context_items becomes a Claude document content block."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Summarize this"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Summary done.")
        )

        context_items = [{
            "id": "file-2",
            "name": "report.pdf",
            "type": "document",
            "data": "data:application/pdf;base64,JVBERi0=",
        }]

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Summarize this",
            context_items=context_items,
        )

        call_args = mock_anthropic.messages.create.call_args
        messages = call_args.kwargs["messages"]
        user_content = messages[0]["content"]

        doc_blocks = [b for b in user_content if b.get("type") == "document"]
        assert len(doc_blocks) == 1
        assert doc_blocks[0]["source"]["media_type"] == "application/pdf"

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_context_items_text_decoded_as_text_block(self, mock_prompt, mock_supabase):
        """A text/plain data URL is decoded and included as a text block."""
        import base64
        text_data = base64.b64encode(b"Hello, world!").decode()

        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Read this file"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Got it.")
        )

        context_items = [{
            "id": "file-3",
            "name": "notes.txt",
            "type": "document",
            "data": f"data:text/plain;base64,{text_data}",
        }]

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Read this file",
            context_items=context_items,
        )

        call_args = mock_anthropic.messages.create.call_args
        messages = call_args.kwargs["messages"]
        user_content = messages[0]["content"]

        text_blocks = [b for b in user_content if b.get("type") == "text"]
        # Should have the file text block + the message text block
        file_text_blocks = [b for b in text_blocks if "notes.txt" in b.get("text", "")]
        assert len(file_text_blocks) == 1
        assert "Hello, world!" in file_text_blocks[0]["text"]

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_context_items_none_sends_simple_text(self, mock_prompt, mock_supabase):
        """When context_items is None, message is sent as simple text (not a list)."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Hello"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Hi!")
        )

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Hello",
        )

        call_args = mock_anthropic.messages.create.call_args
        messages = call_args.kwargs["messages"]
        # Should be simple text, not a list of blocks
        assert messages[0]["content"] == "Hello"


# ---------------------------------------------------------------------------
# Batch position injection tests
# ---------------------------------------------------------------------------


def _make_multi_tool_response(tool_blocks_data: list[tuple[str, str, dict]]):
    """Create a mock Anthropic Message with multiple tool_use blocks.

    tool_blocks_data: list of (tool_name, tool_id, tool_input)
    """
    blocks = []
    for name, tid, inp in tool_blocks_data:
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.id = tid
        block.input = inp
        blocks.append(block)

    message = MagicMock()
    message.content = blocks
    message.stop_reason = "tool_use"
    return message


class TestBatchPositionInjection:
    """When multiple create_entity calls arrive without explicit positions,
    the loop should inject tiled grid positions."""

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "e-1", "type": "note"})
    async def test_single_create_no_injection(self, mock_execute, mock_prompt, mock_supabase):
        """Single create_entity should NOT trigger batch position injection."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn", presentation="hidden",
            state={"role": "user", "content": "Create a note"}, created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(side_effect=[
            _make_tool_response("create_entity", "tool_1", {"type": "note"}),
            _make_text_response("Done!"),
        ])

        events = []
        async def collect(e): events.append(e)

        await run_agent(
            mock_supabase, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID,
            "Create a note", on_event=collect,
        )

        start_events = [e for e in events if e["type"] == "tool_call_start"]
        assert len(start_events) == 1
        # Should NOT have position injected (single entity, no batch)
        assert "position" not in start_events[0]["args"]

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "e-1", "type": "image"})
    async def test_three_creates_get_positions(self, mock_execute, mock_prompt, mock_supabase):
        """3 create_entity calls → positions injected, all distinct."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn", presentation="hidden",
            state={"role": "user", "content": "3 images"}, created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(side_effect=[
            _make_multi_tool_response([
                ("create_entity", "t1", {"type": "image", "state": {"generation_prompt": "cat"}}),
                ("create_entity", "t2", {"type": "image", "state": {"generation_prompt": "dog"}}),
                ("create_entity", "t3", {"type": "image", "state": {"generation_prompt": "bird"}}),
            ]),
            _make_text_response("Done!"),
        ])

        events = []
        async def collect(e): events.append(e)

        await run_agent(
            mock_supabase, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID,
            "Generate 3 images", on_event=collect,
        )

        start_events = [e for e in events if e["type"] == "tool_call_start"]
        assert len(start_events) == 3
        # All should have position injected
        positions = [e["args"]["position"] for e in start_events]
        coords = [(p["x"], p["y"]) for p in positions]
        assert len(set(coords)) == 3  # all distinct

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "e-1", "type": "note"})
    async def test_mixed_tools_no_injection(self, mock_execute, mock_prompt, mock_supabase):
        """1 create_entity + 1 query_entities → no batch injection (only 1 create)."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn", presentation="hidden",
            state={"role": "user", "content": "do stuff"}, created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(side_effect=[
            _make_multi_tool_response([
                ("create_entity", "t1", {"type": "note"}),
                ("query_entities", "t2", {"type": "note"}),
            ]),
            _make_text_response("Done!"),
        ])

        events = []
        async def collect(e): events.append(e)

        await run_agent(
            mock_supabase, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID,
            "do stuff", on_event=collect,
        )

        start_events = [e for e in events if e["type"] == "tool_call_start"]
        create_starts = [e for e in start_events if e["tool"] == "create_entity"]
        assert len(create_starts) == 1
        assert "position" not in create_starts[0]["args"]

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "e-1", "type": "image"})
    async def test_explicit_position_not_overwritten(self, mock_execute, mock_prompt, mock_supabase):
        """create_entity with explicit position should not be overwritten by batch injection."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn", presentation="hidden",
            state={"role": "user", "content": "images"}, created_by="agent",
        )])

        explicit_pos = {"x": 10, "y": 20, "locked": True}
        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(side_effect=[
            _make_multi_tool_response([
                ("create_entity", "t1", {"type": "image", "position": explicit_pos}),
                ("create_entity", "t2", {"type": "image"}),
                ("create_entity", "t3", {"type": "image"}),
            ]),
            _make_text_response("Done!"),
        ])

        events = []
        async def collect(e): events.append(e)

        await run_agent(
            mock_supabase, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID,
            "images", on_event=collect,
        )

        start_events = [e for e in events if e["type"] == "tool_call_start"]
        # First entity has explicit position — should be preserved
        assert start_events[0]["args"]["position"] == explicit_pos
        # Other two should have injected positions (distinct from each other)
        pos1 = start_events[1]["args"]["position"]
        pos2 = start_events[2]["args"]["position"]
        assert (pos1["x"], pos1["y"]) != (pos2["x"], pos2["y"])

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "e-1", "type": "image"})
    async def test_tool_call_start_events_include_enriched_args(self, mock_execute, mock_prompt, mock_supabase):
        """tool_call_start events should include the enriched args with positions."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn", presentation="hidden",
            state={"role": "user", "content": "2 images"}, created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(side_effect=[
            _make_multi_tool_response([
                ("create_entity", "t1", {"type": "image"}),
                ("create_entity", "t2", {"type": "image"}),
            ]),
            _make_text_response("Done!"),
        ])

        events = []
        async def collect(e): events.append(e)

        await run_agent(
            mock_supabase, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID,
            "2 images", on_event=collect,
        )

        start_events = [e for e in events if e["type"] == "tool_call_start"]
        for e in start_events:
            assert "position" in e["args"]
            pos = e["args"]["position"]
            assert "x" in pos and "y" in pos

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    @patch("agent.loop.execute_tool", new_callable=AsyncMock, return_value={"id": "e-1", "type": "image"})
    async def test_execute_tool_receives_enriched_params(self, mock_execute, mock_prompt, mock_supabase):
        """execute_tool should be called with the position-enriched params."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn", presentation="hidden",
            state={"role": "user", "content": "2 images"}, created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(side_effect=[
            _make_multi_tool_response([
                ("create_entity", "t1", {"type": "image"}),
                ("create_entity", "t2", {"type": "image"}),
            ]),
            _make_text_response("Done!"),
        ])

        await run_agent(
            mock_supabase, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID,
            "2 images",
        )

        # Both execute_tool calls should have received params with position
        assert mock_execute.call_count == 2
        for call in mock_execute.call_args_list:
            params = call[0][2]  # third positional arg
            assert "position" in params


class TestTimezoneThreading:
    """run_agent should pass user_timezone through to build_system_prompt."""

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_run_agent_passes_timezone_to_prompt(self, mock_prompt, mock_supabase):
        """When user_timezone is provided, it should be passed to build_system_prompt."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Schedule meeting at 7pm"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Done!")
        )

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Schedule meeting at 7pm",
            user_timezone="Europe/Bucharest",
        )

        # build_system_prompt should have received user_timezone
        call_kwargs = mock_prompt.call_args.kwargs
        assert call_kwargs.get("user_timezone") == "Europe/Bucharest"

    @patch("agent.loop.build_system_prompt", new_callable=AsyncMock, return_value="test prompt")
    async def test_run_agent_omits_timezone_when_none(self, mock_prompt, mock_supabase):
        """When user_timezone is not provided, it should not be in the call."""
        mock_supabase.set_table_response("entities", [_make_entity(
            entity_type="conversation_turn",
            presentation="hidden",
            state={"role": "user", "content": "Hello"},
            created_by="agent",
        )])

        mock_anthropic = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("Hi!")
        )

        await run_agent(
            mock_supabase,
            mock_anthropic,
            TEST_SPACE_ID,
            TEST_USER_ID,
            "Hello",
        )

        # build_system_prompt should have been called without user_timezone
        # (or with user_timezone=None)
        call_kwargs = mock_prompt.call_args.kwargs
        assert call_kwargs.get("user_timezone") is None


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
