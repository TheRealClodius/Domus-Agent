"""Shared test fixtures — mock Supabase client and test data."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

TEST_SPACE_ID = "00000000-0000-0000-0000-000000000001"
TEST_USER_ID = "00000000-0000-0000-0000-000000000002"


def _make_entity(
    *,
    entity_id: str | None = None,
    space_id: str = TEST_SPACE_ID,
    user_id: str = TEST_USER_ID,
    entity_type: str = "note",
    content: str | None = None,
    presentation: str = "window",
    position: dict | None = None,
    size: dict | None = None,
    z_index: int = 0,
    state: dict | None = None,
    summary: str | None = None,
    created_by: str = "user",
    archived: bool = False,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> dict:
    """Build a fake entity row dict matching the Supabase entities table schema."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": entity_id or str(uuid.uuid4()),
        "space_id": space_id,
        "user_id": user_id,
        "type": entity_type,
        "content": content,
        "presentation": presentation,
        "position": position or {"x": 50, "y": 50, "locked": False},
        "size": size or {"width": 600, "height": 400},
        "z_index": z_index,
        "state": state or {},
        "summary": summary,
        "created_by": created_by,
        "archived": archived,
        "created_at": created_at or now,
        "updated_at": updated_at or now,
    }


class MockQueryBuilder:
    """Chainable mock for Supabase query builder pattern.

    Supports: .select().eq().neq().gt().lt().gte().lte().limit().order().execute()
    All methods return self for chaining. Call .set_response() to configure the result.
    """

    def __init__(self, data=None):
        self._data = data if data is not None else []
        self._count = None

    def set_response(self, data, count=None):
        self._data = data
        self._count = count
        return self

    def select(self, *args, **kwargs):
        return self

    def insert(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def neq(self, *args, **kwargs):
        return self

    def gt(self, *args, **kwargs):
        return self

    def lt(self, *args, **kwargs):
        return self

    def gte(self, *args, **kwargs):
        return self

    def lte(self, *args, **kwargs):
        return self

    def in_(self, *args, **kwargs):
        return self

    def is_(self, *args, **kwargs):
        return self

    def ilike(self, *args, **kwargs):
        return self

    def text_search(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def single(self, *args, **kwargs):
        return self

    def maybe_single(self, *args, **kwargs):
        self._maybe_single = True
        return self

    async def execute(self):
        result = MagicMock()
        if getattr(self, "_maybe_single", False) and isinstance(self._data, list):
            result.data = self._data[0] if self._data else None
        else:
            result.data = self._data
        result.count = self._count
        return result


class MockRpcBuilder:
    """Mock for supabase.rpc() calls."""

    def __init__(self, data=None):
        self._data = data

    async def execute(self):
        result = MagicMock()
        result.data = self._data
        return result


class MockSupabaseClient:
    """Mock async Supabase client.

    Usage:
        client = MockSupabaseClient()
        client.set_table_response("entities", [entity1, entity2])
        result = await client.table("entities").select("*").execute()
        assert result.data == [entity1, entity2]
    """

    def __init__(self):
        self._table_responses: dict[str, list] = {}
        self._rpc_responses: dict[str, object] = {}

    def set_table_response(self, table_name: str, data: list) -> None:
        self._table_responses[table_name] = data

    def set_rpc_response(self, fn_name: str, data: object) -> None:
        self._rpc_responses[fn_name] = data

    def table(self, name: str) -> MockQueryBuilder:
        data = self._table_responses.get(name, [])
        return MockQueryBuilder(data)

    def rpc(self, fn_name: str, params: dict | None = None) -> MockRpcBuilder:
        data = self._rpc_responses.get(fn_name)
        return MockRpcBuilder(data)


@pytest.fixture
def mock_supabase():
    """Provide a MockSupabaseClient instance."""
    return MockSupabaseClient()


@pytest.fixture
def make_entity():
    """Provide the _make_entity factory function."""
    return _make_entity
