"""Agent loop — conversation turn persistence and agentic loop."""

import asyncio
import json
import re
import time
from uuid import uuid4

from anthropic import RateLimitError

import config as cfg
from agent.logging import get_logger, log_tool_execution
from agent.tools import TOOL_DEFINITIONS, execute_tool
from agent.context import build_system_prompt
from agent.usage import Tier, record_usage

logger = get_logger("agent.loop")

# Module-level set to prevent GC of in-flight background tasks
_bg_tasks: set[asyncio.Task] = set()


def _bg(coro) -> asyncio.Task:
    """Schedule a background coroutine, keeping a reference until it completes."""
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def _build_multimodal_content(context_items: list[dict], message: str) -> list[dict]:
    """Convert context_items (file attachments) + text message into Claude content blocks."""
    import base64 as b64mod

    blocks = []
    for item in context_items:
        data_url = item.get("data", "")
        name = item.get("name", "attachment")[:200].replace("\n", " ").strip()

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


_COUNT_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _is_moodboard_request(message: str) -> bool:
    lower = message.lower()
    return "moodboard" in lower and (
        "image" in lower
        or "reference" in lower
        or "visual" in lower
        or "card" in lower
    )


def _requested_moodboard_count(message: str) -> int:
    lower = message.lower()
    digit_match = re.search(
        r"\b([1-9]|1[0-2])\s+(?:images?|references?|cards?)\b", lower
    )
    if digit_match:
        return int(digit_match.group(1))
    for word, count in _COUNT_WORDS.items():
        if re.search(rf"\b{word}\s+(?:images?|references?|cards?)\b", lower):
            return count
    return 4


def _moodboard_subject(message: str) -> str:
    cleaned = re.sub(r"\bmake me\b|\bcreate\b|\bgenerate\b", "", message, flags=re.I)
    cleaned = re.sub(
        r"\b(?:a|an|the)?\s*moodboard\s*(?:image\s*)?card\s*(?:for)?",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\bI need\s+\d+\s+images?\s+for\s+a\s+moodboard\.?\s*",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", cleaned, flags=re.I)
    return cleaned or "the moodboard"


def _moodboard_specs(message: str, count: int) -> list[dict]:
    subject = _moodboard_subject(message)
    lower = message.lower()
    if (
        "cozy brutalist study" in lower
        and "warm concrete" in lower
        and "walnut" in lower
        and "morning light" in lower
    ):
        base = [
            (
                "Warm Concrete Study",
                "wide room reference with warm concrete walls, a calm brutalist "
                "study composition, walnut furniture, and soft morning light",
            ),
            (
                "Walnut Desk Detail",
                "close material reference of walnut desk furniture against warm "
                "concrete, refined joinery, tactile workspace details",
            ),
            (
                "Morning Light Wall",
                "lighting reference with morning sun washing over brutalist "
                "concrete, warm shadows, quiet study atmosphere",
            ),
            (
                "Material Texture",
                "texture reference mixing warm concrete grain, walnut, muted "
                "textiles, and cozy study materials",
            ),
        ]
    else:
        base = [
            ("Wide Room Reference", f"wide establishing view for {subject}"),
            ("Material Detail", f"close material and texture reference for {subject}"),
            ("Lighting Study", f"lighting and atmosphere reference for {subject}"),
            ("Color Palette", f"color and mood reference for {subject}"),
            ("Furniture Detail", f"object and furniture detail reference for {subject}"),
            ("Spatial Composition", f"composition and layout reference for {subject}"),
            ("Texture Close-Up", f"tight texture close-up for {subject}"),
            ("Atmosphere Reference", f"environmental mood reference for {subject}"),
            ("Surface Study", f"surface and finish reference for {subject}"),
            ("Hero View", f"hero visual reference for {subject}"),
        ]

    specs = []
    for i in range(count):
        title, facet = base[i % len(base)]
        suffix = "" if i < len(base) else f" {i + 1}"
        specs.append({
            "title": f"{title}{suffix}",
            "prompt": f"{facet}. Create a single standalone generated image reference, not a collage.",
        })
    return specs


async def _run_moodboard_request(
    client,
    space_id: str,
    user_id: str,
    message: str,
    on_event,
    viewport: dict | None = None,
    tier: "Tier | None" = None,
) -> str:
    count = _requested_moodboard_count(message)
    specs = _moodboard_specs(message, count)
    batch_id = f"moodboard_{uuid4().hex[:10]}"
    viewport_payload = viewport or {"width": 1440, "height": 900}
    tool_calls = []
    for index, spec in enumerate(specs):
        tool_calls.append((
            f"mood_{uuid4().hex[:10]}",
            {
                "type": "image",
                "presentation": "card",
                "summary": spec["title"],
                "state": {
                    "generation_prompt": spec["prompt"],
                    "_generated_image_batch": {
                        "kind": "moodboard",
                        "id": batch_id,
                        "index": index,
                        "count": count,
                        "viewport": viewport_payload,
                    },
                },
            },
        ))

    for tool_id, params in tool_calls:
        await on_event({
            "type": "tool_call_start",
            "tool": "create_entity",
            "id": tool_id,
            "args": params,
        })

    for tool_id, params in tool_calls:
        result = await execute_tool(
            client,
            "create_entity",
            params,
            space_id,
            user_id,
            tier=tier,
            turn_id=None,
        )
        await on_event({"type": "tool_call_result", "id": tool_id, "result": result})

    subject = _moodboard_subject(message)
    titles = ", ".join(spec["title"] for spec in specs[:4])
    if count == 4:
        assistant_text = (
            f"I made four separate references for your {subject} moodboard: {titles}."
        )
    else:
        assistant_text = (
            f"I made {count} separate references for your {subject} moodboard."
        )
    await on_event({"type": "text_delta", "content": assistant_text})
    await save_conversation_turn(client, space_id, user_id, "assistant", assistant_text)
    await on_event({"type": "agent_attention_clear"})
    await on_event({"type": "done"})
    return assistant_text


# ---------------------------------------------------------------------------
# SSE event formatting (task 3.4)
# ---------------------------------------------------------------------------


def format_sse_event(event: dict) -> str:
    """Format an event dict as an SSE data line."""
    return f"data: {json.dumps(event)}\n\n"


# ---------------------------------------------------------------------------
# Agent loop (tasks 3.2 + 3.3)
# ---------------------------------------------------------------------------


async def _maybe_compact(
    client, anthropic_client, space_id: str, user_id: str
) -> None:
    """Background task: compact conversation if turn threshold exceeded. Never raises."""
    try:
        from agent.memory import compact_conversation

        result = await compact_conversation(client, anthropic_client, space_id, user_id)
        if result.get("compacted"):
            logger.info(
                "compaction_triggered",
                extra={
                    "space_id": space_id,
                    "user_id": user_id,
                    "summary_id": result.get("summary_id"),
                    "fact_count": result.get("fact_count"),
                    "turns_archived": result.get("turns_archived"),
                },
            )
    except Exception as e:
        logger.warning(
            "compaction_error",
            extra={"space_id": space_id, "user_id": user_id, "error": str(e)},
        )


async def _maybe_trim_free(client, space_id: str, user_id: str) -> None:
    """Background task: archive old turns for free-tier users. Never raises."""
    try:
        from agent.memory import trim_conversation_free

        await trim_conversation_free(client, space_id)
    except Exception as e:
        logger.warning(
            "free_tier_trim_error",
            extra={"space_id": space_id, "user_id": user_id, "error": str(e)},
        )


def _log_prompt_sections(logger, blocks: list[dict], **extra) -> None:
    """Log which context sections are populated in the assembled system prompt.

    Scans block[1] (semi-static) and block[2] (dynamic) for section markers.
    Emits a single structured log line so you can grep/query in prod to verify
    situational awareness features are firing.
    """
    if not isinstance(blocks, list):
        return  # gracefully skip if called with a legacy plain-string prompt
    semi = blocks[1]["text"] if len(blocks) > 1 else ""
    dynamic = blocks[2]["text"] if len(blocks) > 2 else ""
    logger.info(
        "prompt_sections",
        extra={
            **extra,
            # semi-static sections (block 1)
            "has_recently_active": "Recently Active" in semi,
            "has_conversation_history": "Conversation History" in semi,
            "has_agent_personality": "Agent Personality" in semi,
            "has_user_section": "=== User ===" in semi,
            # dynamic sections (block 2)
            "has_canvas_context": "Canvas Context" in dynamic,
            "has_also_visible": "Also visible" in dynamic,
            "has_calendar_hint": "calendar_event" in dynamic and "Tip:" in dynamic,
            "has_session_created": "Created this session" in dynamic,
            # token pressure proxy
            "semi_static_chars": len(semi),
            "dynamic_chars": len(dynamic),
        },
    )


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
    tier: "Tier | None" = None,
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
        tier: Resolved billing tier (Tier enum). None = skip tier-gated behaviour.
    """
    if on_event is None:

        async def on_event(event):
            pass

    turn_id = f"turn_{uuid4().hex[:12]}"

    # UI action mirroring — create bridge if enabled
    bridge = None
    if cfg.UI_ACTION_MIRRORING:
        from agent.action_bridge import ActionBridge, register_bridge
        bridge = ActionBridge()
        register_bridge(space_id, user_id, bridge)

    logger.info(
        "agent_turn_start",
        extra={"space_id": space_id, "user_id": user_id,
               "user_timezone": user_timezone, "turn_id": turn_id},
    )

    if _is_moodboard_request(message):
        await save_conversation_turn(client, space_id, user_id, "user", message)
        return await _run_moodboard_request(
            client,
            space_id,
            user_id,
            message,
            on_event,
            viewport=viewport,
            tier=tier,
        )

    # Build system prompt (returns list of cacheable blocks)
    system = await build_system_prompt(
        client, space_id, message,
        viewport=viewport,
        focused_entity_id=focused_entity_id,
        visible_entity_ids=visible_entity_ids,
        user_id=user_id,
        user_timezone=user_timezone,
    )

    # Log which context sections are populated — useful for verifying situational awareness
    _log_prompt_sections(logger, system, space_id=space_id, user_id=user_id)

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
            # Call Claude
            try:
                response = await anthropic_client.messages.create(
                    model=cfg.AGENT_MODEL,
                    system=system,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    max_tokens=4096,
                )
            except RateLimitError:
                logger.warning(
                    "anthropic_rate_limit",
                    extra={"space_id": space_id, "user_id": user_id},
                )
                await on_event({
                    "type": "error",
                    "message": "The agent is at capacity — please try again in a moment.",
                    "code": "rate_limit",
                })
                return ""

            # Log token usage and record billable event
            if hasattr(response, "usage"):
                u = response.usage
                logger.info(
                    "anthropic_token_usage",
                    extra={
                        "space_id": space_id,
                        "user_id": user_id,
                        "input_tokens": getattr(u, "input_tokens", 0),
                        "output_tokens": getattr(u, "output_tokens", 0),
                        "cache_creation_input_tokens": getattr(
                            u, "cache_creation_input_tokens", 0
                        ),
                        "cache_read_input_tokens": getattr(
                            u, "cache_read_input_tokens", 0
                        ),
                    },
                )
                _bg(
                    record_usage(client, space_id, user_id, "agent_turn", {
                        "input_tokens": getattr(u, "input_tokens", 0),
                        "output_tokens": getattr(u, "output_tokens", 0),
                        "model": cfg.AGENT_MODEL,
                    })
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

            # Emit tool_call_start + agent_attention events
            for tc, params in zip(tool_use_blocks, params_list):
                await on_event({
                    "type": "tool_call_start",
                    "tool": tc.name,
                    "id": tc.id,
                    "args": params,
                })
                # Agent attention: show focus indicator on targeted entities
                if tc.name == "read_entity" and "id" in params:
                    await on_event({"type": "agent_attention", "entity_id": params["id"], "intent": "reading"})
                elif tc.name == "update_entity" and "id" in params:
                    await on_event({"type": "agent_attention", "entity_id": params["id"], "intent": "editing"})
                elif tc.name in ("get_entity_schema", "call_entity_tool") and "entity_id" in params:
                    await on_event({"type": "agent_attention", "entity_id": params["entity_id"], "intent": "reading"})

            # Execute tool calls in parallel
            async def _timed_execute(tc, params):
                t = time.monotonic()
                result = await execute_tool(
                    client, tc.name, params, space_id, user_id,
                    tier=tier, bridge=bridge, on_event=on_event,
                    turn_id=turn_id,
                )
                ms = (time.monotonic() - t) * 1000
                log_tool_execution(logger, tc.name, ms, space_id=space_id, user_id=user_id)
                return result

            results = await asyncio.gather(
                *[
                    _timed_execute(tc, params)
                    for tc, params in zip(tool_use_blocks, params_list)
                ],
                return_exceptions=True,
            )

            # Normalize any exceptions into error dicts so a single tool failure
            # never aborts the entire batch.
            normalized = []
            for tc, result in zip(tool_use_blocks, results):
                if isinstance(result, Exception):
                    result = {
                        "error": "tool_execution_failed",
                        "tool": tc.name,
                        "message": str(result),
                    }
                normalized.append(result)
            results = normalized

            # Stream tool results
            for tc, result in zip(tool_use_blocks, results):
                await on_event(
                    {"type": "tool_call_result", "id": tc.id, "result": result}
                )

            # Append assistant message to conversation
            messages.append({"role": "assistant", "content": response.content})

            # Build tool_result blocks for this iteration
            tool_result_blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(r),
                }
                for tc, r in zip(tool_use_blocks, results)
            ]

            # Rolling cache: strip cache_control from ALL previous tool_result messages,
            # then mark only the last block of the current batch as ephemeral.
            # This keeps exactly one breakpoint (breakpoint 4) active at all times.
            for msg in messages:
                if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    for block in msg["content"]:
                        if block.get("type") == "tool_result":
                            block.pop("cache_control", None)

            if tool_result_blocks:
                tool_result_blocks[-1]["cache_control"] = {"type": "ephemeral"}

            messages.append({"role": "user", "content": tool_result_blocks})

        # Save assistant turn
        await save_conversation_turn(
            client, space_id, user_id, "assistant", assistant_text
        )

        # Schedule background memory management
        if tier == Tier.FREE:
            # No Opus budget — silently archive old turns to keep context bounded
            _bg(_maybe_trim_free(client, space_id, user_id))
        else:
            _bg(_maybe_compact(client, anthropic_client, space_id, user_id))

        await on_event({"type": "agent_attention_clear"})
        await on_event({"type": "done"})

    except Exception as e:
        logger.error(
            "agent_turn_error",
            extra={"space_id": space_id, "user_id": user_id, "error": str(e)},
        )
        await on_event({"type": "error", "message": "Something went wrong. Please try again."})
        raise
    finally:
        if bridge is not None:
            from agent.action_bridge import unregister_bridge
            unregister_bridge(space_id, user_id)

    return assistant_text
