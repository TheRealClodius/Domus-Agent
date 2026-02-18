"""Tool definitions and implementations for the Domus Agent.

4 tools for v0: create_entity, update_entity, query_entities, read_entity.
web_search is deferred.

Definitions live in TOOL_DEFINITIONS (list of dicts for Claude's tools parameter).
Implementations are async functions that take (client, space_id, user_id, params).
"""

from agent.logging import get_logger

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
    # SPIKE: entity-as-mcp — discover what tools an entity supports
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
    # SPIKE: entity-as-mcp — call a structured tool on an entity
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
]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def create_entity(client, space_id: str, user_id: str, params: dict) -> dict:
    """Insert a new entity. Always sets created_by='agent'.

    For image entities with state.generation_prompt, automatically generates
    the image via Gemini and enriches the state with image_url, width, height.
    """
    entity_type = params["type"]
    state = dict(params.get("state", {}))

    # Image generation: type='image' + generation_prompt triggers Gemini pipeline
    is_image_gen = entity_type == "image" and "generation_prompt" in state
    if is_image_gen:
        from agent.image_gen import generate_image

        try:
            gen_result = await generate_image(
                state["generation_prompt"], space_id, client
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

    # Image-specific defaults
    if entity_type == "image":
        default_presentation = "card"
        default_size = {"width": 232, "height": 300}
    else:
        default_presentation = "window"
        default_size = {"width": 600, "height": 400}

    row = {
        "space_id": space_id,
        "user_id": user_id,
        "type": entity_type,
        "content": params.get("content"),
        "presentation": params.get("presentation", default_presentation),
        "position": params.get("position", {"x": 50, "y": 50, "locked": False}),
        "size": params.get("size", default_size),
        "state": state,
        "summary": params.get("summary"),
        "created_by": "agent",
    }
    try:
        result = await client.table("entities").insert(row).execute()
    except Exception as e:
        logger.error(
            "create_entity_insert_failed",
            extra={
                "space_id": space_id,
                "entity_type": entity_type,
                "error": str(e)[:500],
                "row_keys": list(row.keys()),
                "presentation": row.get("presentation"),
                "state_keys": list(state.keys()) if isinstance(state, dict) else str(type(state)),
            },
        )
        raise
    return result.data[0] if result.data else row


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
    """Update entity using RFC 7396 merge patch for state."""
    entity_id = params["id"]

    # Read current entity
    current = (
        await client.table("entities")
        .select("*")
        .eq("id", entity_id)
        .eq("space_id", space_id)
        .maybe_single()
        .execute()
    )
    if not current.data:
        return {"error": "not_found", "id": entity_id}

    current_data = current.data

    # Build update dict
    updates = {}

    if "state" in params:
        # RFC 7396 merge patch
        merged = _merge_patch(current_data.get("state", {}), params["state"])
        updates["state"] = merged

    for field in ("content", "summary", "position", "size", "presentation"):
        if field in params:
            updates[field] = params[field]

    if not updates:
        return current_data

    result = await (
        client.table("entities")
        .update(updates)
        .eq("id", entity_id)
        .eq("space_id", space_id)
        .execute()
    )
    if isinstance(result.data, list) and result.data:
        return result.data[0]
    # Construct the merged row from current data + updates
    return {**current_data, **updates}


def _merge_patch(target: dict, patch: dict) -> dict:
    """RFC 7396 JSON Merge Patch — pure Python implementation."""
    if not isinstance(patch, dict):
        return patch
    result = dict(target)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_patch(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# SPIKE: entity-as-mcp — schema discovery and structured tool calls
# ---------------------------------------------------------------------------


async def get_entity_schema(client, space_id: str, user_id: str, params: dict) -> dict:
    """Fetch MCP tool schemas for an entity from the frontend."""
    import httpx
    from config import DOMUS_FRONTEND_URL, DOMUS_SERVICE_TOKEN

    entity_id = params["entity_id"]
    url = f"{DOMUS_FRONTEND_URL}/api/entities/{entity_id}/schema"

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            url,
            params={"space_id": space_id},
            headers={"Authorization": f"Bearer {DOMUS_SERVICE_TOKEN}"},
            timeout=10.0,
        )

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

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            url,
            params={"space_id": space_id},
            headers={"Authorization": f"Bearer {DOMUS_SERVICE_TOKEN}"},
            json={"tool_name": tool_name, "params": tool_params},
            timeout=10.0,
        )

    if resp.status_code != 200:
        return {"error": "tool_call_failed", "status": resp.status_code, "body": resp.text}
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


async def check_batch_image_quota(
    client, user_id: str, count: int
) -> tuple[bool, str]:
    """Check if user can generate `count` images. Returns (allowed, reason).

    Stub — always allows. Will be wired to usage_events + tier system in Phase 12.
    """
    return True, ""


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------


async def execute_tool(client, name: str, params: dict, space_id: str, user_id: str) -> dict:
    """Dispatch a tool call by name. Returns the tool result or an error dict."""
    tools = {
        "create_entity": create_entity,
        "read_entity": read_entity,
        "query_entities": query_entities,
        "update_entity": update_entity,
        # SPIKE: entity-as-mcp
        "get_entity_schema": get_entity_schema,
        "call_entity_tool": call_entity_tool,
    }
    fn = tools.get(name)
    if fn is None:
        return {"error": "unknown_tool", "tool": name}
    try:
        return await fn(client, space_id, user_id, params)
    except Exception as e:
        return {"error": "tool_execution_failed", "tool": name, "message": str(e)}
