"""Context builders — entity index, recent turns, and enrichment for the agent system prompt."""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from agent.prompts.iframe_builder import IFRAME_BUILDER_CONTEXT


# ---------------------------------------------------------------------------
# In-process TTL cache
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[float, float, Any]] = {}  # key -> (stored_at, ttl, value)
_cache_lock = asyncio.Lock()


async def _cache_get(key: str) -> Any | None:
    """Return cached value if still valid, else None."""
    async with _cache_lock:
        entry = _cache.get(key)
        if entry and time.monotonic() - entry[0] < entry[1]:
            return entry[2]
        _cache.pop(key, None)
        return None


async def _cache_set(key: str, val: Any, ttl: float = 60.0) -> None:
    """Store value in cache with TTL in seconds."""
    async with _cache_lock:
        _cache[key] = (time.monotonic(), ttl, val)


async def _cache_invalidate(key: str) -> None:
    """Remove a key from the cache."""
    async with _cache_lock:
        _cache.pop(key, None)


# ---------------------------------------------------------------------------
# Focus hints and session helpers
# ---------------------------------------------------------------------------

_FOCUS_HINTS: dict[str, str] = {
    "calendar": "Tip: use query_entities(type='calendar_event') to load this calendar's events.",
}

SESSION_WINDOW_MINUTES = 90


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string to a timezone-aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# Supabase data fetchers
# ---------------------------------------------------------------------------


async def _noop() -> None:
    """No-op coroutine for use in asyncio.gather when a query is conditional."""
    return None


# Internal memory types — never surfaced in the entity index.
# User-created entities (app, note, image, calendar, …) are always included
# regardless of presentation so the agent sees docked/hidden apps too.
_INTERNAL_ENTITY_TYPES = frozenset({
    "conversation_turn",
    "conversation_summary",
    "fact",
    "edge",
    "personality_trait",
})

# Types that have no valid hidden/docked state.
# If an entity of these types has presentation='hidden', it's a bug — exclude it.
# App-like types (calendar, composed, sounds, etc.) may be legitimately docked.
_TYPES_WITHOUT_HIDDEN_STATE = frozenset({
    "image",
    "folder",
    "note",
})


async def get_entity_index(client, space_id: str) -> list[dict]:
    """Get all non-archived user-facing entities for the space.

    Returns: list of {id, type, presentation, z_index, summary}
    Internal memory types (turns, facts, edges, traits, summaries) are excluded.
    User-created entities are always included — even hidden ones (docked apps).
    Cached for 60 seconds to avoid re-querying on every tool-loop iteration.
    """
    cache_key = f"entity_index:{space_id}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    result = await (
        client.table("entities")
        .select("id, type, presentation, z_index, summary, updated_at, created_at")
        .eq("space_id", space_id)
        .eq("archived", False)
        .execute()
    )
    entities = [
        e for e in (result.data or [])
        if e.get("type") not in _INTERNAL_ENTITY_TYPES
        and not (
            e.get("presentation") == "hidden"
            and e.get("type") in _TYPES_WITHOUT_HIDDEN_STATE
        )
    ]
    await _cache_set(cache_key, entities, ttl=60.0)
    return entities


async def get_recent_turns(client, space_id: str, limit: int = 5) -> list[dict]:
    """Get the most recent conversation turns for the space.

    Returns: list of {state, created_at} dicts ordered newest-first.
    Each state contains role + content.
    """
    result = await (
        client.table("entities")
        .select("state, created_at")
        .eq("space_id", space_id)
        .eq("type", "conversation_turn")
        .eq("archived", False)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


async def get_user_profile(client, user_id: str) -> dict | None:
    """Fetch user name/username from the users table.

    Returns the row dict or None if not found (guest/anonymous).
    Cached for 300 seconds.
    """
    cache_key = f"user_profile:{user_id}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    result = await (
        client.table("users")
        .select("name, username, avatar_url")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    data = result.data
    await _cache_set(cache_key, data, ttl=300.0)
    return data


async def get_space_info(client, space_id: str) -> dict | None:
    """Fetch the space name.

    Returns dict with 'name' or None if not found.
    Cached for 300 seconds.
    """
    cache_key = f"space_info:{space_id}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return cached

    result = await (
        client.table("spaces")
        .select("name")
        .eq("id", space_id)
        .maybe_single()
        .execute()
    )
    data = result.data
    await _cache_set(cache_key, data, ttl=300.0)
    return data


async def get_conversation_summaries(client, space_id: str) -> list[str]:
    """Get conversation summaries for the space, oldest first.

    Returns: list of summary strings from conversation_summary entities.
    """
    result = await (
        client.table("entities")
        .select("state, created_at")
        .eq("space_id", space_id)
        .eq("type", "conversation_summary")
        .eq("archived", False)
        .order("created_at", desc=False)
        .execute()
    )
    data = result.data or []
    return [
        r["state"]["summary"]
        for r in data
        if r.get("state") and r["state"].get("summary")
    ]


async def get_personality_traits(client, space_id: str) -> list[dict]:
    """Get personality traits for the space.

    Returns: list of {content, confidence} dicts from personality_trait entities.
    """
    result = await (
        client.table("entities")
        .select("state")
        .eq("space_id", space_id)
        .eq("type", "personality_trait")
        .eq("archived", False)
        .execute()
    )
    data = result.data or []
    return [
        {
            "content": r["state"]["content"],
            "confidence": r["state"].get("confidence", 1.0),
        }
        for r in data
        if r.get("state") and r["state"].get("content")
    ]


async def get_user_facts(client, space_id: str) -> list[dict]:
    """Get known facts about the user, sorted by confidence descending, capped at 20.

    Returns: list of {content, confidence} dicts from fact entities.
    """
    result = await (
        client.table("entities")
        .select("state")
        .eq("space_id", space_id)
        .eq("type", "fact")
        .eq("archived", False)
        .execute()
    )
    data = result.data or []
    facts = [
        {
            "content": r["state"]["content"],
            "confidence": r["state"].get("confidence", 1.0),
        }
        for r in data
        if r.get("state") and r["state"].get("content")
    ]
    facts.sort(key=lambda f: f["confidence"], reverse=True)
    return facts[:20]


async def get_focused_entity(client, space_id: str, entity_id: str) -> dict | None:
    """Fetch full entity content and state for the focused entity.

    Returns dict with id, type, content, state, summary, presentation — or None.
    """
    result = await (
        client.table("entities")
        .select("id, type, content, state, summary, presentation")
        .eq("id", entity_id)
        .eq("space_id", space_id)
        .maybe_single()
        .execute()
    )
    return result.data


# ---------------------------------------------------------------------------
# System prompt assembly
# ---------------------------------------------------------------------------

# Base instructions for the agent
_BASE_INSTRUCTIONS = """You are Domus, an intelligent spatial assistant. You live inside a user's space — a canvas of entities they own. Your job is to help them think, build, and evolve that space.

=== Personality ===
Be concise and direct. No emojis — ever. No unsolicited suggestion lists. Don't narrate what you're about to do; just do it. Match the user's register: casual if casual, brief if brief. Be genuinely curious — when something is unclear, investigate with your tools before asking.

=== Tools ===
create_entity     — create a new entity in the space
update_entity     — modify an entity (JSON Merge Patch: provided fields overwrite, null deletes, omitted fields preserved, arrays replaced entirely)
query_entities    — search and filter entities
read_entity       — get full details of a specific entity
get_entity_schema — discover what structured actions an entity supports
call_entity_tool  — execute a structured action on an entity
build_app         — generate a custom interactive app (React + shadcn/ui, sandboxed iframe)
update_app        — update a generated app's code, schema, or state
web_search        — search the web for current information

=== Discovery ===
Everything in the space is discoverable through your tools. You don't need prior knowledge of what types exist or what state shapes they use.

Before acting on an entity you haven't inspected: call read_entity or get_entity_schema first.
Any entity may expose a schema. Call get_entity_schema on any entity to discover what actions it supports, then call_entity_tool to execute them.
Prefer call_entity_tool over update_entity whenever an entity exposes a schema — it preserves internal logic and side effects.
Before creating something, use query_entities to check if it already exists.

=== Creation ===
You can create any kind of entity. Set the presentation at creation time:
- window: draggable canvas window
- card: compact canvas card
- folder: grouped entity stack
- hidden: minimized/docked — not visible on canvas but still exists

Apps with presentation='hidden' are docked. To open a docked app, update its presentation to 'window'.

When uncertain about an entity's expected shape, look at similar entities already in the space.

=== Custom Apps ===
When a user wants an interactive tool, use build_app (React + shadcn/ui, sandboxed iframe). Use update_app to iterate.

=== Presence ===
When you create or update entities, the user sees the same animations and transitions as if they performed the action themselves. Entities you create appear selected on the canvas. When you read an entity, the user sees a visual focus indicator on it.

=== Attitude ===
You are not limited by what you know upfront. Everything is findable. The space is yours to explore and operate. Take initiative; be resourceful; don't ask when you can discover.
"""


def _build_semi_static_block(
    space: dict | None,
    user: dict | None,
    entities: list[dict],
    summaries: list[str] | None = None,
    traits: list[dict] | None = None,
    user_facts: list[dict] | None = None,
) -> str:
    """Build block 1: space name, user profile, entity index, conversation history."""
    parts = []

    if space and space.get("name"):
        parts.append(f"=== Space: {space['name']} ===")

    if user and user.get("name"):
        user_section = f"=== User ===\nThe user's name is {user['name']}."
        if user.get("username"):
            user_section += f" Their username is {user['username']}."
        if user_facts:
            facts_bullets = "\n".join(f"- {f['content']}" for f in user_facts)
            user_section += f"\n\nKnown facts about this user:\n{facts_bullets}"
        parts.append(user_section)

    if summaries:
        summary_lines = "\n".join(f"- {s}" for s in summaries)
        parts.append(f"=== Conversation History ===\n{summary_lines}")

    if traits:
        trait_bullets = "\n".join(
            f"- {t['content']} (confidence: {t['confidence']})" for t in traits
        )
        parts.append(f"=== Agent Personality ===\n{trait_bullets}")

    if entities:
        sorted_by_recency = sorted(
            entities, key=lambda e: e.get("updated_at") or "", reverse=True
        )
        recent = sorted_by_recency[:3]
        recent_lines = [
            f"- [{e['type']}] {e['id']}: {e.get('summary') or '(no summary)'}"
            for e in recent
        ]
        parts.append("=== Recently Active ===\n" + "\n".join(recent_lines))

        entity_lines = []
        for e in entities:
            summary = e.get("summary") or "(no summary)"
            entity_lines.append(
                f"- [{e['type']}] {e['id']}: {summary} ({e['presentation']})"
            )
        parts.append(
            "## Current entities in this space (source of truth — if something isn't listed here, it was deleted):\n"
            + "\n".join(entity_lines)
        )
    else:
        parts.append("## Current entities in this space:\nNo entities yet.")

    return "\n\n".join(parts)


def _build_dynamic_block(
    entities: list[dict],
    focused: dict | None,
    turns: list[dict],
    viewport: dict | None,
    visible_entity_ids: list[str] | None,
    user_timezone: str | None,
) -> str:
    """Build block 2: canvas context, session signal, recent turns, temporal context."""
    parts = []

    # Compute now_utc early — used for both session window and temporal display
    now_utc = datetime.now(timezone.utc)

    # Canvas context
    canvas_lines = []
    if viewport:
        canvas_lines.append(
            f"Viewport: {viewport.get('width', '?')}\u00d7{viewport.get('height', '?')}"
        )
    if focused:
        canvas_lines.append(
            f"Focused: [{focused.get('type', '?')}] {focused['id']}: "
            f"{focused.get('summary', '(no summary)')}"
        )
        content = focused.get("content")
        if content:
            if len(content) > 2000:
                content = content[:2000] + "... (truncated)"
            canvas_lines.append(f"Content: {content}")
        state = focused.get("state")
        if state:
            state_json = json.dumps(state)
            if len(state_json) > 4000:
                state_json = state_json[:4000] + "... (truncated)"
            canvas_lines.append(f"State: {state_json}")
        # 19.8: type-aware hint for focused entity
        hint = _FOCUS_HINTS.get(focused.get("type", ""))
        if hint:
            canvas_lines.append(hint)
    elif entities and viewport is None and visible_entity_ids is None:
        pass  # no canvas context to add

    # 19.6: list visible entities by id+summary instead of showing a count
    if visible_entity_ids:
        entity_map = {e["id"]: e for e in (entities or [])}
        focused_id = focused.get("id") if focused else None
        visible_lines = [
            f"- [{e['type']}] {e['id']}: {e.get('summary') or '(no summary)'}"
            for eid in visible_entity_ids
            if (e := entity_map.get(eid)) and eid != focused_id
        ]
        if visible_lines:
            canvas_lines.append("Also visible:\n" + "\n".join(visible_lines))

    if canvas_lines:
        parts.append("=== Canvas Context ===\n" + "\n".join(canvas_lines))

    # 19.9: surface entities created within the session window
    session_cutoff = now_utc - timedelta(minutes=SESSION_WINDOW_MINUTES)
    session_entities = [
        e for e in (entities or [])
        if e.get("created_at") and _parse_iso(e["created_at"]) >= session_cutoff
    ]
    if session_entities:
        session_lines = [
            f"- [{e['type']}] {e['id']}: {e.get('summary') or '(no summary)'}"
            for e in session_entities
        ]
        parts.append("=== Created this session ===\n" + "\n".join(session_lines))

    # Recent turns
    if turns:
        turn_lines = []
        for t in reversed(turns):  # Reverse to chronological order
            state = t.get("state", {})
            role = state.get("role", "unknown")
            content = state.get("content", "")
            turn_lines.append(f"{role}: {content}")
        parts.append("## Recent conversation:\n" + "\n".join(turn_lines))

    # Temporal context — show the user's local time, fall back to UTC
    if user_timezone:
        try:
            local_now = now_utc.astimezone(ZoneInfo(user_timezone))
        except KeyError:
            local_now = now_utc
    else:
        local_now = now_utc
    time_str = local_now.strftime("%H:%M %d %b %Y")
    parts.append(f"=== Current User Date & Time ===\n{time_str}")

    return "\n\n".join(parts)


async def _safe_get_space_info(client, space_id: str) -> dict | None:
    try:
        return await get_space_info(client, space_id)
    except Exception:
        return None


async def _safe_get_user_profile(client, user_id: str) -> dict | None:
    try:
        return await get_user_profile(client, user_id)
    except Exception:
        return None


async def _safe_get_focused_entity(client, space_id: str, entity_id: str) -> dict | None:
    try:
        return await get_focused_entity(client, space_id, entity_id)
    except Exception:
        return None


async def build_system_prompt(
    client, space_id: str, message: str,
    viewport: dict | None = None,
    focused_entity_id: str | None = None,
    visible_entity_ids: list[str] | None = None,
    user_id: str | None = None,
    user_timezone: str | None = None,
) -> list[dict]:
    """Assemble the system prompt as a list of cacheable blocks.

    Returns three blocks:
      [0] Static: base instructions + iframe builder context (cache_control=ephemeral)
      [1] Semi-static: space, user, entity index (cache_control=ephemeral)
      [2] Dynamic: canvas context, recent turns, current time (no cache_control)

    All independent queries run in parallel via asyncio.gather.
    """
    # Run all independent queries in parallel
    (space, user, entities, turns, focused, summaries, traits, user_facts) = await asyncio.gather(
        _safe_get_space_info(client, space_id),
        _safe_get_user_profile(client, user_id) if user_id else _noop(),
        get_entity_index(client, space_id),
        get_recent_turns(client, space_id),
        _safe_get_focused_entity(client, space_id, focused_entity_id)
        if focused_entity_id else _noop(),
        get_conversation_summaries(client, space_id),
        get_personality_traits(client, space_id),
        get_user_facts(client, space_id),
    )

    # Block 0: fully static (base instructions + iframe builder context)
    static_text = _BASE_INSTRUCTIONS.strip() + "\n\n" + IFRAME_BUILDER_CONTEXT.strip()

    # Block 1: semi-static (space name, user profile, entity index, conversation history)
    semi_static_text = _build_semi_static_block(
        space, user, entities or [],
        summaries=summaries or [],
        traits=traits or [],
        user_facts=user_facts or [],
    )

    # Block 2: fully dynamic (canvas context, recent turns, temporal context)
    dynamic_text = _build_dynamic_block(
        entities or [], focused, turns or [],
        viewport, visible_entity_ids, user_timezone,
    )

    return [
        {"type": "text", "text": static_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": semi_static_text, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic_text},
    ]
