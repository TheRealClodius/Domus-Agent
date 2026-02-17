"""Context builders — entity index and recent turns for the agent system prompt."""


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


# ---------------------------------------------------------------------------
# System prompt assembly (task 2.3)
# ---------------------------------------------------------------------------

# Base instructions for the agent
_BASE_INSTRUCTIONS = """You are Domus, an intelligent spatial assistant. You help users organize their space by creating and managing entities.

You have access to these tools:
- create_entity: Create new entities (notes, calendars, images, etc.)
- update_entity: Update existing entities using JSON Merge Patch
- query_entities: Search and filter entities in the space
- read_entity: Get full details of a specific entity

When creating entities, always provide a clear summary. Use the appropriate entity type and presentation mode.

Entity state shapes:
- A note entity has state: { title: string, content: string }
- A calendar entity has state: { events: [{ title, start, end }] }
- An image entity has state: { generation_prompt: string, image_url: string, width: number, height: number }

When creating images, set type='image' with state.generation_prompt. The system generates the image automatically. Use presentation='card'.

Entities can be presented as: window, card, sidebar, or hidden.
"""


async def build_system_prompt(client, space_id: str, message: str) -> str:
    """Assemble the system prompt for a given space and message.

    Combines:
    1. Base instructions (agent identity, tool descriptions, state shapes)
    2. Entity index from the space
    3. Recent conversation turns
    """
    parts = [_BASE_INSTRUCTIONS.strip()]

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
            "## Current entities in this space:\n" + "\n".join(entity_lines)
        )
    else:
        parts.append("## Current entities in this space:\nNo entities yet.")

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

    return "\n\n".join(parts)
