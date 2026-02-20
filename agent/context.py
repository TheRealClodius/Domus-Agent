"""Context builders — entity index, recent turns, and enrichment for the agent system prompt."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agent.prompts.iframe_builder import IFRAME_BUILDER_CONTEXT


async def get_entity_index(client, space_id: str) -> list[dict]:
    """Get all non-archived entities for the space (including hidden).

    Returns: list of {id, type, presentation, z_index, summary}
    """
    result = await (
        client.table("entities")
        .select("id, type, presentation, z_index, summary")
        .eq("space_id", space_id)
        .eq("archived", False)
        .execute()
    )
    return result.data


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
    """
    result = await (
        client.table("users")
        .select("name, username, avatar_url")
        .eq("id", user_id)
        .maybe_single()
        .execute()
    )
    return result.data


async def get_space_info(client, space_id: str) -> dict | None:
    """Fetch the space name.

    Returns dict with 'name' or None if not found.
    """
    result = await (
        client.table("spaces")
        .select("name")
        .eq("id", space_id)
        .maybe_single()
        .execute()
    )
    return result.data


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
# System prompt assembly (task 2.3)
# ---------------------------------------------------------------------------

# Base instructions for the agent
_BASE_INSTRUCTIONS = """You are Domus, an intelligent spatial assistant. You help users organize their space by creating and managing entities.

You have access to these tools:
- create_entity: Create new entities (notes, calendars, images, calendar events, composed apps, etc.)
- update_entity: Update existing entities using JSON Merge Patch
- query_entities: Search and filter entities in the space
- read_entity: Get full details of a specific entity
- get_entity_schema: Discover what structured actions an app entity supports
- call_entity_tool: Execute a structured action on an app entity
- build_app: Generate a custom interactive app (React + shadcn/ui in a sandboxed iframe)
- update_app: Update a generated app's code, schema, or state

When creating entities, always provide a clear summary.

=== App-Specific Actions ===
For app entities (calendar, sounds, etc.), prefer get_entity_schema + call_entity_tool over raw update_entity state writes.
Flow: (1) get_entity_schema to discover available tools, (2) call_entity_tool to execute — the response includes a fresh schema reflecting the new state.
If a tool is not available (e.g. pattern editing while sounds is playing), the schema tells you what IS available — adapt accordingly.
Only fall back to update_entity for entities that don't have a schema (notes, images, calendar_events).

=== Entity Types & State Shapes ===

note:
  Use the entity-level `content` field for the note body (markdown text).
  state: {} (empty — content lives in the `content` field, not state)
  presentation: 'window'
  Example: create_entity(type='note', content='# Shopping\\n- Milk\\n- Eggs', summary='Shopping list')

image:
  state: { generation_prompt: string }
  The system generates the image automatically from the prompt. Do NOT set image_url — it's filled in by the pipeline.
  presentation: 'card'
  Example: create_entity(type='image', state={ generation_prompt: 'a sunset over mountains' }, summary='Sunset landscape')

calendar:
  The calendar app window. There should only be ONE per space. Do NOT create duplicates.
  state: { view: 'month' | 'week' | 'day' | 'agenda', selected_date: 'YYYY-MM-DD' }
  Do NOT put events in the calendar entity. Events are separate entities (see calendar_event).
  presentation: 'window'

calendar_event:
  A single calendar event. Always use presentation='hidden' — the calendar app reads these automatically.
  state: { title: string, start: ISO datetime, end: ISO datetime, all_day: boolean, color?: 'default' | 'warm' | 'cool' | 'muted', attendees?: string[] }
  attendees is an optional list of attendee names (e.g. ['Alice', 'Bob']). Include when the user mentions who's attending.
  presentation: 'hidden'
  Example: create_entity(type='calendar_event', presentation='hidden', state={ title: 'Team standup', start: '2026-02-17T15:00:00', end: '2026-02-17T15:30:00', all_day: false, attendees: ['Alice', 'Bob'] }, summary='Team standup at 3pm')

=== Generated Apps (iframe-sandboxed) ===
When a user asks for a custom app (trip planner, tracker, dashboard, etc.), use build_app directly:
  build_app(name='...', icon='...', description='...', code='...', schema=[...], initial_state={...})
This creates a new app entity with type='app'. The React code runs in a sandboxed iframe.
After building, use get_entity_schema + call_entity_tool to test and interact with it.
Use update_app(entity_id=..., code=..., schema=..., state_patch=...) to iterate and fix issues.

Pick an icon name from this list (kebab-case, used in the dock):
plane, map-pin, list-checks, utensils, dumbbell, heart-pulse, graduation-cap,
briefcase, shopping-cart, calculator, book-open, trophy, palette, music,
film, camera, home, car, dog, sun, cloud, dollar-sign, clock, users,
chart-bar, target, gift, star, rocket, brain, hammer, leaf

=== Singleton Apps (do NOT create duplicates) ===
chat, settings, sounds — these are built-in apps limited to one instance per space. Only update existing ones if the user asks.

=== Presentation Modes ===
window: Full draggable window (default for most entities)
card: Smaller canvas card (default for images)
folder: Grouped stack of entities
hidden: Not rendered on canvas (use for calendar_event, conversation_turn, facts, edges)
"""


async def build_system_prompt(
    client, space_id: str, message: str,
    viewport: dict | None = None,
    focused_entity_id: str | None = None,
    visible_entity_ids: list[str] | None = None,
    user_id: str | None = None,
    user_timezone: str | None = None,
) -> str:
    """Assemble the system prompt for a given space and message.

    Combines:
    1. Base instructions (agent identity, tool descriptions, state shapes)
    2. Space name + user profile
    3. Entity index from the space
    4. Canvas context (viewport, focused entity with content, visible entities)
    5. Recent conversation turns
    6. Current date and time
    """
    parts = [_BASE_INSTRUCTIONS.strip(), IFRAME_BUILDER_CONTEXT.strip()]

    # Space name (non-critical — degrade gracefully if table missing)
    try:
        space_info = await get_space_info(client, space_id)
        if space_info and space_info.get("name"):
            parts.append(f"=== Space: {space_info['name']} ===")
    except Exception:
        pass

    # User profile (non-critical — degrade gracefully if table missing)
    if user_id:
        try:
            profile = await get_user_profile(client, user_id)
            if profile and profile.get("name"):
                parts.append(f"=== User ===\nThe user's name is {profile['name']}.")
        except Exception:
            pass

    # Entity index
    entities = await get_entity_index(client, space_id)
    if entities:
        entity_lines = []
        for e in entities:
            summary = e.get("summary") or "(no summary)"
            entity_lines.append(
                f"- [{e['type']}] {e['id']}: {summary} ({e['presentation']})"
            )
        parts.append(
            "## Current entities in this space (source of truth — if something isn't listed here, it was deleted):\n" + "\n".join(entity_lines)
        )
    else:
        parts.append("## Current entities in this space:\nNo entities yet.")

    # Canvas context
    canvas_lines = []
    if viewport:
        canvas_lines.append(
            f"Viewport: {viewport.get('width', '?')}\u00d7{viewport.get('height', '?')}"
        )
    if focused_entity_id:
        try:
            focused_full = await get_focused_entity(client, space_id, focused_entity_id)
        except Exception:
            focused_full = None
        if focused_full:
            canvas_lines.append(
                f"Focused: [{focused_full.get('type', '?')}] {focused_full['id']}: "
                f"{focused_full.get('summary', '(no summary)')}"
            )
            content = focused_full.get("content")
            if content:
                if len(content) > 2000:
                    content = content[:2000] + "... (truncated)"
                canvas_lines.append(f"Content: {content}")
            state = focused_full.get("state")
            if state:
                canvas_lines.append(f"State: {json.dumps(state)}")
        else:
            # Fallback to entity index lookup
            focused = next((e for e in entities if e["id"] == focused_entity_id), None)
            if focused:
                canvas_lines.append(
                    f"Focused: [{focused['type']}] {focused['id']}: "
                    f"{focused.get('summary', '(no summary)')}"
                )
    if visible_entity_ids:
        visible_count = len(visible_entity_ids)
        total_count = len(entities) if entities else 0
        canvas_lines.append(f"Visible: {visible_count} of {total_count} entities on screen")

    if canvas_lines:
        parts.append("=== Canvas Context ===\n" + "\n".join(canvas_lines))

    # Recent turns
    turns = await get_recent_turns(client, space_id)
    if turns:
        turn_lines = []
        for t in reversed(turns):  # Reverse to chronological order
            state = t.get("state", {})
            role = state.get("role", "unknown")
            content = state.get("content", "")
            turn_lines.append(f"{role}: {content}")
        parts.append("## Recent conversation:\n" + "\n".join(turn_lines))

    # Temporal context — show the user's local time, fall back to UTC
    now_utc = datetime.now(timezone.utc)
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
