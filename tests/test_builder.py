"""Tests for agent/builder.py — builder loop and tool implementations."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.builder import (
    BUILDER_TOOL_DEFINITIONS,
    _add_block,
    _update_block,
    _set_tools_schema,
    _finish_build,
    execute_builder_tool,
    builder_loop,
)
from agent.prompts.builder import build_builder_prompt
from tests.conftest import TEST_SPACE_ID

TEST_ENTITY_ID = "00000000-0000-0000-0000-000000000099"


# ---------------------------------------------------------------------------
# Helpers — mock Supabase that tracks state reads and writes
# ---------------------------------------------------------------------------


class BuilderMockClient:
    """Mock Supabase client that tracks entity state for builder tests.

    The builder tools do read-modify-write on entity.state. This mock
    stores the current state and captures every write so tests can
    assert on the final state without caring about Supabase internals.
    """

    def __init__(self, initial_state: dict | None = None):
        self._state = initial_state if initial_state is not None else {}
        self.writes: list[dict] = []

    def table(self, name: str):
        return _BuilderQueryChain(self)


class _BuilderQueryChain:
    """Chainable query builder that delegates execute to the mock client."""

    def __init__(self, client: BuilderMockClient):
        self._client = client
        self._op: str | None = None
        self._update_payload: dict | None = None

    def select(self, *args, **kwargs):
        self._op = "select"
        return self

    def update(self, payload, **kwargs):
        self._op = "update"
        self._update_payload = payload
        return self

    def eq(self, *args, **kwargs):
        return self

    def maybe_single(self):
        return self

    async def execute(self):
        result = MagicMock()
        if self._op == "select":
            result.data = {"state": self._client._state}
        elif self._op == "update":
            # Apply the write to the mock's stored state
            if self._update_payload and "state" in self._update_payload:
                self._client._state = self._update_payload["state"]
            self._client.writes.append(self._update_payload)
            result.data = None
        return result


@pytest.fixture
def builder_client():
    """Provide a BuilderMockClient with empty state."""
    return BuilderMockClient(initial_state={"building": True, "blocks": []})


# ---------------------------------------------------------------------------
# Builder tool unit tests
# ---------------------------------------------------------------------------


class TestAddBlock:
    """_add_block appends blocks to entity.state.blocks."""

    async def test_add_block_appends_to_empty(self, builder_client):
        """add_block on empty blocks list creates a one-element list."""
        block = {"id": "title", "type": "heading", "text": "My App"}
        result = await _add_block(builder_client, TEST_ENTITY_ID, block)

        assert result["ok"] is True
        assert result["block_count"] == 1
        assert builder_client._state["blocks"] == [block]

    async def test_add_block_appends_to_existing(self):
        """add_block with existing blocks appends to the end."""
        existing = {"id": "header", "type": "heading", "text": "Existing"}
        client = BuilderMockClient(
            initial_state={"building": True, "blocks": [existing]}
        )

        new_block = {"id": "checklist-1", "type": "checklist", "items": []}
        result = await _add_block(client, TEST_ENTITY_ID, new_block)

        assert result["ok"] is True
        assert result["block_count"] == 2
        assert client._state["blocks"][0] == existing
        assert client._state["blocks"][1] == new_block


class TestUpdateBlock:
    """_update_block patches an existing block by id."""

    async def test_update_block_patches_existing(self):
        """update_block merges the patch into the matching block."""
        block = {"id": "metric-1", "type": "metric", "label": "Score", "value": 42}
        client = BuilderMockClient(
            initial_state={"building": True, "blocks": [block]}
        )

        result = await _update_block(
            client, TEST_ENTITY_ID, "metric-1", {"value": 99}
        )

        assert result["ok"] is True
        updated = client._state["blocks"][0]
        assert updated["value"] == 99
        assert updated["label"] == "Score"  # preserved

    async def test_update_block_returns_error_for_missing(self, builder_client):
        """update_block with a non-existent block_id returns error."""
        result = await _update_block(
            builder_client, TEST_ENTITY_ID, "nonexistent", {"value": 1}
        )

        assert result["ok"] is False
        assert "not found" in result["error"].lower()


class TestSetToolsSchema:
    """_set_tools_schema writes tools array to state."""

    async def test_writes_tools_array(self, builder_client):
        """set_tools_schema writes the tools list to state.tools."""
        tools = [
            {
                "name": "toggle_item",
                "description": "Toggle a checklist item",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "block_id": {"type": "string"},
                        "item_id": {"type": "string"},
                    },
                },
            }
        ]

        result = await _set_tools_schema(builder_client, TEST_ENTITY_ID, tools)

        assert result["ok"] is True
        assert result["tool_count"] == 1
        assert builder_client._state["tools"] == tools


class TestFinishBuild:
    """_finish_build clears the building flag."""

    async def test_clears_building_flag(self, builder_client):
        """finish_build sets building to False."""
        assert builder_client._state["building"] is True

        result = await _finish_build(builder_client, TEST_ENTITY_ID)

        assert result["ok"] is True
        assert builder_client._state["building"] is False


# ---------------------------------------------------------------------------
# execute_builder_tool dispatcher tests
# ---------------------------------------------------------------------------


class TestExecuteBuilderTool:
    """execute_builder_tool dispatches to the correct handler."""

    async def test_dispatch_add_block(self, builder_client):
        block = {"id": "b1", "type": "text", "content": "hello"}
        result = await execute_builder_tool(
            builder_client, TEST_ENTITY_ID, TEST_SPACE_ID,
            "add_block", {"block": block},
        )
        assert result["ok"] is True
        assert builder_client._state["blocks"] == [block]

    async def test_dispatch_update_block(self):
        block = {"id": "b1", "type": "text", "content": "old"}
        client = BuilderMockClient(
            initial_state={"building": True, "blocks": [block]}
        )
        result = await execute_builder_tool(
            client, TEST_ENTITY_ID, TEST_SPACE_ID,
            "update_block", {"block_id": "b1", "patch": {"content": "new"}},
        )
        assert result["ok"] is True
        assert client._state["blocks"][0]["content"] == "new"

    async def test_dispatch_set_tools_schema(self, builder_client):
        tools = [{"name": "t", "description": "d", "inputSchema": {}}]
        result = await execute_builder_tool(
            builder_client, TEST_ENTITY_ID, TEST_SPACE_ID,
            "set_tools_schema", {"tools": tools},
        )
        assert result["ok"] is True
        assert builder_client._state["tools"] == tools

    async def test_dispatch_finish_build(self, builder_client):
        result = await execute_builder_tool(
            builder_client, TEST_ENTITY_ID, TEST_SPACE_ID,
            "finish_build", {},
        )
        assert result["ok"] is True
        assert builder_client._state["building"] is False

    async def test_dispatch_unknown_returns_error(self, builder_client):
        result = await execute_builder_tool(
            builder_client, TEST_ENTITY_ID, TEST_SPACE_ID,
            "nonexistent_tool", {},
        )
        assert "error" in result


# ---------------------------------------------------------------------------
# Helpers for mocking Anthropic responses
# ---------------------------------------------------------------------------


def _make_text_response(text: str):
    """Mock Anthropic response with just text, no tool calls."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.content = [block]
    return response


def _make_tool_response(tool_calls: list[tuple[str, dict]]):
    """Mock Anthropic response with tool_use blocks.

    tool_calls: list of (tool_name, input_dict) tuples.
    """
    blocks = []
    for name, input_data in tool_calls:
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.input = input_data
        block.id = f"tool_{name}"
        blocks.append(block)
    response = MagicMock()
    response.content = blocks
    return response


# ---------------------------------------------------------------------------
# Builder loop tests (mock Anthropic)
# ---------------------------------------------------------------------------


class TestBuilderLoop:
    """builder_loop orchestrates the Anthropic model and builder tools."""

    async def test_auto_finishes_when_no_tool_calls(self, builder_client):
        """When Anthropic returns text-only (no tool_use), builder calls _finish_build."""
        mock_anthropic = MagicMock()
        mock_anthropic.messages = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            return_value=_make_text_response("All done!")
        )

        await builder_loop(
            builder_client, mock_anthropic,
            TEST_ENTITY_ID, TEST_SPACE_ID, "Build a todo app",
        )

        assert builder_client._state["building"] is False

    async def test_processes_tool_calls(self, builder_client):
        """When Anthropic returns tool_use blocks, builder executes each tool."""
        block = {"id": "title", "type": "heading", "text": "Todo"}

        # First response: tool call to add_block.
        # Second response: text-only to end the loop.
        mock_anthropic = MagicMock()
        mock_anthropic.messages = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[
                _make_tool_response([("add_block", {"block": block})]),
                _make_text_response("Done"),
            ]
        )

        await builder_loop(
            builder_client, mock_anthropic,
            TEST_ENTITY_ID, TEST_SPACE_ID, "Build a todo app",
        )

        assert builder_client._state["blocks"] == [block]
        assert builder_client._state["building"] is False

    async def test_handles_errors_gracefully(self, builder_client):
        """On Anthropic exception, building flag is cleared and build_error is set."""
        mock_anthropic = MagicMock()
        mock_anthropic.messages = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=RuntimeError("API timeout")
        )

        await builder_loop(
            builder_client, mock_anthropic,
            TEST_ENTITY_ID, TEST_SPACE_ID, "Build a todo app",
        )

        assert builder_client._state["building"] is False
        assert "API timeout" in builder_client._state["build_error"]


# ---------------------------------------------------------------------------
# Builder prompt test
# ---------------------------------------------------------------------------


class TestBuilderPrompt:
    """build_builder_prompt includes the spec."""

    def test_includes_spec(self):
        """The spec text appears in the generated prompt."""
        spec = "A weekly meal planner with grocery list"
        prompt = build_builder_prompt(spec)
        assert spec in prompt

    def test_includes_block_library(self):
        """The prompt includes the block type reference."""
        prompt = build_builder_prompt("anything")
        assert "heading" in prompt
        assert "checklist" in prompt
        assert "key-value" in prompt

    def test_includes_design_context(self):
        """The prompt includes design direction."""
        prompt = build_builder_prompt("anything")
        assert "text-on-surface" in prompt


# ---------------------------------------------------------------------------
# Builder tool definition structure tests
# ---------------------------------------------------------------------------


class TestBuilderToolDefinitions:
    """BUILDER_TOOL_DEFINITIONS is a well-formed list."""

    def test_has_four_tools(self):
        assert len(BUILDER_TOOL_DEFINITIONS) == 4

    def test_tool_names(self):
        names = [d["name"] for d in BUILDER_TOOL_DEFINITIONS]
        assert names == ["add_block", "update_block", "set_tools_schema", "finish_build"]

    def test_each_has_required_keys(self):
        for defn in BUILDER_TOOL_DEFINITIONS:
            assert "name" in defn
            assert "description" in defn
            assert "input_schema" in defn
