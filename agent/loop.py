"""Agent loop — conversation turn persistence and agentic loop."""

import asyncio
import json
import time

from agent.logging import get_logger, log_tool_execution
from agent.tools import TOOL_DEFINITIONS, execute_tool
from agent.context import build_system_prompt

logger = get_logger("agent.loop")


def _build_multimodal_content(context_items: list[dict], message: str) -> list[dict]:
    """Convert context_items (file attachments) + text message into Claude content blocks."""
    import base64 as b64mod

    blocks = []
    for item in context_items:
        data_url = item.get("data", "")
        name = item.get("name", "attachment")

        if not data_url or ";base64," not in data_url:
            continue

        header, b64_data = data_url.split(";base64,", 1)
        media_type = header.replace("data:", "")

        if media_type.startswith("image/"):
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            })
        elif media_type == "application/pdf":
            blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": b64_data,
                },
            })
        else:
            # Text/CSV/other: decode and include as text
            try:
                text = b64mod.b64decode(b64_data).decode("utf-8")
                blocks.append({"type": "text", "text": f"[File: {name}]\n{text}"})
            except Exception:
                blocks.append({"type": "text", "text": f"[File: {name}] (could not decode)"})

    # Always add the text message last
    blocks.append({"type": "text", "text": message})
    return blocks


async def save_conversation_turn(
    client, space_id: str, user_id: str, role: str, content: str
) -> dict:
    """Save a conversation turn as a hidden entity.

    Creates an entity with:
    - type: 'conversation_turn'
    - presentation: 'hidden'
    - state: {role, content}
    - created_by: 'agent' (the agent service manages all turns)
    """
    row = {
        "space_id": space_id,
        "user_id": user_id,
        "type": "conversation_turn",
        "presentation": "hidden",
        "state": {"role": role, "content": content},
        "created_by": "agent",
    }
    result = await client.table("entities").insert(row).execute()
    return result.data[0] if result.data else row


# ---------------------------------------------------------------------------
# SSE event formatting (task 3.4)
# ---------------------------------------------------------------------------


def format_sse_event(event: dict) -> str:
    """Format an event dict as an SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


# ---------------------------------------------------------------------------
# Agent loop (tasks 3.2 + 3.3)
# ---------------------------------------------------------------------------


async def run_agent(
    client,
    anthropic_client,
    space_id: str,
    user_id: str,
    message: str,
    on_event=None,
    viewport: dict | None = None,
    focused_entity_id: str | None = None,
    visible_entity_ids: list[str] | None = None,
    context_items: list[dict] | None = None,
    user_timezone: str | None = None,
):
    """Run the agent loop. Calls Claude, handles tool calls, streams events.

    Args:
        client: Supabase async client
        anthropic_client: Anthropic AsyncAnthropic client
        space_id: The space to operate in
        user_id: The user making the request
        message: The user's message
        on_event: Async callback for SSE events. Called with event dicts.
        viewport: Current viewport dimensions from frontend
        focused_entity_id: Entity ID the user is focused on
        visible_entity_ids: Entity IDs currently visible on canvas
        context_items: File attachments [{id, name, type, data}] with base64 data URLs
        user_timezone: IANA timezone string (e.g. 'Europe/Bucharest')
    """
    if on_event is None:

        async def on_event(event):
            pass

    logger.info(
        "agent_turn_start",
        extra={"space_id": space_id, "user_id": user_id, "user_timezone": user_timezone},
    )

    # Build system prompt
    system = await build_system_prompt(
        client, space_id, message,
        viewport=viewport,
        focused_entity_id=focused_entity_id,
        visible_entity_ids=visible_entity_ids,
        user_id=user_id,
        user_timezone=user_timezone,
    )

    # Save user turn (text only — don't persist base64 attachments)
    await save_conversation_turn(client, space_id, user_id, "user", message)

    # Build initial messages with attachments
    if context_items:
        user_content = _build_multimodal_content(context_items, message)
    else:
        user_content = message

    messages = [{"role": "user", "content": user_content}]

    assistant_text = ""

    try:
        while True:
            # Call Claude (non-streaming for v0)
            response = await anthropic_client.messages.create(
                model="claude-sonnet-4-5-20250929",
                system=system,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                max_tokens=4096,
            )

            # Process response content blocks
            text_parts = []
            tool_use_blocks = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    await on_event({"type": "text_delta", "content": block.text})
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)

            if text_parts:
                assistant_text += "".join(text_parts)

            # If no tool calls, we're done
            if not tool_use_blocks:
                break

            # Build params list — inject batch positions for group creates
            params_list = [dict(tc.input) for tc in tool_use_blocks]
            create_indices = [
                i for i, tc in enumerate(tool_use_blocks)
                if tc.name == "create_entity" and "position" not in tc.input
            ]
            if len(create_indices) > 1:
                from agent.tools import compute_group_positions
                positions = compute_group_positions(len(create_indices), viewport)
                for idx, pos in zip(create_indices, positions):
                    params_list[idx]["position"] = pos

            # Emit tool_call_start events (with enriched args)
            for tc, params in zip(tool_use_blocks, params_list):
                await on_event({
                    "type": "tool_call_start",
                    "tool": tc.name,
                    "id": tc.id,
                    "args": params,
                })

            # Execute tool calls in parallel
            async def _timed_execute(tc, params):
                t = time.monotonic()
                result = await execute_tool(client, tc.name, params, space_id, user_id)
                ms = (time.monotonic() - t) * 1000
                log_tool_execution(logger, tc.name, ms, space_id=space_id, user_id=user_id)
                return result

            results = await asyncio.gather(
                *[
                    _timed_execute(tc, params)
                    for tc, params in zip(tool_use_blocks, params_list)
                ]
            )

            # Stream tool results
            for tc, result in zip(tool_use_blocks, results):
                await on_event(
                    {"type": "tool_call_result", "id": tc.id, "result": result}
                )

            # Append assistant message and tool results for next turn
            messages.append({"role": "assistant", "content": response.content})
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": json.dumps(r),
                        }
                        for tc, r in zip(tool_use_blocks, results)
                    ],
                }
            )

        # Save assistant turn
        await save_conversation_turn(
            client, space_id, user_id, "assistant", assistant_text
        )
        await on_event({"type": "done"})

    except Exception as e:
        await on_event({"type": "error", "message": str(e)})
        raise

    return assistant_text
