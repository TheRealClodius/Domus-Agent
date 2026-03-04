"""FastAPI app — Domus Agent service entry point."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config as cfg
from agent.logging import setup_logging, get_logger
from agent.loop import run_agent, format_sse_event
from agent.usage import check_quota, check_rate_limit, get_user_tier, acquire_turn_slot, release_turn_slot
from agent.context import (
    build_system_prompt,
    get_entity_index,
    get_recent_turns,
    get_space_info,
    get_user_profile,
    get_conversation_summaries,
    get_personality_traits,
    get_user_facts,
    SESSION_WINDOW_MINUTES,
    _BASE_INSTRUCTIONS,
    _build_semi_static_block,
    _cache_invalidate,
)
from agent.prompts.iframe_builder import IFRAME_BUILDER_CONTEXT
from agent.prompts.builder import (
    COMPONENT_CATALOG,
    ACTION_DSL,
    APP_EXAMPLES,
    DESIGN_CONTEXT,
    build_builder_prompt,
)

setup_logging()
logger = get_logger("main")

# Keep these imports accessible for existing tests that patch them at module level
from config import acreate_client, create_anthropic_client  # noqa: F401


# ---------------------------------------------------------------------------
# Lifespan — create shared clients once at startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared clients on startup; clean up on shutdown."""
    supabase = await cfg.acreate_client()
    anthropic = cfg.create_anthropic_client()
    cfg.set_shared_clients(anthropic, supabase)
    app.state.supabase = supabase
    app.state.anthropic = anthropic
    logger.info("shared_clients_initialized")
    yield
    # Graceful shutdown
    for closer in [
        getattr(supabase, "aclose", None),
        getattr(anthropic, "close", None),
    ]:
        if callable(closer):
            try:
                result = closer()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass


app = FastAPI(title="Domus Agent", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS — allow the Next.js frontend (local dev and Vercel deployments)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(http://localhost:3000|https://.*\.vercel\.app)$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Service auth dependency
# ---------------------------------------------------------------------------


async def verify_service_auth(request: Request) -> None:
    """Validate the shared service token from the Vercel proxy.

    Applied as a FastAPI dependency on protected routes (not /health).
    """
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing service token")
    token = auth.removeprefix("Bearer ")
    if token != cfg.DOMUS_SERVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid service token")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    """Liveness probe — no auth required."""
    return {"status": "ok"}


@app.get("/protected-test", dependencies=[Depends(verify_service_auth)])
async def protected_test():
    """Temporary route used by tests to exercise the auth dependency."""
    return {"ok": True}


# ---------------------------------------------------------------------------
# Agent endpoint
# ---------------------------------------------------------------------------


class AgentRequest(BaseModel):
    space_id: str
    message: str
    user_id: str
    viewport: dict | None = None
    focused_entity_id: str | None = None
    visible_entity_ids: list[str] | None = None
    context_items: list[dict] | None = None
    user_timezone: str | None = None


@app.post("/debug/prompt", dependencies=[Depends(verify_service_auth)])
async def debug_prompt(req: AgentRequest, request: Request):
    """Return the assembled system prompt blocks as JSON — for local inspection only.

    Requires DEBUG_PROMPT_ENABLED=true in the environment. Disabled in production.
    Protected by service auth so it can't be called without the shared token.
    """
    if not cfg.DEBUG_PROMPT_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")

    supabase = request.app.state.supabase
    blocks = await build_system_prompt(
        supabase, req.space_id, req.message,
        viewport=req.viewport,
        focused_entity_id=req.focused_entity_id,
        visible_entity_ids=req.visible_entity_ids,
        user_id=req.user_id,
        user_timezone=req.user_timezone,
    )

    _LABELS = ["static", "semi_static", "dynamic"]
    _CHECKS = {
        "recently_active": "Recently Active",
        "conversation_history": "Conversation History",
        "agent_personality": "Agent Personality",
        "user_section": "=== User ===",
        "canvas_context": "Canvas Context",
        "also_visible": "Also visible",
        "calendar_hint": "Tip:",
        "session_created": "Created this session",
    }
    full_text = "\n\n".join(b["text"] for b in blocks)
    return JSONResponse({
        "blocks": [
            {
                "index": i,
                "label": _LABELS[i] if i < len(_LABELS) else f"block_{i}",
                "chars": len(b["text"]),
                "cached": "cache_control" in b,
                "text": b["text"],
            }
            for i, b in enumerate(blocks)
        ],
        "sections_present": {
            name: marker in full_text
            for name, marker in _CHECKS.items()
        },
        "total_chars": len(full_text),
    })


@app.post("/agent", dependencies=[Depends(verify_service_auth)])
async def agent_endpoint(req: AgentRequest, request: Request):
    """Accept a user message and stream SSE events from the agent loop."""
    # Read shared clients from app.state (set once during lifespan startup)
    supabase = request.app.state.supabase
    anthropic = request.app.state.anthropic

    # Resolve tier and enforce rate/quota limits before streaming
    tier = await get_user_tier(supabase, req.user_id)

    rate_allowed, retry_after = await check_rate_limit(req.user_id, tier)
    if not rate_allowed:
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    quota = await check_quota(supabase, req.user_id, tier, "agent_turn")
    if not quota["allowed"]:
        return JSONResponse(
            status_code=429,
            content={"error": "quota_exhausted", "remaining": 0, "resets_at": quota["resets_at"]},
        )

    slot_acquired = await acquire_turn_slot(req.user_id, tier)
    if not slot_acquired:
        return JSONResponse(
            status_code=429,
            content={"error": "concurrent_limit"},
        )

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    async def on_event(event: dict):
        await queue.put(event)

    async def generate():
        task = asyncio.create_task(
            run_agent(
                supabase,
                anthropic,
                space_id=req.space_id,
                user_id=req.user_id,
                message=req.message,
                on_event=on_event,
                viewport=req.viewport,
                focused_entity_id=req.focused_entity_id,
                visible_entity_ids=req.visible_entity_ids,
                context_items=req.context_items,
                user_timezone=req.user_timezone,
                tier=tier,
            )
        )
        terminal_event_sent = False
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                    yield format_sse_event(event)
                    if event.get("type") in ("done", "error"):
                        terminal_event_sent = True
                        # Await the task to drain its exception so asyncio doesn't
                        # emit "Task exception was never retrieved" warnings.
                        try:
                            await task
                        except Exception:
                            pass
                        break
                except asyncio.TimeoutError:
                    if task.done():
                        # Drain remaining events
                        while not queue.empty():
                            event = queue.get_nowait()
                            yield format_sse_event(event)
                            if event.get("type") in ("done", "error"):
                                terminal_event_sent = True

                        if not terminal_event_sent:
                            exc = task.exception()
                            if exc is not None:
                                yield format_sse_event(
                                    {"type": "error", "message": str(exc)}
                                )
                            else:
                                yield format_sse_event({"type": "done"})
                        break
        finally:
            await release_turn_slot(req.user_id)
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(generate(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# UI Action Mirroring — callback endpoint
# ---------------------------------------------------------------------------


class ActionResultRequest(BaseModel):
    action_id: str
    space_id: str
    user_id: str
    success: bool
    result: dict | None = None
    error: str | None = None


@app.post("/agent/action-result", dependencies=[Depends(verify_service_auth)])
async def action_result(req: ActionResultRequest):
    """Receive the result of a ui_action from the frontend.

    The frontend executes the action through its UI state machine and POSTs
    the result here. This resolves the pending Future in the ActionBridge,
    allowing the agent loop to continue.
    """
    from agent.action_bridge import get_bridge

    bridge = get_bridge(req.space_id, req.user_id)
    if bridge is None:
        raise HTTPException(status_code=404, detail="No active agent turn")

    resolved = bridge.resolve(req.action_id, {
        "success": req.success,
        "result": req.result,
        "error": req.error,
    })
    if not resolved:
        raise HTTPException(status_code=404, detail="Unknown or expired action_id")

    logger.info(
        "ui_action_callback_received",
        extra={"action_id": req.action_id, "space_id": req.space_id,
               "success": req.success},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin observability endpoints
# ---------------------------------------------------------------------------


@app.get("/admin/domus-context", dependencies=[Depends(verify_service_auth)])
async def admin_domus_context(space_id: str, user_id: str, request: Request):
    """Snapshot the Domus agent's assembled context — blocks, entity index, health."""
    supabase = request.app.state.supabase
    now_utc = datetime.now(timezone.utc)

    # Always show live Supabase state — bypass the entity index cache.
    await _cache_invalidate(f"entity_index:{space_id}")

    async def _count_type(type_: str) -> list:
        result = await (
            supabase.table("entities")
            .select("id")
            .eq("space_id", space_id)
            .eq("type", type_)
            .eq("archived", False)
            .execute()
        )
        return result.data or []

    async def _recent_events() -> list:
        result = await (
            supabase.table("usage_events")
            .select("event_type, created_at, metadata")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
        return result.data or []

    (
        entities, turns, space, user, summaries, traits, user_facts,
        turn_rows, summary_rows, fact_rows, edge_rows, recent_events,
    ) = await asyncio.gather(
        get_entity_index(supabase, space_id),
        get_recent_turns(supabase, space_id),
        get_space_info(supabase, space_id),
        get_user_profile(supabase, user_id),
        get_conversation_summaries(supabase, space_id),
        get_personality_traits(supabase, space_id),
        get_user_facts(supabase, space_id),
        _count_type("conversation_turn"),
        _count_type("conversation_summary"),
        _count_type("fact"),
        _count_type("edge"),
        _recent_events(),
    )

    entities = entities or []
    turns = turns or []
    summaries = summaries or []
    traits = traits or []
    user_facts = user_facts or []

    # Block 0: static — base instructions + iframe builder context
    static_text = _BASE_INSTRUCTIONS.strip() + "\n\n" + IFRAME_BUILDER_CONTEXT.strip()
    static_chars = len(static_text)

    # Block 1: semi-static — assembled from fetched data
    semi_static_text = _build_semi_static_block(
        space, user, entities,
        summaries=summaries,
        traits=traits,
        user_facts=user_facts,
    )
    semi_static_chars = len(semi_static_text)

    # Session-created count (entities created within the session window)
    session_cutoff = now_utc - timedelta(minutes=SESSION_WINDOW_MINUTES)
    session_created_count = sum(
        1 for e in entities
        if e.get("created_at") and
        datetime.fromisoformat(e["created_at"].replace("Z", "+00:00")) >= session_cutoff
    )

    # Entity index by type
    by_type: dict[str, int] = {}
    for e in entities:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1

    # Health counters
    active_turn_count = len(turn_rows)
    summary_count = len(summary_rows)
    fact_count = len(fact_rows)
    edge_count = len(edge_rows)
    compaction_needed = active_turn_count > cfg.COMPACTION_TURN_THRESHOLD

    total_chars = static_chars + semi_static_chars
    return JSONResponse({
        "agent": "domus",
        "space_id": space_id,
        "user_id": user_id,
        "assembled_at": now_utc.isoformat(),
        "prompt_structure": {
            "total_chars": total_chars,
            "token_estimate": total_chars // 4,
            "model": cfg.AGENT_MODEL,
            "blocks": {
                "static":      {"chars": static_chars,      "token_estimate": static_chars // 4,      "cached": True},
                "semi_static": {"chars": semi_static_chars, "token_estimate": semi_static_chars // 4, "cached": True},
                "dynamic":     {"chars": 0,                 "token_estimate": 0,                      "cached": False},
            },
        },
        "blocks": {
            "static": {
                "chars": static_chars,
                "token_estimate": static_chars // 4,
                "cached": True,
                "sections": {
                    "base_instructions": {"chars": len(_BASE_INSTRUCTIONS)},
                    "iframe_builder_context": {"chars": len(IFRAME_BUILDER_CONTEXT)},
                },
            },
            "semi_static": {
                "chars": semi_static_chars,
                "token_estimate": semi_static_chars // 4,
                "cached": True,
                "space": {"name": space.get("name") if space else None},
                "user": {
                    "name": user.get("name") if user else None,
                    "username": user.get("username") if user else None,
                },
                "user_facts": user_facts,
                "conversation_summaries": summaries,
                "personality_traits": traits,
                "recently_active": sorted(
                    entities, key=lambda e: e.get("updated_at") or "", reverse=True
                )[:3],
                "entity_index": {
                    "total": len(entities),
                    "by_type": by_type,
                    "entities": entities,
                },
            },
            "dynamic": {
                "token_estimate": 0,
                "cached": False,
                "note": (
                    "Dynamic block is assembled per-request from session data "
                    "(viewport, focused_entity, visible_entity_ids) sent by the frontend. "
                    "Showing session-agnostic fields only."
                ),
                "session_window_minutes": SESSION_WINDOW_MINUTES,
                "session_created_count": session_created_count,
                "recent_turns": [
                    {
                        "role": t.get("state", {}).get("role"),
                        "content": t.get("state", {}).get("content"),
                        "created_at": t.get("created_at"),
                    }
                    for t in turns
                ],
            },
        },
        "health": {
            "active_turn_count": active_turn_count,
            "summary_count": summary_count,
            "fact_count": fact_count,
            "edge_count": edge_count,
            "compaction_threshold": cfg.COMPACTION_TURN_THRESHOLD,
            "compaction_needed": compaction_needed,
            "recent_events": [
                {
                    "event_type": e["event_type"],
                    "created_at": e["created_at"],
                    "metadata": e.get("metadata") or {},
                }
                for e in recent_events
            ],
        },
    })


@app.get("/admin/builder-context", dependencies=[Depends(verify_service_auth)])
async def admin_builder_context(space_id: str, user_id: str, request: Request):
    """Snapshot the Builder agent's prompt structure and recent declarative/iframe apps."""
    supabase = request.app.state.supabase
    now_utc = datetime.now(timezone.utc)

    # Query app entities for this user's space
    result = await (
        supabase.table("entities")
        .select("id, state, summary, updated_at, created_at")
        .eq("space_id", space_id)
        .eq("user_id", user_id)
        .eq("type", "app")
        .eq("archived", False)
        .order("updated_at", desc=True)
        .execute()
    )
    apps = result.data or []

    # Compute prompt section sizes from module-level constants
    component_catalog_chars = len(COMPONENT_CATALOG)
    action_dsl_chars = len(ACTION_DSL)
    app_examples_chars = len(APP_EXAMPLES)
    design_context_chars = len(DESIGN_CONTEXT)
    example_prompt = build_builder_prompt("a sample app")
    total_chars = len(example_prompt)
    role_process_chars = (
        total_chars
        - component_catalog_chars
        - action_dsl_chars
        - app_examples_chars
        - design_context_chars
    )

    # Categorize apps by state structure
    declarative_apps = []
    iframe_apps = []
    for app_entity in apps:
        state = app_entity.get("state") or {}
        meta = state.get("_meta") or {}
        if "_def" in state:
            def_obj = state["_def"]
            view = def_obj.get("view", []) if isinstance(def_obj, dict) else []
            actions = def_obj.get("actions", {}) if isinstance(def_obj, dict) else {}
            declarative_apps.append({
                "entity_id": app_entity["id"],
                "name": meta.get("name") or app_entity.get("summary", ""),
                "summary": app_entity.get("summary", ""),
                "description": meta.get("description", ""),
                "component_count": len(view),
                "action_count": len(actions),
                "building": state.get("building", False),
                "has_error": bool(state.get("error")),
                "updated_at": app_entity.get("updated_at"),
            })
        elif "_code" in state:
            iframe_apps.append({
                "entity_id": app_entity["id"],
                "name": meta.get("name") or app_entity.get("summary", ""),
                "summary": app_entity.get("summary", ""),
                "description": meta.get("description", ""),
                "building": state.get("building", False),
                "updated_at": app_entity.get("updated_at"),
            })

    return JSONResponse({
        "agent": "builder",
        "space_id": space_id,
        "user_id": user_id,
        "assembled_at": now_utc.isoformat(),
        "prompt_structure": {
            "total_chars": total_chars,
            "token_estimate": total_chars // 4,
            "note": (
                "Prompt is static. Dynamic context = spec generated by Domus agent, "
                "which may include user/space context. Spec is passed as both system context "
                "('The user has requested: ...') and user message."
            ),
            "sections": {
                "role_and_process": {"chars": role_process_chars},
                "component_catalog": {"chars": component_catalog_chars},
                "action_dsl": {"chars": action_dsl_chars},
                "app_examples": {"chars": app_examples_chars},
                "design_context": {"chars": design_context_chars},
            },
            "tools": ["define_app", "finish_build"],
            "model": cfg.BUILDER_MODEL,
        },
        "declarative_apps": declarative_apps,
        "iframe_apps": iframe_apps,
    })
