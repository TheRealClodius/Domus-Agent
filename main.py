"""FastAPI app — Domus Agent service entry point."""

import asyncio

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import config
from config import acreate_client, create_anthropic_client
from agent.logging import setup_logging
from agent.loop import run_agent, format_sse_event

setup_logging()

app = FastAPI(title="Domus Agent")

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
    if token != config.DOMUS_SERVICE_TOKEN:
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


@app.post("/agent", dependencies=[Depends(verify_service_auth)])
async def agent_endpoint(req: AgentRequest):
    """Accept a user message and stream SSE events from the agent loop."""
    supabase = await acreate_client()
    anthropic = create_anthropic_client()

    queue: asyncio.Queue = asyncio.Queue()

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
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return StreamingResponse(generate(), media_type="text/event-stream")
