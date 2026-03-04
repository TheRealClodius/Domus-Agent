# Domus-Agent

The standalone Python agent service for Domus — an agent-first spatial OS. 
This repo is the agent core, not the app. When developing locally, the frontend lives in `/Users/andreiclodius/Documents/Projects/Domus/...`. You can to to .../Domus/Docs/ARCHITECTURE.md to have a unified view of the product and in .../Domus/OPS.md to see how services connect. When developing remotely you won't be able to access the Domus repo. 

In this repo you also have a docs/AGENT-INTERNALS.MD and a docs/OPS.md with domus-agent specific info.
 
## System Context

Domus is three things: a Next.js frontend on Vercel, this agent service on Railway, and Supabase (Postgres, Auth, Realtime, Storage). The agent is the brain — it takes user messages, calls Claude (Sonnet for turns, Opus for compaction), and manipulates entities in Supabase via 9 tools. The frontend streams agent responses via SSE through a Vercel proxy.


## Quick Reference

- **Python:** 3.11+
- **Run:** `uvicorn main:app --reload --port 8000`
- **Test:** `pytest`
- **Deps:** `pip install -r requirements.txt`

## Project Structure

```
agent/
  loop.py            — while True agent loop, Anthropic SDK direct, SSE streaming
  context.py         — Lightweight system prompt (entity index, schemas, personality, recent turns)
  tools.py           — 9 tools + execute_tool dispatcher (see AGENT-INTERNALS.md § Tools)
  image_gen.py       — Gemini image generation (google-genai SDK, PIL, Supabase Storage)
  logging.py         — Structured JSON logging (setup_logging, get_logger, log_tool_execution)
  memory.py          — Compaction: Opus summarizes old turns, extracts facts + edges
  usage.py           — Tier resolution, quota enforcement, rate limiting, usage recording
  action_bridge.py   — Future-based bridge for UI action mirroring
  builder.py         — Declarative view-tree builder sub-agent
  prompts/
    builder.py       — Declarative app builder prompt
    iframe_builder.py — Iframe app spec-writing brief (injected into system prompt)
main.py              — FastAPI app + SSE endpoint
config.py            — Environment-based config
```

## Core Principles

1. **Everything is an entity.** Notes, images, calendars, conversation turns, facts, edges — all rows in one `entities` table. `type` determines rendering, `presentation` determines framing.
2. **9 tools, not 15.** 5 entity primitives (create, update, query, read, web_search) + 4 app helpers (get_entity_schema, call_entity_tool, build_app, update_app). If you're tempted to add a tenth, you're doing something wrong.
3. **Agentic search, not fat prompts.** System prompt is thin (entity index + personality + recent turns). Agent discovers details on demand via query_entities + read_entity.
4. **Claude direct, no framework.** Anthropic SDK. No LangChain. The loop is ~60 lines.
5. **Memory is entities.** conversation_turn, conversation_summary, fact, personality_trait, edge — all hidden entities. No embeddings, no vector store.

## Key Conventions

- Entity state uses RFC 7396 JSON Merge Patch (provided fields overwrite, null deletes, arrays replaced entirely, omitted preserved)
- Agent writes raw state directly — no reducers (reducers are frontend-only)
- Image pipeline is fully in-memory: Supabase Storage -> BytesIO -> PIL -> Gemini -> PIL -> BytesIO -> Supabase Storage
- App schemas fetched from domus-web (`GET /api/schemas`), cached in-memory
- Structured JSON logs with `space_id` and `user_id` correlation fields
- Service auth: `DOMUS_SERVICE_TOKEN` shared secret with Vercel proxy. Agent trusts user_id/space_id from payload.

## TDD Rules

Tests first. No exceptions.

1. **Red → Green → Refactor.** Write a failing test that specifies the behavior. Write the minimum code to pass it. Clean up. Do not write implementation before the test exists.
2. **One test file per module.** `agent/tools.py` → `tests/test_tools.py`. `agent/action_bridge.py` → `tests/test_action_bridge.py`.
3. **Mock external services, not internal logic.** Mock Supabase, Anthropic, Gemini, Perplexity at the client boundary. Do not mock internal functions to make tests pass.
4. **Test behavior, not implementation.** Assert what a function returns or what side effects it produces. Do not assert internal call counts or argument shapes unless testing integration points.
5. **Async tests use `pytest.mark.asyncio`.** Most of this codebase is async. Use `pytest-asyncio` fixtures.
6. **Run `pytest` before claiming anything works.** Green tests are the only proof. If you can't run the tests, say so.

## Architecture Docs (this repo)

- [docs/AGENT-INTERNALS.md](docs/AGENT-INTERNALS.md) — Agent loop, tools, image gen, memory, UI mirroring, composed apps
- [docs/OPS.md](docs/OPS.md) — Run, test, deploy, env vars, dependencies
