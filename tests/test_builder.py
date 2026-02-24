"""Tests for agent/builder.py — builder loop and tool implementations."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.builder import (
    BUILDER_TOOL_DEFINITIONS,
    _define_app,
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
    """Provide a BuilderMockClient with building state."""
    return BuilderMockClient(initial_state={"building": True, "icon": "list-checks", "name": "Test App"})


# ---------------------------------------------------------------------------
# Builder tool unit tests
# ---------------------------------------------------------------------------


SAMPLE_VIEW = [
    {"id": "title", "type": "heading", "props": {"text": "My App"}},
    {"id": "toggle", "type": "switch", "bind": "active", "props": {"label": "Active"}},
]

SAMPLE_ACTIONS = {
    "toggle_active": {
        "type": "toggle",
        "path": "active",
        "description": "Toggle active state",
    }
}

SAMPLE_STATE = {"active": False}


class TestDefineApp:
    """_define_app writes the full app definition to entity state."""

    async def test_writes_definition_and_state(self, builder_client):
        """define_app writes _def + state + clears building flag."""
        result = await _define_app(
            builder_client,
            TEST_ENTITY_ID,
            SAMPLE_VIEW,
            SAMPLE_ACTIONS,
            SAMPLE_STATE,
            "My App — {active}",
        )

        assert result["ok"] is True
        assert result["component_count"] == 2
        assert result["action_count"] == 1

        state = builder_client._state
        assert state["building"] is False
        assert state["active"] is False  # app data preserved
        assert "_def" in state

        _def = state["_def"]
        assert _def["view"] == SAMPLE_VIEW
        assert _def["actions"] == SAMPLE_ACTIONS
        assert _def["summary_template"] == "My App — {active}"
        assert _def["icon"] == "list-checks"  # preserved from initial state

    async def test_preserves_icon_from_initial_state(self):
        """define_app preserves the icon set during create_entity."""
        client = BuilderMockClient(
            initial_state={"building": True, "icon": "plane", "name": "Trip"}
        )
        await _define_app(client, TEST_ENTITY_ID, [], {}, {}, "")

        assert client._state["_def"]["icon"] == "plane"
        assert client._state["_def"]["name"] == "Trip"

    async def test_defaults_icon_to_box(self):
        """define_app defaults to 'box' icon if none set."""
        client = BuilderMockClient(initial_state={"building": True})
        await _define_app(client, TEST_ENTITY_ID, [], {}, {}, "")

        assert client._state["_def"]["icon"] == "box"


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

    async def test_dispatch_define_app(self, builder_client):
        result = await execute_builder_tool(
            builder_client, TEST_ENTITY_ID, TEST_SPACE_ID,
            "define_app",
            {
                "view": SAMPLE_VIEW,
                "actions": SAMPLE_ACTIONS,
                "state": SAMPLE_STATE,
                "summary_template": "test",
            },
        )
        assert result["ok"] is True
        assert "_def" in builder_client._state

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

    async def test_missing_field_returns_error(self, builder_client):
        """define_app with missing required field returns error."""
        result = await execute_builder_tool(
            builder_client, TEST_ENTITY_ID, TEST_SPACE_ID,
            "define_app", {"view": []},  # missing actions, state, summary_template
        )
        assert result["ok"] is False
        assert "Missing required field" in result["error"]


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

    async def test_processes_define_app_call(self, builder_client):
        """When Anthropic returns define_app tool call, builder writes the definition."""
        define_input = {
            "view": SAMPLE_VIEW,
            "actions": SAMPLE_ACTIONS,
            "state": SAMPLE_STATE,
            "summary_template": "Test",
        }

        mock_anthropic = MagicMock()
        mock_anthropic.messages = MagicMock()
        mock_anthropic.messages.create = AsyncMock(
            side_effect=[
                _make_tool_response([("define_app", define_input)]),
                _make_text_response("Done"),
            ]
        )

        await builder_loop(
            builder_client, mock_anthropic,
            TEST_ENTITY_ID, TEST_SPACE_ID, "Build a test app",
        )

        assert "_def" in builder_client._state
        assert builder_client._state["_def"]["view"] == SAMPLE_VIEW
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
# Builder prompt tests
# ---------------------------------------------------------------------------


class TestBuilderPrompt:
    """build_builder_prompt includes the spec and new references."""

    def test_includes_spec(self):
        """The spec text appears in the generated prompt."""
        spec = "A weekly meal planner with grocery list"
        prompt = build_builder_prompt(spec)
        assert spec in prompt

    def test_includes_component_catalog(self):
        """The prompt includes component types from the catalog."""
        prompt = build_builder_prompt("anything")
        assert "heading" in prompt
        assert "checklist" in prompt
        assert "key-value" in prompt
        assert "switch" in prompt
        assert "slider" in prompt

    def test_includes_action_dsl(self):
        """The prompt includes the action DSL reference."""
        prompt = build_builder_prompt("anything")
        assert "toggle_in_array" in prompt
        assert "$param" in prompt
        assert "set_many" in prompt

    def test_includes_examples(self):
        """The prompt includes complete app examples."""
        prompt = build_builder_prompt("anything")
        assert "Habit Tracker" in prompt
        assert "Trip Planner" in prompt

    def test_includes_design_context(self):
        """The prompt includes design direction."""
        prompt = build_builder_prompt("anything")
        assert "minimal" in prompt


# ---------------------------------------------------------------------------
# Builder tool definition structure tests
# ---------------------------------------------------------------------------


class TestBuilderToolDefinitions:
    """BUILDER_TOOL_DEFINITIONS is a well-formed list."""

    def test_has_two_tools(self):
        assert len(BUILDER_TOOL_DEFINITIONS) == 2

    def test_tool_names(self):
        names = [d["name"] for d in BUILDER_TOOL_DEFINITIONS]
        assert names == ["define_app", "finish_build"]

    def test_each_has_required_keys(self):
        for defn in BUILDER_TOOL_DEFINITIONS:
            assert "name" in defn
            assert "description" in defn
            assert "input_schema" in defn

    def test_define_app_has_required_properties(self):
        define_app = BUILDER_TOOL_DEFINITIONS[0]
        required = define_app["input_schema"]["required"]
        assert "view" in required
        assert "actions" in required
        assert "state" in required
        assert "summary_template" in required
