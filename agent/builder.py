"""Builder agent — background task that constructs declarative apps.

Invoked via builder_loop(supabase, anthropic, entity_id, space_id, spec).
Not yet wired into any agent tool — currently called only in tests and directly.
Runs as an asyncio.create_task(); writes the full app definition to Supabase
in one shot via define_app; CDC pushes the result to the frontend.

Note: build_app in tools.py creates React iframe apps (state._code) — a separate
system. This builder produces declarative view-tree apps (state._def).
"""

import json

import config as cfg
from agent.logging import get_logger
from agent.prompts.builder import build_builder_prompt

logger = get_logger("agent.builder")

# ---------------------------------------------------------------------------
# Builder tool definitions (separate from Domus Agent's tools)
# ---------------------------------------------------------------------------

BUILDER_TOOL_DEFINITIONS = [
    {
        "name": "define_app",
        "description": (
            "Set the complete app definition in one shot. This writes the view tree, "
            "actions, initial state, and summary template to the entity. "
            "The app becomes interactive immediately. Clears the building flag."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "view": {
                    "type": "array",
                    "description": (
                        "Array of ViewNode objects defining the UI tree. "
                        "Each node has: type, id (optional), props (optional), "
                        "bind (optional dot-path into state), action (optional named action), "
                        "children (optional array of child node ids), "
                        "visible (optional dot-path for conditional rendering)."
                    ),
                    "items": {"type": "object"},
                },
                "actions": {
                    "type": "object",
                    "description": (
                        "Map of action_name → ActionDefinition. Each definition has: "
                        "type (set|toggle|increment|append|remove_from_array|toggle_in_array|set_many), "
                        "path, value, clamp, template, key, field, assignments, description."
                    ),
                },
                "state": {
                    "type": "object",
                    "description": "Initial app data (NOT including _def — that's written automatically).",
                },
                "summary_template": {
                    "type": "string",
                    "description": (
                        "Template string with {path} placeholders for generating the entity summary. "
                        "Example: '{name} — {items} items packed'"
                    ),
                },
            },
            "required": ["view", "actions", "state", "summary_template"],
        },
    },
    {
        "name": "finish_build",
        "description": "Mark the app as fully built. Safety net — define_app already clears the building flag.",
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


async def _define_app(
    client, entity_id: str, view: list, actions: dict, state: dict, summary_template: str
) -> dict:
    """Write the full app definition to entity.state and clear building flag."""
    current = await _read_entity_state(client, entity_id)

    # Preserve icon from the initial create_entity call
    icon = current.get("icon", "box")

    # Build the new state: _def holds the definition, rest is app data
    new_state = {
        **state,
        "_def": {
            "view": view,
            "actions": actions,
            "summary_template": summary_template,
            "name": current.get("name", "App"),
            "icon": icon,
        },
        "building": False,
    }

    await _write_entity_state(client, entity_id, new_state)
    return {"ok": True, "component_count": len(view), "action_count": len(actions)}


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
        if tool_name == "define_app":
            return await _define_app(
                client,
                entity_id,
                tool_input["view"],
                tool_input["actions"],
                tool_input["state"],
                tool_input["summary_template"],
            )
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
    """Background task: build a composed app.

    Runs as an asyncio.create_task() — fully autonomous, no SSE streaming.
    The builder generates the full app definition in one define_app call.
    """
    logger.info(
        "builder_start",
        extra={"entity_id": entity_id, "space_id": space_id, "spec": spec[:300]},
    )

    system = build_builder_prompt(spec)
    messages = [{"role": "user", "content": spec}]

    try:
        max_turns = 10  # Safety limit (typically completes in 1-2 turns)
        for turn in range(max_turns):
            logger.info(
                "builder_turn_start",
                extra={"entity_id": entity_id, "turn": turn},
            )
            response = await anthropic_client.messages.create(
                model=cfg.BUILDER_MODEL,
                system=system,
                messages=messages,
                tools=BUILDER_TOOL_DEFINITIONS,
                max_tokens=16384,
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

                tool_detail = {
                    "entity_id": entity_id,
                    "tool": tc.name,
                    "turn": turn,
                    "result_ok": result.get("ok", False),
                }
                if tc.name == "define_app":
                    tool_detail["component_count"] = result.get("component_count", 0)
                    tool_detail["action_count"] = result.get("action_count", 0)
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
