"""Tool definitions and implementations for the Domus Agent.

5 tools: create_entity, update_entity, query_entities, read_entity, web_search.
Plus entity-as-MCP helpers: get_entity_schema, call_entity_tool, build_app, update_app.

Definitions live in TOOL_DEFINITIONS (list of dicts for Claude's tools parameter).
Implementations are async functions that take (client, space_id, user_id, params).
"""

import asyncio
import time

from agent.logging import get_logger
from agent.context import _cache_invalidate

logger = get_logger("agent.tools")

TOOL_DEFINITIONS = [
    {
        "name": "create_entity",
        "description": (
            "Create a new entity in the space. Use this to open apps, "
            "create notes, generate images, or add any content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "The app type (e.g., 'calendar', 'note', 'image')",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown body — the primary content of the entity",
                },
                "presentation": {
                    "type": "string",
                    "enum": ["window", "card", "folder", "hidden"],
                    "default": "window",
                },
                "position": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                },
                "size": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                    },
                },
                "state": {
                    "type": "object",
                    "description": (
                        "Structured data for renderers. "
                        "For image entities, include {\"generation_prompt\": \"<description>\"}. "
                        "Use only when a component needs typed fields."
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "One-line description of the entity",
                },
            },
            "required": ["type"],
        },
    },
    {
        "name": "update_entity",
        "description": (
            "Update an existing entity. State uses RFC 7396 JSON Merge Patch: "
            "provided fields overwrite, omitted preserved, null deletes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Entity ID",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown body (full replacement)",
                },
                "state": {
                    "type": "object",
                    "description": "Partial state (RFC 7396 merge patch).",
                },
                "summary": {
                    "type": "string",
                    "description": "Updated one-line description",
                },
                "position": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                    },
                },
                "size": {
                    "type": "object",
                    "properties": {
                        "width": {"type": "number"},
                        "height": {"type": "number"},
                    },
                },
                "presentation": {
                    "type": "string",
                    "enum": ["window", "card", "folder", "hidden"],
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "query_entities",
        "description": (
            "Search and list entities in the space. Returns lightweight summaries "
            "(id, type, summary). Use read_entity to get full state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Filter by entity type",
                },
                "search": {
                    "type": "string",
                    "description": "Full-text search query on summary field",
                },
                "presentation": {
                    "type": "string",
                    "description": "Filter by presentation mode",
                },
                "created_after": {
                    "type": "string",
                    "description": "ISO date — entities created after this date",
                },
                "created_before": {
                    "type": "string",
                    "description": "ISO date — entities created before this date",
                },
                "include_archived": {
                    "type": "boolean",
                    "default": False,
                },
                "limit": {
                    "type": "number",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "read_entity",
        "description": (
            "Get a single entity's full state by ID. Returns full row. "
            "Use after query_entities to load details."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Entity ID to read",
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "get_entity_schema",
        "description": (
            "Discover what actions an app entity supports. Returns a list of "
            "MCP tool schemas describing available operations and their parameters. "
            "Use this before call_entity_tool to know what you can do."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID to get the schema for",
                },
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "call_entity_tool",
        "description": (
            "Execute a structured action on an app entity. The tool_name must be "
            "one returned by get_entity_schema. Returns the new state, summary, "
            "and updated schema (tools may change based on new state)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID to call the tool on",
                },
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool to call (from get_entity_schema)",
                },
                "params": {
                    "type": "object",
                    "description": "Parameters for the tool call",
                },
            },
            "required": ["entity_id", "tool_name"],
        },
    },
    {
        "name": "build_app",
        "description": (
            "Generate a custom interactive app using React and shadcn/ui. "
            "The app renders in a sandboxed iframe. Write the component code, "
            "define the MCP tool schema, and set initial state. "
            "After building, use get_entity_schema and call_entity_tool to test it, "
            "then use update_app to improve it if needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "App display name, e.g. 'Calculator'",
                },
                "icon": {
                    "type": "string",
                    "description": "Lucide icon name in kebab-case, e.g. 'calculator'",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the app does",
                },
                "code": {
                    "type": "string",
                    "description": (
                        "React component source code (JSX). Must define a function called App. "
                        "Available in scope without imports: React hooks (useState, useEffect, "
                        "useCallback, useMemo, useRef), UI components (Button, Input, Slider, "
                        "Switch, Dialog, Tooltip), all Lucide icons, and useAppState(defaultState) "
                        "which returns [state, setState] synced to the entity."
                    ),
                },
                "schema": {
                    "type": "array",
                    "description": "MCP tool schemas the app exposes to the agent",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "inputSchema": {"type": "object"},
                        },
                        "required": ["name", "description", "inputSchema"],
                    },
                },
                "initial_state": {
                    "type": "object",
                    "description": "Initial runtime state for the app",
                },
                "width": {"type": "integer", "default": 480},
                "height": {"type": "integer", "default": 560},
            },
            "required": ["name", "icon", "description", "code", "schema", "initial_state"],
        },
    },
    {
        "name": "update_app",
        "description": (
            "Update a generated app's code, schema, or state. Use after build_app "
            "to iterate and improve the app. Only provided fields are updated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "ID of the generated app entity to update",
                },
                "code": {
                    "type": "string",
                    "description": "Updated React component source code",
                },
                "schema": {
                    "type": "array",
                    "description": "Updated MCP tool schemas",
                },
                "state_patch": {
                    "type": "object",
                    "description": "Runtime state keys to update (merge patch)",
                },
            },
            "required": ["entity_id"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for current information. Returns a direct answer with source citations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "focus": {
                    "type": "string",
                    "enum": ["general", "academic", "news"],
                    "default": "general",
                    "description": "Search domain focus",
                },
            },
            "required": ["query"],
        },
    },
]

# Anthropic prompt caching: add cache_control to the last tool definition
# so all tool definitions are cached as a single breakpoint (5-min Anthropic KV TTL).
TOOL_DEFINITIONS[-1]["cache_control"] = {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_INTERNAL_TYPES = {
    "conversation_turn",
    "conversation_summary",
    "fact",
    "personality_trait",
    "edge",
}


async def _preprocess_create(client, space_id: str, user_id: str, params: dict) -> dict:
    """Pre-process create_entity params: run image generation if needed.

    Returns a new params dict with enriched state (image_url, width, height).
    This runs server-side before either direct write or UI action mirroring.
    """
    params = dict(params)
    entity_type = params.get("type", "note")
    state = dict(params.get("state") or {})

    if "prompt" in state and "generation_prompt" not in state:
        state["generation_prompt"] = state.pop("prompt")
    if entity_type == "image" and "generation_prompt" in state:
        from agent.image_gen import generate_image

        try:
            gen_result = await generate_image(
                state["generation_prompt"], space_id, client, user_id
            )
            state["image_url"] = gen_result["public_url"]
            state["width"] = gen_result["width"]
            state["height"] = gen_result["height"]
        except Exception as e:
            logger.warning(
                "image_generation_failed",
                extra={"space_id": space_id, "error": str(e)},
            )
            state["generation_error"] = str(e)[:200]

    params["state"] = state
    return params


async def create_entity(client, space_id: str, user_id: str, params: dict) -> dict:
    """Create a new entity. Internal types insert directly to Supabase (presentation=hidden).
    User-facing types POST to the Next.js API. Always sets created_by='agent'.

    For image entities with state.generation_prompt, automatically generates
    the image via Gemini and enriches the state with image_url, width, height
    before forwarding to the frontend.
    """
    params = await _preprocess_create(client, space_id, user_id, params)
    entity_type = params.get("type", "note")
    state = dict(params.get("state") or {})

    if entity_type in _INTERNAL_TYPES:
        row: dict = {
            "space_id": space_id,
            "user_id": user_id,
            "type": entity_type,
            "presentation": "hidden",
            "state": state,
            "created_by": "agent",
        }
        for field in ("content", "summary"):
            if field in params:
                row[field] = params[field]
        result = await client.table("entities").insert(row).execute()
        await _cache_invalidate(f"entity_index:{space_id}")
        return result.data[0] if result.data else row

    import httpx
    from config import DOMUS_FRONTEND_URL, DOMUS_SERVICE_TOKEN

    body: dict = {"created_by": "agent", "type": entity_type}
    for field in ("content", "summary", "position", "size"):
        if field in params:
            body[field] = params[field]
    if state:
        body["state"] = state

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"{DOMUS_FRONTEND_URL}/api/entities",
                params={"space_id": space_id},
                headers={"Authorization": f"Bearer {DOMUS_SERVICE_TOKEN}"},
                json=body,
                timeout=10.0,
            )
    except httpx.TimeoutException:
        return {"error": "frontend_timeout", "tool": "create_entity"}
    except httpx.ConnectError:
        return {"error": "frontend_unreachable", "tool": "create_entity"}

    if resp.status_code not in (200, 201):
        return {"error": "create_failed", "status": resp.status_code, "body": resp.text}
    await _cache_invalidate(f"entity_index:{space_id}")
    return resp.json()


async def read_entity(client, space_id: str, user_id: str, params: dict) -> dict:
    """Read a single entity by ID, scoped to space_id."""
    entity_id = params["id"]
    result = (
        await client.table("entities")
        .select("*")
        .eq("id", entity_id)
        .eq("space_id", space_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        return {"error": "not_found", "id": entity_id}
    return result.data


async def query_entities(client, space_id: str, user_id: str, params: dict) -> dict:
    """Query entities matching filters. Returns lightweight summaries."""
    query = (
        client.table("entities")
        .select("id, type, summary, presentation, created_at")
        .eq("space_id", space_id)
    )

    if not params.get("include_archived", False):
        query = query.eq("archived", False)

    if "type" in params:
        query = query.eq("type", params["type"])

    if "presentation" in params:
        query = query.eq("presentation", params["presentation"])

    if "created_after" in params:
        query = query.gte("created_at", params["created_after"])

    if "created_before" in params:
        query = query.lte("created_at", params["created_before"])

    if "search" in params:
        query = query.text_search("summary", params["search"])

    limit = params.get("limit", 20)
    query = query.limit(limit)

    result = await query.execute()
    return {"entities": result.data}


async def update_entity(client, space_id: str, user_id: str, params: dict) -> dict:
    """PATCH an entity via the Next.js API. RFC 7396 merge patch is handled by the frontend."""
    import httpx
    from config import DOMUS_FRONTEND_URL, DOMUS_SERVICE_TOKEN

    entity_id = params["id"]
    body: dict = {}
    for field in ("content", "state", "summary", "position", "size", "presentation", "archived"):
        if field in params:
            body[field] = params[field]

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.patch(
                f"{DOMUS_FRONTEND_URL}/api/entities/{entity_id}",
                params={"space_id": space_id},
                headers={"Authorization": f"Bearer {DOMUS_SERVICE_TOKEN}"},
                json=body,
                timeout=10.0,
            )
    except httpx.TimeoutException:
        return {"error": "frontend_timeout", "tool": "update_entity"}
    except httpx.ConnectError:
        return {"error": "frontend_unreachable", "tool": "update_entity"}

    if resp.status_code == 409:
        return resp.json()  # {"error": "folder_has_children", "child_ids": [...], "count": N}
    if resp.status_code != 200:
        return {"error": "update_failed", "status": resp.status_code, "body": resp.text}
    await _cache_invalidate(f"entity_index:{space_id}")
    return resp.json()


# ---------------------------------------------------------------------------
# Schema discovery and structured tool calls via frontend API
# ---------------------------------------------------------------------------


async def get_entity_schema(client, space_id: str, user_id: str, params: dict) -> dict:
    """Fetch MCP tool schemas for an entity from the frontend."""
    import httpx
    from config import DOMUS_FRONTEND_URL, DOMUS_SERVICE_TOKEN

    entity_id = params["entity_id"]
    url = f"{DOMUS_FRONTEND_URL}/api/entities/{entity_id}/schema"

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                url,
                params={"space_id": space_id},
                headers={"Authorization": f"Bearer {DOMUS_SERVICE_TOKEN}"},
                timeout=10.0,
            )
    except httpx.TimeoutException:
        return {"error": "frontend_timeout", "tool": "get_entity_schema"}
    except httpx.ConnectError:
        return {"error": "frontend_unreachable", "tool": "get_entity_schema"}

    if resp.status_code != 200:
        return {"error": "schema_fetch_failed", "status": resp.status_code, "body": resp.text}
    return resp.json()


async def call_entity_tool(client, space_id: str, user_id: str, params: dict) -> dict:
    """Execute a structured tool on an entity via the frontend."""
    import httpx
    from config import DOMUS_FRONTEND_URL, DOMUS_SERVICE_TOKEN

    entity_id = params["entity_id"]
    tool_name = params["tool_name"]
    tool_params = params.get("params", {})
    url = f"{DOMUS_FRONTEND_URL}/api/entities/{entity_id}/call"

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                url,
                params={"space_id": space_id},
                headers={"Authorization": f"Bearer {DOMUS_SERVICE_TOKEN}"},
                json={"tool_name": tool_name, "params": tool_params},
                timeout=10.0,
            )
    except httpx.TimeoutException:
        return {"error": "frontend_timeout", "tool": "call_entity_tool"}
    except httpx.ConnectError:
        return {"error": "frontend_unreachable", "tool": "call_entity_tool"}

    if resp.status_code != 200:
        return {"error": "tool_call_failed", "status": resp.status_code, "body": resp.text}
    await _cache_invalidate(f"entity_index:{space_id}")
    return resp.json()


# ---------------------------------------------------------------------------
# Batch positioning
# ---------------------------------------------------------------------------


def compute_group_positions(count: int, viewport: dict | None = None) -> list[dict]:
    """Tile N entities in a grid centered at (50%, 50%).

    Returns list of {x, y, locked} dicts in percentage coordinates.
    Uses viewport dimensions to convert card pixel sizes to percentages.
    """
    import math

    cols = math.ceil(math.sqrt(count))
    rows = math.ceil(count / cols)

    vw = (viewport or {}).get("width", 1440)
    vh = (viewport or {}).get("height", 900)

    card_w, card_h, gap = 232, 300, 20  # pixels
    cell_w_pct = (card_w + gap) / vw * 100
    cell_h_pct = (card_h + gap) / vh * 100

    grid_w = cols * cell_w_pct
    grid_h = rows * cell_h_pct
    start_x = 50 - grid_w / 2 + cell_w_pct / 2
    start_y = 50 - grid_h / 2 + cell_h_pct / 2

    positions = []
    for i in range(count):
        col, row = i % cols, i // cols
        x = max(5, min(95, start_x + col * cell_w_pct))
        y = max(5, min(95, start_y + row * cell_h_pct))
        positions.append({"x": round(x, 1), "y": round(y, 1), "locked": False})
    return positions


# ---------------------------------------------------------------------------
# Builder integration
# ---------------------------------------------------------------------------


def _preprocess_build_app(params: dict) -> dict:
    """Assemble the full entity row dict from build_app params.

    Returns a dict with type, state, position, size, etc. ready for either
    direct Supabase insert or UI action mirroring.
    """
    state = {
        "_code": params["code"],
        "_schema": params["schema"],
        "_meta": {
            "name": params["name"],
            "icon": params["icon"],
            "description": params.get("description", ""),
        },
        **params.get("initial_state", {}),
    }
    return {
        "type": "app",
        "content": "",
        "presentation": "window",
        "position": params.get("position", {"x": 100, "y": 100, "locked": False}),
        "size": {
            "width": params.get("width", 480),
            "height": params.get("height", 560),
        },
        "state": state,
        "summary": params["name"],
        "created_by": "agent",
    }


async def build_app(client, space_id: str, user_id: str, params: dict) -> dict:
    """Create a generated app entity with React code, schema, and initial state."""
    row = _preprocess_build_app(params)
    row["space_id"] = space_id
    row["user_id"] = user_id

    result = await client.table("entities").insert(row).execute()
    entity = result.data[0] if result.data else row
    await _cache_invalidate(f"entity_index:{space_id}")
    return {"ok": True, "entity_id": entity.get("id", ""), "name": params["name"]}


async def update_app(client, space_id: str, user_id: str, params: dict) -> dict:
    """Update a generated app's code, schema, or state."""
    entity_id = params["entity_id"]

    result = await (
        client.table("entities")
        .select("state")
        .eq("id", entity_id)
        .eq("space_id", space_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        return {"error": "not_found", "id": entity_id}

    current_state = result.data.get("state", {}) or {}

    if "code" in params:
        current_state["_code"] = params["code"]
    if "schema" in params:
        current_state["_schema"] = params["schema"]
    if "state_patch" in params:
        for k, v in params["state_patch"].items():
            if not k.startswith("_"):
                current_state[k] = v

    await (
        client.table("entities")
        .update({"state": current_state})
        .eq("id", entity_id)
        .eq("space_id", space_id)
        .execute()
    )

    await _cache_invalidate(f"entity_index:{space_id}")
    return {"ok": True, "entity_id": entity_id}


# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------


async def web_search(client, space_id: str, user_id: str, params: dict) -> dict:
    """Search the web via Perplexity API. Returns answer + citations."""
    import httpx
    from config import PERPLEXITY_API_KEY

    if not PERPLEXITY_API_KEY:
        return {"error": "web_search_unavailable", "message": "PERPLEXITY_API_KEY not configured"}

    query = params["query"]
    focus = params.get("focus", "general")
    model = "sonar-pro" if focus == "academic" else "sonar"

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {PERPLEXITY_API_KEY}"},
            json={"model": model, "messages": [{"role": "user", "content": query}]},
            timeout=30.0,
        )

    if resp.status_code != 200:
        return {"error": "web_search_failed", "status": resp.status_code}

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return {"error": "web_search_failed", "message": "Unexpected response from Perplexity"}
    answer = choices[0].get("message", {}).get("content", "")
    citations = [{"url": url} for url in (data.get("citations") or []) if isinstance(url, str)]
    return {"answer": answer, "citations": citations}


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------


_MIRRORED_TOOLS = {"create_entity", "update_entity", "build_app", "update_app"}


async def execute_tool(
    client, name: str, params: dict, space_id: str, user_id: str,
    tier=None, bridge=None, on_event=None,
) -> dict:
    """Dispatch a tool call by name. Returns the tool result or an error dict.

    When tier is provided:
    - Enforces image_generation quota before create_entity with generation_prompt
    - Enforces web_search quota before web_search
    - Records a tool_call usage event after every execution (fire-and-forget)

    When bridge and on_event are provided (UI_ACTION_MIRRORING):
    - Visible entity mutations emit ui_action SSE events instead of writing directly
    - Frontend executes the action and POSTs result back via /agent/action-result
    - On timeout, falls back to direct execution
    """
    from agent.usage import check_quota, record_usage
    from agent.loop import _bg

    _tools = {
        "create_entity": create_entity,
        "read_entity": read_entity,
        "query_entities": query_entities,
        "update_entity": update_entity,
        "get_entity_schema": get_entity_schema,
        "call_entity_tool": call_entity_tool,
        "build_app": build_app,
        "update_app": update_app,
        "web_search": web_search,
    }
    fn = _tools.get(name)
    if fn is None:
        return {"error": "unknown_tool", "tool": name}

    # Enforce quotas before executing billable tools
    if tier is not None:
        if name == "create_entity":
            state = params.get("state", {}) or {}
            if params.get("type") == "image" and ("generation_prompt" in state or "prompt" in state):
                quota = await check_quota(client, user_id, tier, "image_generation")
                if not quota["allowed"]:
                    return {
                        "error": "quota_exhausted",
                        "type": "image_generation",
                        "message": f"Image quota exhausted. Resets at {quota['resets_at']}.",
                    }
        elif name == "web_search":
            quota = await check_quota(client, user_id, tier, "web_search")
            if not quota["allowed"]:
                return {
                    "error": "quota_exhausted",
                    "type": "web_search",
                    "message": f"Web search quota exhausted. Resets at {quota['resets_at']}.",
                }

    t0 = time.monotonic()

    # UI action mirroring — intercept visible entity mutations
    if bridge is not None and on_event is not None and name in _MIRRORED_TOOLS:
        should_mirror = True
        # Internal/hidden types skip mirroring — no UI representation
        if name == "create_entity" and params.get("type", "note") in _INTERNAL_TYPES:
            should_mirror = False

        if should_mirror:
            # Pre-process server-side (e.g. image generation) before emitting to frontend
            mirrored_params = dict(params)
            if name == "create_entity":
                mirrored_params = await _preprocess_create(client, space_id, user_id, params)
            elif name == "build_app":
                mirrored_params = _preprocess_build_app(params)

            action = bridge.create_action(action=name, params=mirrored_params)
            await on_event({
                "type": "ui_action",
                "action_id": action.action_id,
                "action": name,
                "params": mirrored_params,
            })

            # Await the future directly — resolve() may have already fired
            # during on_event (e.g. in tests) before we get here.
            try:
                result = await asyncio.wait_for(action.future, timeout=15.0)
            except asyncio.TimeoutError:
                bridge.cancel(action.action_id)
                result = {"error": "ui_action_timeout", "action_id": action.action_id}

            # On timeout, fall back to direct execution
            if result.get("error") == "ui_action_timeout":
                logger.warning(
                    "ui_action_timeout_fallback",
                    extra={"tool": name, "space_id": space_id},
                )
                # Fall through to direct execution below
            else:
                # Frontend responded — use its result
                if result.get("success") is False:
                    result = {
                        "error": "ui_action_failed",
                        "message": result.get("error", "unknown"),
                    }
                else:
                    result = result.get("result") or result
                await _cache_invalidate(f"entity_index:{space_id}")
                ms = round((time.monotonic() - t0) * 1000)
                _bg(
                    record_usage(client, space_id, user_id, "tool_call", {
                        "tool_name": name,
                        "duration_ms": ms,
                        "success": "error" not in result,
                    })
                )
                return result

    try:
        result = await fn(client, space_id, user_id, params)
    except Exception as e:
        result = {"error": "tool_execution_failed", "tool": name, "message": str(e)}

    ms = round((time.monotonic() - t0) * 1000)
    _bg(
        record_usage(client, space_id, user_id, "tool_call", {
            "tool_name": name,
            "duration_ms": ms,
            "success": "error" not in result,
        })
    )
    return result
