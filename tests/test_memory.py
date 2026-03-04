"""Tests for agent/memory.py — conversation compaction."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.memory import compact_conversation, trim_conversation_free
from agent.context import get_conversation_summaries
from tests.conftest import TEST_SPACE_ID, TEST_USER_ID


# ---------------------------------------------------------------------------
# Test infrastructure: tracking mock client
# ---------------------------------------------------------------------------


def _make_turn(index: int) -> dict:
    """Build a minimal conversation_turn entity row."""
    return {
        "id": str(uuid.uuid4()),
        "space_id": TEST_SPACE_ID,
        "type": "conversation_turn",
        "state": {
            "role": "user" if index % 2 == 0 else "assistant",
            "content": f"Message {index}",
        },
        "created_at": f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z",
    }


def _make_fact(content: str = "User likes coffee", confidence: float = 0.9) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "space_id": TEST_SPACE_ID,
        "type": "fact",
        "state": {"content": content, "confidence": confidence},
    }


def _make_trait(content: str = "Prefers bullet points", confidence: float = 0.8) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "space_id": TEST_SPACE_ID,
        "type": "personality_trait",
        "state": {"content": content, "confidence": confidence},
    }


def _make_summary(summary: str, created_at: str | None = None) -> dict:
    now = created_at or datetime.now(timezone.utc).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "space_id": TEST_SPACE_ID,
        "type": "conversation_summary",
        "state": {"summary": summary},
        "created_at": now,
    }


class _TrackingQueryBuilder:
    """QueryBuilder that routes selects by entity type and tracks inserts/updates."""

    def __init__(self, client, table_name: str):
        self._client = client
        self._table = table_name
        self._op = "select"
        self._type_filter: str | None = None
        self._id_filter: str | None = None
        self._in_filter: list | None = None
        self._insert_data: dict | None = None
        self._update_data: dict | None = None

    def select(self, *a, **kw):
        return self

    def insert(self, data, **kw):
        self._op = "insert"
        self._insert_data = data
        return self

    def update(self, data, **kw):
        self._op = "update"
        self._update_data = data
        return self

    def eq(self, col, val):
        if col == "type":
            self._type_filter = val
        elif col == "id":
            self._id_filter = val
        return self

    def in_(self, col, values):
        if col == "id":
            self._in_filter = values
        return self

    def neq(self, *a, **kw):
        return self

    def order(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    async def execute(self):
        result = MagicMock()
        if self._op == "select":
            if self._type_filter == "conversation_turn":
                result.data = self._client._turns
            elif self._type_filter == "fact":
                result.data = self._client._facts
            elif self._type_filter == "personality_trait":
                result.data = self._client._traits
            elif self._type_filter == "conversation_summary":
                result.data = self._client._summaries
            else:
                result.data = []
        elif self._op == "insert":
            self._client._inserts.append(self._insert_data)
            entity_id = str(uuid.uuid4())
            result.data = [{**self._insert_data, "id": entity_id}]
        elif self._op == "update":
            entry: dict = {"data": self._update_data}
            if self._id_filter is not None:
                entry["id"] = self._id_filter
            if self._in_filter is not None:
                entry["in_ids"] = self._in_filter
            self._client._updates.append(entry)
            result.data = [{"id": self._id_filter or "batch"}]
        return result


class TrackingClient:
    """Mock Supabase client for memory tests — routes by type, tracks side effects."""

    def __init__(self):
        self._turns: list[dict] = []
        self._facts: list[dict] = []
        self._traits: list[dict] = []
        self._summaries: list[dict] = []
        self._inserts: list[dict] = []
        self._updates: list[dict] = []

    def set_turns(self, turns: list[dict]):
        self._turns = turns

    def set_facts(self, facts: list[dict]):
        self._facts = facts

    def set_traits(self, traits: list[dict]):
        self._traits = traits

    def set_summaries(self, summaries: list[dict]):
        self._summaries = summaries

    @property
    def inserts_by_type(self) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for ins in self._inserts:
            t = ins.get("type", "unknown")
            result.setdefault(t, []).append(ins)
        return result

    def table(self, name: str) -> _TrackingQueryBuilder:
        return _TrackingQueryBuilder(self, name)


def _make_opus_mock(
    summary: str = "Test summary",
    facts: list[dict] | None = None,
    edges: list[dict] | None = None,
    duplicate_fact_ids: list[str] | None = None,
    personality_traits: list[dict] | None = None,
    duplicate_trait_ids: list[str] | None = None,
) -> AsyncMock:
    """Build a mock Anthropic client that returns structured JSON compaction response."""
    payload = {
        "summary": summary,
        "facts": facts or [],
        "edges": edges or [],
        "duplicate_fact_ids": duplicate_fact_ids or [],
        "personality_traits": personality_traits or [],
        "duplicate_trait_ids": duplicate_trait_ids or [],
    }
    content_block = MagicMock()
    content_block.text = json.dumps(payload)

    mock_response = MagicMock()
    mock_response.content = [content_block]

    mock_anthropic = AsyncMock()
    mock_anthropic.messages.create = AsyncMock(return_value=mock_response)
    return mock_anthropic


# ---------------------------------------------------------------------------
# TestCompactConversation
# ---------------------------------------------------------------------------


class TestCompactConversation:
    """compact_conversation summarizes old turns and extracts facts/edges."""

    async def test_early_return_when_few_turns(self):
        """Returns {compacted: False} immediately when turn count <= threshold."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(10)])  # 10 < 40

        mock_anthropic = _make_opus_mock()
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result == {"compacted": False}
        mock_anthropic.messages.create.assert_not_called()

    async def test_early_return_at_exactly_threshold(self):
        """Returns {compacted: False} when turn count == threshold (40)."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(40)])

        mock_anthropic = _make_opus_mock()
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result == {"compacted": False}
        mock_anthropic.messages.create.assert_not_called()

    async def test_compacts_when_above_threshold(self):
        """Triggers compaction when turn count > threshold (41 turns)."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(41)])

        mock_anthropic = _make_opus_mock(summary="A summary")
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["compacted"] is True
        mock_anthropic.messages.create.assert_called_once()

    async def test_creates_summary_entity(self):
        """Creates a conversation_summary entity with the Opus-generated summary."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        mock_anthropic = _make_opus_mock(summary="User discussed groceries and recipes.")
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["compacted"] is True
        summaries = client.inserts_by_type.get("conversation_summary", [])
        assert len(summaries) == 1
        assert summaries[0]["state"]["summary"] == "User discussed groceries and recipes."
        assert summaries[0]["type"] == "conversation_summary"
        assert summaries[0]["presentation"] == "hidden"

    async def test_creates_fact_entities(self):
        """Creates a fact entity for each new fact returned by Opus."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        facts = [
            {"content": "User likes coffee", "confidence": 0.9},
            {"content": "User is a software developer", "confidence": 0.85},
        ]
        mock_anthropic = _make_opus_mock(facts=facts)
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["fact_count"] == 2
        fact_inserts = client.inserts_by_type.get("fact", [])
        assert len(fact_inserts) == 2
        contents = {f["state"]["content"] for f in fact_inserts}
        assert contents == {"User likes coffee", "User is a software developer"}

    async def test_duplicate_facts_not_re_created(self):
        """When model reports duplicate_fact_ids, existing facts are not re-created.

        The model returns only genuinely new facts in the 'facts' array.
        duplicate_fact_ids records existing fact entity IDs that were already captured.
        We verify that the total new fact inserts equals len(facts), not more.
        """
        existing_fact_id = str(uuid.uuid4())
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])
        client.set_facts([_make_fact("User likes coffee")])

        # Model returns 1 new fact, 1 duplicate (already in DB)
        facts = [{"content": "User is a developer", "confidence": 0.8}]
        mock_anthropic = _make_opus_mock(
            facts=facts, duplicate_fact_ids=[existing_fact_id]
        )
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        # Only 1 new fact inserted — existing fact was NOT re-created
        assert result["fact_count"] == 1
        fact_inserts = client.inserts_by_type.get("fact", [])
        assert len(fact_inserts) == 1
        assert fact_inserts[0]["state"]["content"] == "User is a developer"

    async def test_archives_old_turns_preserves_recent_5(self):
        """Archives all but the 5 most recent turns in a single batch update."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        mock_anthropic = _make_opus_mock()
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        # 45 total - 5 preserved = 40 archived
        assert result["turns_archived"] == 40

        # Batch archiving: exactly 1 update with 40 IDs
        archive_updates = [
            u for u in client._updates if u.get("data", {}).get("archived") is True
        ]
        assert len(archive_updates) == 1
        assert len(archive_updates[0].get("in_ids", [])) == 40

    async def test_creates_edge_entities(self):
        """Creates edge entities for relationships returned by Opus."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        edges = [
            {
                "source_id": "entity-1",
                "target_id": "entity-2",
                "relation": "references",
                "weight": 0.8,
            }
        ]
        mock_anthropic = _make_opus_mock(edges=edges)
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["edge_count"] == 1
        edge_inserts = client.inserts_by_type.get("edge", [])
        assert len(edge_inserts) == 1
        assert edge_inserts[0]["state"]["source_id"] == "entity-1"
        assert edge_inserts[0]["state"]["target_id"] == "entity-2"

    async def test_handles_invalid_json_from_opus(self):
        """Returns {compacted: False, error: ...} when Opus returns malformed JSON."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        content_block = MagicMock()
        content_block.text = "This is not JSON at all!"
        mock_response = MagicMock()
        mock_response.content = [content_block]
        mock_anthropic = AsyncMock()
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["compacted"] is False
        assert "error" in result

    async def test_summary_entity_is_hidden(self):
        """conversation_summary entities must have presentation='hidden'."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        mock_anthropic = _make_opus_mock(summary="A test summary.")
        await compact_conversation(client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID)

        summaries = client.inserts_by_type.get("conversation_summary", [])
        assert summaries[0]["presentation"] == "hidden"

    async def test_calls_opus_model(self):
        """compact_conversation must invoke Opus (COMPACTION_MODEL) not Sonnet."""
        import config as cfg

        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        mock_anthropic = _make_opus_mock()
        await compact_conversation(client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID)

        call_kwargs = mock_anthropic.messages.create.call_args
        assert call_kwargs[1]["model"] == cfg.COMPACTION_MODEL

    async def test_creates_personality_trait_entities(self):
        """Creates a personality_trait entity for each trait returned by Opus."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        traits = [
            {"content": "Prefers concise responses", "confidence": 0.85},
            {"content": "Works in short focused sessions", "confidence": 0.7},
        ]
        mock_anthropic = _make_opus_mock(personality_traits=traits)
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["trait_count"] == 2
        trait_inserts = client.inserts_by_type.get("personality_trait", [])
        assert len(trait_inserts) == 2
        contents = {t["state"]["content"] for t in trait_inserts}
        assert contents == {"Prefers concise responses", "Works in short focused sessions"}

    async def test_personality_traits_are_hidden(self):
        """personality_trait entities must have presentation='hidden'."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        traits = [{"content": "Detail-oriented", "confidence": 0.8}]
        mock_anthropic = _make_opus_mock(personality_traits=traits)
        await compact_conversation(client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID)

        trait_inserts = client.inserts_by_type.get("personality_trait", [])
        assert len(trait_inserts) == 1
        assert trait_inserts[0]["presentation"] == "hidden"
        assert trait_inserts[0]["state"]["confidence"] == 0.8

    async def test_duplicate_traits_not_re_created(self):
        """When model reports duplicate_trait_ids, existing traits are not re-created."""
        existing_trait_id = str(uuid.uuid4())
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])
        client.set_traits([_make_trait("Prefers bullet points")])

        traits = [{"content": "Likes dark mode", "confidence": 0.75}]
        mock_anthropic = _make_opus_mock(
            personality_traits=traits, duplicate_trait_ids=[existing_trait_id]
        )
        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["trait_count"] == 1
        trait_inserts = client.inserts_by_type.get("personality_trait", [])
        assert len(trait_inserts) == 1
        assert trait_inserts[0]["state"]["content"] == "Likes dark mode"

    async def test_existing_traits_included_in_prompt(self):
        """Existing personality_trait entities are listed in the Opus prompt for deduplication."""
        existing_trait = _make_trait("Prefers lists over prose")
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])
        client.set_traits([existing_trait])

        mock_anthropic = _make_opus_mock()
        await compact_conversation(client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID)

        call_kwargs = mock_anthropic.messages.create.call_args
        user_prompt = call_kwargs[1]["messages"][0]["content"]
        assert "existing_traits" in user_prompt
        assert "Prefers lists over prose" in user_prompt

    async def test_compacts_when_opus_wraps_json_in_code_fences(self):
        """compact_conversation succeeds even when Opus wraps the JSON in code fences."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        payload = {
            "summary": "User discussed project planning.",
            "facts": [{"content": "User is planning a project", "confidence": 0.8}],
            "edges": [],
            "duplicate_fact_ids": [],
        }
        content_block = MagicMock()
        content_block.text = f"```json\n{json.dumps(payload)}\n```"
        mock_response = MagicMock()
        mock_response.content = [content_block]
        mock_anthropic = AsyncMock()
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        result = await compact_conversation(
            client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID
        )

        assert result["compacted"] is True
        summaries = client.inserts_by_type.get("conversation_summary", [])
        assert len(summaries) == 1
        assert summaries[0]["state"]["summary"] == "User discussed project planning."


# ---------------------------------------------------------------------------
# TestGetConversationSummaries
# ---------------------------------------------------------------------------


class TestGetConversationSummaries:
    """get_conversation_summaries returns summaries from conversation_summary entities."""

    async def test_returns_summaries_in_chronological_order(self, mock_supabase):
        """Summaries are returned oldest-first (DB orders by created_at ASC)."""
        summaries = [
            _make_summary("First summary", created_at="2026-01-01T00:00:00Z"),
            _make_summary("Second summary", created_at="2026-01-01T01:00:00Z"),
            _make_summary("Third summary", created_at="2026-01-01T02:00:00Z"),
        ]
        mock_supabase.set_table_response("entities", summaries)

        result = await get_conversation_summaries(mock_supabase, TEST_SPACE_ID)

        assert result == ["First summary", "Second summary", "Third summary"]

    async def test_returns_empty_list_when_no_summaries(self, mock_supabase):
        """Returns empty list when no conversation_summary entities exist."""
        mock_supabase.set_table_response("entities", [])

        result = await get_conversation_summaries(mock_supabase, TEST_SPACE_ID)

        assert result == []

    async def test_filters_entities_with_missing_summary(self, mock_supabase):
        """Entities with no 'summary' key in state are skipped."""
        entities = [
            _make_summary("Real summary"),
            {
                "id": str(uuid.uuid4()),
                "type": "conversation_summary",
                "state": {},  # no summary key
                "created_at": "2026-01-01T00:00:00Z",
            },
        ]
        mock_supabase.set_table_response("entities", entities)

        result = await get_conversation_summaries(mock_supabase, TEST_SPACE_ID)

        assert result == ["Real summary"]


# ---------------------------------------------------------------------------
# TestCompactionResponseGuard
# ---------------------------------------------------------------------------


class TestCompactionResponseGuard:
    """compact_conversation handles unexpected Anthropic response shapes gracefully."""

    async def test_compaction_handles_empty_response_content(self):
        """Returns {compacted: False, error: unexpected_response} when content is empty."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        mock_response = MagicMock()
        mock_response.content = []
        mock_anthropic = AsyncMock()
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        result = await compact_conversation(client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID)

        assert result["compacted"] is False
        assert result.get("error") == "unexpected_response"

    async def test_compaction_handles_non_text_response_content(self):
        """Returns {compacted: False, error: unexpected_response} when content block has no .text."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        # A tool_use block has no .text attribute (only .name, .id, .input)
        tool_use_block = MagicMock(spec=["type", "name", "id", "input"])
        tool_use_block.type = "tool_use"
        mock_response = MagicMock()
        mock_response.content = [tool_use_block]
        mock_anthropic = AsyncMock()
        mock_anthropic.messages.create = AsyncMock(return_value=mock_response)

        result = await compact_conversation(client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID)

        assert result["compacted"] is False
        assert result.get("error") == "unexpected_response"


# ---------------------------------------------------------------------------
# TestCompactionParallelInserts
# ---------------------------------------------------------------------------


class TestCompactionParallelInserts:
    """compact_conversation inserts facts, traits, and edges in parallel."""

    async def test_compaction_parallel_inserts_correct_counts(self):
        """All facts, traits, and edges from Opus are inserted and counted correctly."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])

        facts = [
            {"content": "User likes tea", "confidence": 0.9},
            {"content": "User works remotely", "confidence": 0.8},
            {"content": "User has a cat", "confidence": 0.7},
        ]
        traits = [
            {"content": "Prefers concise answers", "confidence": 0.85},
            {"content": "Night owl", "confidence": 0.75},
        ]
        edges = [
            {"source_id": "ent-1", "target_id": "ent-2", "relation": "related_to", "weight": 1.0},
        ]
        mock_anthropic = _make_opus_mock(
            summary="A summary",
            facts=facts,
            personality_traits=traits,
            edges=edges,
        )

        result = await compact_conversation(client, mock_anthropic, TEST_SPACE_ID, TEST_USER_ID)

        assert result["compacted"] is True
        assert result["fact_count"] == 3
        assert result["trait_count"] == 2
        assert result["edge_count"] == 1

        fact_inserts = client.inserts_by_type.get("fact", [])
        trait_inserts = client.inserts_by_type.get("personality_trait", [])
        edge_inserts = client.inserts_by_type.get("edge", [])
        assert len(fact_inserts) == 3
        assert len(trait_inserts) == 2
        assert len(edge_inserts) == 1


# ---------------------------------------------------------------------------
# TestTrimConversationFree
# ---------------------------------------------------------------------------


class TestTrimConversationFree:
    """trim_conversation_free archives old turns for free-tier users."""

    async def test_below_threshold_returns_trimmed_false(self):
        """Returns {trimmed: False} when turn count is at or below threshold."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(10)])  # 10 <= 40

        result = await trim_conversation_free(client, TEST_SPACE_ID)

        assert result == {"trimmed": False}
        assert len(client._updates) == 0

    async def test_above_threshold_archives_old_turns(self):
        """Archives all but the 5 most recent turns in a single batch update."""
        client = TrackingClient()
        client.set_turns([_make_turn(i) for i in range(45)])  # 45 > 40

        result = await trim_conversation_free(client, TEST_SPACE_ID)

        assert result["trimmed"] is True
        assert result["turns_archived"] == 40

        archive_updates = [
            u for u in client._updates if u.get("data", {}).get("archived") is True
        ]
        assert len(archive_updates) == 1
        assert len(archive_updates[0].get("in_ids", [])) == 40
