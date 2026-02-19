"""Builder agent — background task that constructs composed apps block by block.

Launched by the main Domus Agent via build_app tool. Runs as an asyncio task
on the same service. Writes blocks to Supabase one by one; CDC pushes each
block to the frontend in real-time.
"""

import json

from agent.logging import get_logger
from agent.prompts.builder import build_builder_prompt

logger = get_logger("agent.builder")

# ---------------------------------------------------------------------------
# Builder tool definitions (separate from Domus Agent's tools)
# ---------------------------------------------------------------------------

BUILDER_TOOL_DEFINITIONS = [
    {
        "name": "add_block",
        "description": (
            "Add a UI block to the app. Each block appears on the user's screen "
            "immediately. Blocks are appended in order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "block": {
                    "type": "object",
                    "description": (
                        "The block to add. Must include type, id, and type-specific fields. "
                        "Types: heading, text, checklist, key-value, table, metric, progress, divider, list."
                    ),
                },
            },
            "required": ["block"],
        },
    },
    {
        "name": "update_block",
        "description": "Update an existing block by id. Merges the patch into the block.",
        "input_schema": {
            "type": "object",
            "properties": {
                "block_id": {
                    "type": "string",
                    "description": "The id of the block to update",
                },
                "patch": {
                    "type": "object",
                    "description": "Fields to merge into the block",
                },
            },
            "required": ["block_id", "patch"],
        },
    },
    {
        "name": "set_tools_schema",
        "description": (
            "Define the tools that the main Domus Agent can use to interact with "
            "this app after it's built. Each tool maps to a block mutation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tools": {
                    "type": "array",
                    "description": "Array of MCP tool schemas",
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
            },
            "required": ["tools"],
        },
    },
    {
        "name": "finish_build",
        "description": "Mark the app as fully built. Removes the building indicator.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ---------------------------------------------------------------------------
# Builder tool implementations
# ---------------------------------------------------------------------------


async def _read_entity_state(client, entity_id: str) -> dict:
    """Read current entity state from Supabase."""
    result = await (
        client.table("entities")
        .select("state")
        .eq("id", entity_id)
        .maybe_single()
        .execute()
    )
    if not result.data:
        return {}
    return result.data.get("state", {}) or {}


async def _write_entity_state(client, entity_id: str, state: dict) -> None:
    """Write entity state to Supabase."""
    await (
        client.table("entities")
        .update({"state": state})
        .eq("id", entity_id)
        .execute()
    )


async def _add_block(client, entity_id: str, block: dict) -> dict:
    """Append a block to entity.state.blocks."""
    state = await _read_entity_state(client, entity_id)
    blocks = list(state.get("blocks", []))
    blocks.append(block)
    state["blocks"] = blocks
    await _write_entity_state(client, entity_id, state)
    return {"ok": True, "block_count": len(blocks)}


async def _update_block(client, entity_id: str, block_id: str, patch: dict) -> dict:
    """Update an existing block by merging patch."""
    state = await _read_entity_state(client, entity_id)
    blocks = list(state.get("blocks", []))
    found = False
    for i, b in enumerate(blocks):
        if b.get("id") == block_id:
            blocks[i] = {**b, **patch}
            found = True
            break
    if not found:
        return {"ok": False, "error": f"Block {block_id} not found"}
    state["blocks"] = blocks
    await _write_entity_state(client, entity_id, state)
    return {"ok": True}


async def _set_tools_schema(client, entity_id: str, tools: list[dict]) -> dict:
    """Write the tools schema to entity.state.tools."""
    state = await _read_entity_state(client, entity_id)
    state["tools"] = tools
    await _write_entity_state(client, entity_id, state)
    return {"ok": True, "tool_count": len(tools)}


async def _finish_build(client, entity_id: str) -> dict:
    """Clear the building flag."""
    state = await _read_entity_state(client, entity_id)
    state["building"] = False
    await _write_entity_state(client, entity_id, state)
    return {"ok": True}


async def execute_builder_tool(
    client, entity_id: str, space_id: str, tool_name: str, tool_input: dict
) -> dict:
    """Dispatch a builder tool call."""
    try:
        if tool_name == "add_block":
            return await _add_block(client, entity_id, tool_input["block"])
        elif tool_name == "update_block":
            return await _update_block(
                client, entity_id, tool_input["block_id"], tool_input["patch"]
            )
        elif tool_name == "set_tools_schema":
            return await _set_tools_schema(client, entity_id, tool_input["tools"])
        elif tool_name == "finish_build":
            return await _finish_build(client, entity_id)
        else:
            return {"ok": False, "error": f"Unknown builder tool: {tool_name}"}
    except KeyError as e:
        return {"ok": False, "error": f"Missing required field: {e}"}


# ---------------------------------------------------------------------------
# Builder loop — the background agent
# ---------------------------------------------------------------------------


async def builder_loop(
    supabase_client,
    anthropic_client,
    entity_id: str,
    space_id: str,
    spec: str,
):
    """Background task: build a composed app block by block.

    Runs as an asyncio.create_task() — fully autonomous, no SSE streaming.
    Each tool call writes to Supabase; CDC pushes changes to frontend.
    """
    logger.info(
        "builder_start",
        extra={"entity_id": entity_id, "space_id": space_id, "spec": spec[:300]},
    )

    system = build_builder_prompt(spec)
    messages = [{"role": "user", "content": spec}]

    try:
        max_turns = 20  # Safety limit
        for turn in range(max_turns):
            logger.info(
                "builder_turn_start",
                extra={"entity_id": entity_id, "turn": turn},
            )
            response = await anthropic_client.messages.create(
                model="claude-sonnet-4-5-20250929",
                system=system,
                messages=messages,
                tools=BUILDER_TOOL_DEFINITIONS,
                max_tokens=4096,
            )

            # Log any text the model produced (thinking/planning)
            text_blocks = [b for b in response.content if b.type == "text"]
            if text_blocks:
                text = " ".join(b.text for b in text_blocks)
                logger.info(
                    "builder_model_text",
                    extra={"entity_id": entity_id, "turn": turn, "text": text[:500]},
                )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                logger.info(
                    "builder_auto_finish",
                    extra={
                        "entity_id": entity_id,
                        "turn": turn,
                        "stop_reason": response.stop_reason,
                    },
                )
                # No more tool calls — auto-finish if not already done
                await _finish_build(supabase_client, entity_id)
                break

            results = []
            for tc in tool_use_blocks:
                result = await execute_builder_tool(
                    supabase_client, entity_id, space_id, tc.name, tc.input
                )
                results.append(result)

                # Detailed tool call log
                tool_detail = {"entity_id": entity_id, "tool": tc.name, "turn": turn, "result_ok": result.get("ok", False)}
                if tc.name == "add_block":
                    block = tc.input.get("block", {})
                    tool_detail["block_type"] = block.get("type")
                    tool_detail["block_id"] = block.get("id")
                elif tc.name == "update_block":
                    tool_detail["block_id"] = tc.input.get("block_id")
                elif tc.name == "set_tools_schema":
                    tool_detail["tool_count"] = len(tc.input.get("tools", []))
                if not result.get("ok"):
                    tool_detail["error"] = result.get("error", "")[:200]
                logger.info("builder_tool_call", extra=tool_detail)

            # Append to messages for multi-turn
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": json.dumps(r),
                    }
                    for tc, r in zip(tool_use_blocks, results)
                ],
            })

        logger.info(
            "builder_done",
            extra={"entity_id": entity_id, "space_id": space_id},
        )

    except Exception as e:
        logger.error(
            "builder_error",
            extra={
                "entity_id": entity_id,
                "space_id": space_id,
                "error": str(e)[:500],
            },
        )
        # Mark build as failed — clear building flag so UI doesn't hang
        try:
            state = await _read_entity_state(supabase_client, entity_id)
            state["building"] = False
            state["build_error"] = str(e)[:200]
            await _write_entity_state(supabase_client, entity_id, state)
        except Exception:
            pass
