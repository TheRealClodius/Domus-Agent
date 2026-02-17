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
                    "enum": ["window", "card", "sidebar", "hidden"],
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
                    "enum": ["window", "card", "sidebar", "hidden"],
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
                    "description": "Full-text search query across content and summary",
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
    result = await client.table("entities").insert(row).execute()
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
# Tool dispatcher
# ---------------------------------------------------------------------------


async def execute_tool(client, name: str, params: dict, space_id: str, user_id: str) -> dict:
    """Dispatch a tool call by name. Returns the tool result or an error dict."""
    tools = {
        "create_entity": create_entity,
        "read_entity": read_entity,
        "query_entities": query_entities,
        "update_entity": update_entity,
    }
    fn = tools.get(name)
    if fn is None:
        return {"error": "unknown_tool", "tool": name}
    try:
        return await fn(client, space_id, user_id, params)
    except Exception as e:
        return {"error": "tool_execution_failed", "tool": name, "message": str(e)}
