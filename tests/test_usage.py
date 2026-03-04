"""Tests for agent/usage.py — tier resolution, quota checking, rate limiting, usage recording."""

import asyncio
import uuid
from unittest.mock import MagicMock

import config as cfg
from agent.usage import (
    Tier, check_quota, check_rate_limit, get_user_tier, record_usage,
    acquire_turn_slot, release_turn_slot,
)

TEST_SPACE_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Mock client for usage tests
# ---------------------------------------------------------------------------


class _UsageQueryBuilder:
    """Chainable mock query builder for usage tests."""

    def __init__(self, client, table):
        self._client = client
        self._table = table
        self._op = "select"
        self._insert_data = None
        self._maybe_single = False

    def select(self, *a, **kw):
        return self

    def insert(self, data, **kw):
        self._op = "insert"
        self._insert_data = data
        return self

    def update(self, *a, **kw):
        return self

    def eq(self, *a, **kw):
        return self

    def gte(self, *a, **kw):
        return self

    def lte(self, *a, **kw):
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    async def execute(self):
        result = MagicMock()
        if self._op == "insert":
            self._client._inserted.append(self._insert_data)
            result.data = [{**self._insert_data, "id": str(uuid.uuid4())}]
            result.count = None
        elif self._table == "users":
            result.data = self._client._user_row
            result.count = None
        elif self._table == "usage_events":
            result.data = []
            result.count = self._client._event_count
        else:
            result.data = None
            result.count = None
        return result


class UsageMockClient:
    """Mock Supabase client for usage module tests."""

    def __init__(self):
        self._user_row = None  # None = user not found; dict = user row with plan
        self._event_count = 0
        self._inserted = []

    def set_user_plan(self, plan):
        """Configure the plan value returned by the users table query."""
        self._user_row = {"plan": plan}

    def set_event_count(self, count: int):
        """Configure the count returned by usage_events count query."""
        self._event_count = count

    @property
    def inserted_rows(self):
        return list(self._inserted)

    def table(self, name):
        return _UsageQueryBuilder(self, name)


# ---------------------------------------------------------------------------
# TestGetUserTier
# ---------------------------------------------------------------------------


class TestGetUserTier:
    """get_user_tier maps users.plan to Tier enum."""

    async def test_citizen_plan_returns_citizen_tier(self):
        client = UsageMockClient()
        client.set_user_plan("citizen")

        tier = await get_user_tier(client, str(uuid.uuid4()))

        assert tier == Tier.CITIZEN

    async def test_extra_plan_returns_extra_tier(self):
        client = UsageMockClient()
        client.set_user_plan("extra")

        tier = await get_user_tier(client, str(uuid.uuid4()))

        assert tier == Tier.EXTRA

    async def test_none_plan_returns_free_tier(self):
        client = UsageMockClient()
        client.set_user_plan(None)

        tier = await get_user_tier(client, str(uuid.uuid4()))

        assert tier == Tier.FREE

    async def test_unknown_plan_returns_free_tier(self):
        client = UsageMockClient()
        client.set_user_plan("premium_gold_ultra")

        tier = await get_user_tier(client, str(uuid.uuid4()))

        assert tier == Tier.FREE

    async def test_user_not_found_returns_free_tier(self):
        """If users table returns no row (None), default to FREE."""
        client = UsageMockClient()
        # _user_row stays None — user not found

        tier = await get_user_tier(client, str(uuid.uuid4()))

        assert tier == Tier.FREE

    async def test_result_is_cached(self):
        """Second call for same user_id returns cached tier without hitting DB."""
        client = UsageMockClient()
        client.set_user_plan("citizen")
        user_id = str(uuid.uuid4())

        tier1 = await get_user_tier(client, user_id)

        # Change plan in mock — should NOT affect cached result
        client._user_row = {"plan": "extra"}
        tier2 = await get_user_tier(client, user_id)

        assert tier1 == tier2 == Tier.CITIZEN


# ---------------------------------------------------------------------------
# TestCheckQuota
# ---------------------------------------------------------------------------


class TestCheckQuota:
    """check_quota enforces daily limits per event_type."""

    async def test_under_limit_returns_allowed_true(self):
        client = UsageMockClient()
        client.set_event_count(5)  # used 5 of 200

        result = await check_quota(client, str(uuid.uuid4()), Tier.CITIZEN, "agent_turn")

        assert result["allowed"] is True
        assert result["remaining"] == 195
        assert result["limit"] == cfg.TIER_LIMITS["citizen"]["agent_turn"]

    async def test_at_limit_returns_allowed_false(self):
        client = UsageMockClient()
        client.set_event_count(200)  # exactly at citizen limit

        result = await check_quota(client, str(uuid.uuid4()), Tier.CITIZEN, "agent_turn")

        assert result["allowed"] is False
        assert result["remaining"] == 0

    async def test_over_limit_returns_allowed_false(self):
        client = UsageMockClient()
        client.set_event_count(250)  # over limit

        result = await check_quota(client, str(uuid.uuid4()), Tier.CITIZEN, "agent_turn")

        assert result["allowed"] is False
        assert result["remaining"] == 0

    async def test_free_tier_image_generation_limit_is_zero(self):
        """FREE tier has 0 image generation quota — always denied."""
        client = UsageMockClient()
        client.set_event_count(0)

        result = await check_quota(client, str(uuid.uuid4()), Tier.FREE, "image_generation")

        assert result["allowed"] is False
        assert result["limit"] == 0

    async def test_returns_resets_at_field(self):
        """Result includes resets_at as a parseable ISO datetime string."""
        from datetime import datetime

        client = UsageMockClient()
        client.set_event_count(0)

        result = await check_quota(client, str(uuid.uuid4()), Tier.CITIZEN, "agent_turn")

        assert "resets_at" in result
        datetime.fromisoformat(result["resets_at"])  # must not raise

    async def test_zero_used_returns_full_remaining(self):
        client = UsageMockClient()
        client.set_event_count(0)

        result = await check_quota(client, str(uuid.uuid4()), Tier.EXTRA, "web_search")

        assert result["allowed"] is True
        assert result["remaining"] == cfg.TIER_LIMITS["extra"]["web_search"]
        assert result["limit"] == cfg.TIER_LIMITS["extra"]["web_search"]

    async def test_returns_all_required_fields(self):
        client = UsageMockClient()
        client.set_event_count(3)

        result = await check_quota(client, str(uuid.uuid4()), Tier.CITIZEN, "web_search")

        assert "allowed" in result
        assert "remaining" in result
        assert "limit" in result
        assert "resets_at" in result


# ---------------------------------------------------------------------------
# TestCheckRateLimit
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    """check_rate_limit enforces per-minute rate limits via in-memory sliding window."""

    def _unique_user(self) -> str:
        """Each test gets a fresh user_id to avoid cross-test state contamination."""
        return str(uuid.uuid4())

    async def test_first_request_allowed(self):
        allowed, retry_after = await check_rate_limit(self._unique_user(), Tier.FREE)

        assert allowed is True
        assert retry_after == 0

    async def test_under_limit_all_allowed(self):
        """Requests up to (rpm - 1) are all allowed."""
        user_id = self._unique_user()
        rpm = cfg.RATE_LIMITS["free"]["requests_per_minute"]  # 5

        for i in range(rpm - 1):
            allowed, _ = await check_rate_limit(user_id, Tier.FREE)
            assert allowed is True, f"Request {i + 1} should be allowed"

    async def test_at_limit_denied(self):
        """The (rpm + 1)th request within 60s is denied."""
        user_id = self._unique_user()
        rpm = cfg.RATE_LIMITS["free"]["requests_per_minute"]  # 5

        for _ in range(rpm):
            await check_rate_limit(user_id, Tier.FREE)

        allowed, retry_after = await check_rate_limit(user_id, Tier.FREE)

        assert allowed is False
        assert retry_after > 0

    async def test_over_limit_returns_positive_retry_after(self):
        """When over limit, retry_after is a positive integer."""
        user_id = self._unique_user()
        rpm = cfg.RATE_LIMITS["citizen"]["requests_per_minute"]

        for _ in range(rpm):
            await check_rate_limit(user_id, Tier.CITIZEN)

        allowed, retry_after = await check_rate_limit(user_id, Tier.CITIZEN)

        assert allowed is False
        assert isinstance(retry_after, int)
        assert retry_after >= 1

    async def test_extra_tier_allows_more_than_free(self):
        """EXTRA tier RPM is higher than FREE — extra user is allowed when free is denied."""
        free_user = self._unique_user()
        extra_user = self._unique_user()
        free_rpm = cfg.RATE_LIMITS["free"]["requests_per_minute"]

        # Exhaust free limit
        for _ in range(free_rpm):
            await check_rate_limit(free_user, Tier.FREE)
        free_allowed, _ = await check_rate_limit(free_user, Tier.FREE)

        # Extra user makes the same number of requests — should still be allowed
        for _ in range(free_rpm):
            await check_rate_limit(extra_user, Tier.EXTRA)
        extra_allowed, _ = await check_rate_limit(extra_user, Tier.EXTRA)

        assert free_allowed is False
        assert extra_allowed is True

    async def test_retry_after_is_integer(self):
        """retry_after is always an integer (not float)."""
        user_id = self._unique_user()
        rpm = cfg.RATE_LIMITS["free"]["requests_per_minute"]

        for _ in range(rpm):
            await check_rate_limit(user_id, Tier.FREE)

        _, retry_after = await check_rate_limit(user_id, Tier.FREE)

        assert isinstance(retry_after, int)

    async def test_check_rate_limit_is_now_async(self):
        """check_rate_limit is async and returns a (bool, int) tuple when awaited."""
        user_id = self._unique_user()
        result = await check_rate_limit(user_id, Tier.FREE)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert result[0] is True
        assert result[1] == 0


# ---------------------------------------------------------------------------
# TestRecordUsage
# ---------------------------------------------------------------------------


class TestRecordUsage:
    """record_usage inserts a row into usage_events and never raises."""

    async def test_inserts_correct_row_shape(self):
        client = UsageMockClient()
        user_id = str(uuid.uuid4())

        await record_usage(
            client,
            TEST_SPACE_ID,
            user_id,
            "agent_turn",
            {"input_tokens": 100, "output_tokens": 50},
        )

        assert len(client.inserted_rows) == 1
        row = client.inserted_rows[0]
        assert row["space_id"] == TEST_SPACE_ID
        assert row["user_id"] == user_id
        assert row["event_type"] == "agent_turn"
        assert row["metadata"]["input_tokens"] == 100
        assert row["metadata"]["output_tokens"] == 50
        assert "created_at" in row

    async def test_never_raises_on_db_error(self):
        """record_usage swallows all exceptions — must not propagate."""

        class FailingClient:
            def table(self, name):
                raise RuntimeError("DB connection failed")

        # Should not raise
        await record_usage(
            FailingClient(), TEST_SPACE_ID, str(uuid.uuid4()), "agent_turn", {}
        )

    async def test_empty_metadata_stored(self):
        """Empty metadata dict is stored as-is."""
        client = UsageMockClient()
        user_id = str(uuid.uuid4())

        await record_usage(client, TEST_SPACE_ID, user_id, "web_search", {})

        row = client.inserted_rows[0]
        assert row["metadata"] == {}

    async def test_various_event_types_stored_correctly(self):
        """event_type is stored verbatim."""
        client = UsageMockClient()
        user_id = str(uuid.uuid4())

        for event_type in ("agent_turn", "image_generation", "web_search", "tool_call", "compaction"):
            await record_usage(client, TEST_SPACE_ID, user_id, event_type, {})

        types = [r["event_type"] for r in client.inserted_rows]
        assert types == ["agent_turn", "image_generation", "web_search", "tool_call", "compaction"]


# ---------------------------------------------------------------------------
# TestConcurrentTurns
# ---------------------------------------------------------------------------


class TestConcurrentTurns:
    """acquire_turn_slot / release_turn_slot enforce concurrent_turns limits."""

    def _unique_user(self) -> str:
        return str(uuid.uuid4())

    async def test_acquire_turn_slot_allows_within_limit(self):
        """First request is allowed for a fresh user."""
        user_id = self._unique_user()
        allowed = await acquire_turn_slot(user_id, Tier.FREE)
        assert allowed is True
        # cleanup
        await release_turn_slot(user_id)

    async def test_acquire_turn_slot_blocks_at_limit(self):
        """Once the concurrent_turns limit is reached, further requests are denied."""
        user_id = self._unique_user()
        limit = cfg.RATE_LIMITS["free"]["concurrent_turns"]

        for _ in range(limit):
            await acquire_turn_slot(user_id, Tier.FREE)

        allowed = await acquire_turn_slot(user_id, Tier.FREE)
        assert allowed is False

        # cleanup
        for _ in range(limit):
            await release_turn_slot(user_id)

    async def test_release_turn_slot_frees_slot(self):
        """After releasing a slot, a new acquire is allowed."""
        user_id = self._unique_user()
        limit = cfg.RATE_LIMITS["free"]["concurrent_turns"]

        for _ in range(limit):
            await acquire_turn_slot(user_id, Tier.FREE)

        # Release one slot
        await release_turn_slot(user_id)

        # Should be allowed again
        allowed = await acquire_turn_slot(user_id, Tier.FREE)
        assert allowed is True

        # cleanup
        for _ in range(limit):
            await release_turn_slot(user_id)

    async def test_release_is_idempotent(self):
        """release_turn_slot called more times than acquire never goes negative."""
        user_id = self._unique_user()
        await release_turn_slot(user_id)  # no prior acquire — should not error
        await release_turn_slot(user_id)
        # Slot should still be acquirable (count never went negative)
        allowed = await acquire_turn_slot(user_id, Tier.FREE)
        assert allowed is True
        await release_turn_slot(user_id)

    async def test_concurrent_acquire_only_one_wins(self):
        """With limit=1, two simultaneous acquire calls yield exactly one True."""
        user_id = self._unique_user()
        results = await asyncio.gather(
            acquire_turn_slot(user_id, Tier.FREE),
            acquire_turn_slot(user_id, Tier.FREE),
        )
        assert sum(results) == 1  # exactly one slot granted
        # cleanup: release however many were acquired
        for acquired in results:
            if acquired:
                await release_turn_slot(user_id)
