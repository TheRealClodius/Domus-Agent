"""Agent loop — conversation turn persistence and agentic loop."""

import asyncio
import json

from agent.tools import TOOL_DEFINITIONS, execute_tool
from agent.context import build_system_prompt


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
):
    """Run the agent loop. Calls Claude, handles tool calls, streams events.

    Args:
        client: Supabase async client
        anthropic_client: Anthropic AsyncAnthropic client
        space_id: The space to operate in
        user_id: The user making the request
        message: The user's message
        on_event: Async callback for SSE events. Called with event dicts.
    """
    if on_event is None:

        async def on_event(event):
            pass

    # Build system prompt
    system = await build_system_prompt(client, space_id, message)

    # Save user turn
    await save_conversation_turn(client, space_id, user_id, "user", message)

    # Build initial messages
    messages = [{"role": "user", "content": message}]

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
                    await on_event(
                        {
                            "type": "tool_call_start",
                            "tool": block.name,
                            "id": block.id,
                        }
                    )

            if text_parts:
                assistant_text += "".join(text_parts)

            # If no tool calls, we're done
            if not tool_use_blocks:
                break

            # Execute tool calls in parallel
            results = await asyncio.gather(
                *[
                    execute_tool(client, tc.name, tc.input, space_id, user_id)
                    for tc in tool_use_blocks
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
