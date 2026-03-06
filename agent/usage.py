"""Usage tracking, tier resolution, quota enforcement, and rate limiting.

Tier hierarchy:
    FREE     — null/unrecognized plan (authenticated but unsubscribed)
    CITIZEN  — 'citizen' plan (base subscription)
    EXTRA    — 'extra' plan (pay-as-you-go)

Key functions:
    get_user_tier      — resolve Tier from users.plan, cached 5 min
    check_quota        — daily quota check against usage_events count
    check_rate_limit   — in-memory sliding-window RPM limiter
    record_usage       — insert a usage_events row (fire-and-forget friendly)
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from enum import Enum

import config as cfg
from agent.logging import get_logger

logger = get_logger("agent.usage")


# ---------------------------------------------------------------------------
# Tier enum
# ---------------------------------------------------------------------------


class Tier(str, Enum):
    FREE = "free"
    CITIZEN = "citizen"
    EXTRA = "extra"


# ---------------------------------------------------------------------------
# In-process caches
# ---------------------------------------------------------------------------

# Note: _tier_cache is not lock-protected. Two concurrent misses for the same user_id
# will both query the DB and write the same value — this is an intentional TOCTOU
# trade-off (idempotent, benign) to avoid serializing all tier lookups under a lock.
_tier_cache: dict[str, tuple[float, "Tier"]] = {}  # user_id -> (stored_at, tier)
_TIER_CACHE_TTL = 300.0  # 5 minutes

# NOTE: _rate_windows and _active_turns are in-process only. On service restart or
# across multiple Railway replicas, these reset to zero and are not shared.
# Proper multi-instance fix: use Redis for atomic sliding-window counters (Phase 21+).
_rate_windows: dict[str, list[float]] = {}  # user_id -> list of request timestamps
_rate_windows_lock = asyncio.Lock()

_active_turns: dict[str, int] = {}  # user_id -> active turn count
_active_turns_lock = asyncio.Lock()

# Per-user lock for billable quota checks — prevents TOCTOU race on image_generation.
# In-process only (same multi-instance caveat as rate limits above).
_quota_locks: dict[str, asyncio.Lock] = {}
_quota_locks_dict_lock = asyncio.Lock()


async def _get_quota_lock(user_id: str) -> asyncio.Lock:
    """Return a per-user asyncio.Lock for serialising quota check + execute pairs."""
    async with _quota_locks_dict_lock:
        if user_id not in _quota_locks:
            _quota_locks[user_id] = asyncio.Lock()
        return _quota_locks[user_id]


# ---------------------------------------------------------------------------
# Tier resolution
# ---------------------------------------------------------------------------


async def get_user_tier(client, user_id: str) -> "Tier":
    """Resolve user tier from users.plan. Result cached for 5 minutes.

    Mapping:
        'citizen'          -> Tier.CITIZEN
        'extra'            -> Tier.EXTRA
        null / unrecognized -> Tier.FREE
    """
    cached = _tier_cache.get(user_id)
    if cached:
        stored_at, tier = cached
        if time.monotonic() - stored_at < _TIER_CACHE_TTL:
            return tier

    try:
        result = await (
            client.table("users")
            .select("plan")
            .eq("id", user_id)
            .maybe_single()
            .execute()
        )
        plan = result.data.get("plan") if result.data else None
    except Exception as e:
        logger.warning(
            "get_user_tier_failed",
            extra={"user_id": user_id, "error": str(e)},
        )
        plan = None

    tier = {"citizen": Tier.CITIZEN, "extra": Tier.EXTRA}.get(plan or "", Tier.FREE)

    _tier_cache[user_id] = (time.monotonic(), tier)
    return tier


# ---------------------------------------------------------------------------
# Quota check
# ---------------------------------------------------------------------------


async def check_quota(client, user_id: str, tier: "Tier", event_type: str) -> dict:
    """Check whether user is within their daily quota for the given event_type.

    Counts usage_events rows for this user + event_type since midnight UTC today.
    Compares against TIER_LIMITS[tier.value][event_type].

    Returns:
        {
            "allowed": bool,
            "remaining": int,
            "limit": int,
            "resets_at": ISO 8601 string (tomorrow midnight UTC),
        }
    """
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    resets_at = (today_start + timedelta(days=1)).isoformat()

    limit = cfg.TIER_LIMITS[tier.value][event_type]

    result = await (
        client.table("usage_events")
        .select("*", count="exact")
        .eq("user_id", user_id)
        .eq("event_type", event_type)
        .gte("created_at", today_start.isoformat())
        .execute()
    )

    used = result.count or 0
    remaining = max(0, limit - used)

    return {
        "allowed": used < limit,
        "remaining": remaining,
        "limit": limit,
        "resets_at": resets_at,
    }


# ---------------------------------------------------------------------------
# Rate limit (in-memory sliding window)
# ---------------------------------------------------------------------------


async def check_rate_limit(user_id: str, tier: "Tier") -> tuple[bool, int]:
    """Check and record a request in the sliding-window rate limiter.

    Prunes timestamps older than 60s, then checks against
    RATE_LIMITS[tier.value]["requests_per_minute"].

    When allowed, records the current timestamp.
    When denied, does NOT record (allows re-try without consuming a slot).

    Returns:
        (allowed: bool, retry_after_seconds: int)
        retry_after_seconds is 0 when allowed.
    """
    now = time.monotonic()
    rpm = cfg.RATE_LIMITS[tier.value]["requests_per_minute"]

    async with _rate_windows_lock:
        window = _rate_windows.get(user_id, [])
        # Prune timestamps outside the 60-second window
        window = [t for t in window if now - t < 60.0]

        if len(window) >= rpm:
            oldest = window[0]
            retry_after = max(1, int(60.0 - (now - oldest)) + 1)
            _rate_windows[user_id] = window
            return False, retry_after

        window.append(now)
        _rate_windows[user_id] = window
        return True, 0


# ---------------------------------------------------------------------------
# Concurrent turn slot management
# ---------------------------------------------------------------------------


async def acquire_turn_slot(user_id: str, tier: "Tier") -> bool:
    """Acquire a concurrent turn slot for a user.

    Returns True if slot was acquired, False if at the limit.
    """
    limit = cfg.RATE_LIMITS[tier.value]["concurrent_turns"]
    async with _active_turns_lock:
        current = _active_turns.get(user_id, 0)
        if current >= limit:
            return False
        _active_turns[user_id] = current + 1
        return True


async def release_turn_slot(user_id: str) -> None:
    """Release a concurrent turn slot. Safe to call even if no slot held."""
    async with _active_turns_lock:
        current = _active_turns.get(user_id, 0)
        if current > 0:
            _active_turns[user_id] = current - 1


# ---------------------------------------------------------------------------
# Usage recording
# ---------------------------------------------------------------------------


async def record_usage(
    client, space_id: str, user_id: str, event_type: str, metadata: dict
) -> None:
    """Insert a usage_events row. Designed to be called via asyncio.create_task().

    Never raises — failures are logged as warnings so they never block the SSE stream.
    """
    try:
        row = {
            "space_id": space_id,
            "user_id": user_id,
            "event_type": event_type,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await client.table("usage_events").insert(row).execute()
    except Exception as e:
        logger.warning(
            "record_usage_failed",
            extra={"user_id": user_id, "event_type": event_type, "error": str(e)},
        )
