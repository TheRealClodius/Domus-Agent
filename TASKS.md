# Domus Agent — Tasks

What to build, in order. Each task is a testable unit. TDD applies: write the failing test first, then the implementation.

**Prerequisite:** Supabase project must be set up with `001_init.sql` applied (tracked in the domus-web repo's TASKS.md). The agent needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in `.env`, and a test user + space seeded for local development.

---

## Phase 0: Foundation ✅

### 0.1 — `config.py` ✅
Environment-based config. Load from `.env` in dev, env vars in production. Expose:
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `ANTHROPIC_API_KEY`
- `DOMUS_SERVICE_TOKEN`
- Async Supabase client factory (`acreate_client()` with service role key)
- Anthropic `AsyncAnthropic` client factory

Validate that required vars are present on startup. Fail fast with clear error if missing.

### 0.2 — `main.py` scaffold ✅
FastAPI app with:
- `GET /health` → `{ "status": "ok" }`
- CORS middleware (allow Vercel origin)
- Service auth dependency: check `Authorization: Bearer <DOMUS_SERVICE_TOKEN>` header. 401 if missing/wrong.

No agent endpoint yet — just the app shell and auth gate.

### 0.3 — Structured logging ✅
Configure Python `logging` with JSON output. Include `space_id` and `user_id` as correlation fields. Log on: tool execution (tool name, duration), loop start/end (message length, turn count), errors. Required by OPS.md and CLAUDE.md conventions.

---

## Phase 1: Tools ✅

The agent's hands. Each tool is a function that takes params + `space_id` + `user_id`, executes against Supabase, and returns a result dict. No Claude involvement yet — just DB operations with tests.

### 1.1 — Tool definitions ✅
The 4 tool JSON schemas (for Claude's `tools` parameter). Store as a list of dicts in `agent/tools.py`. Match the definitions in ARCHITECTURE.md exactly.

### 1.2 — `create_entity` ✅
Insert a row into the entities table. Params: `type`, `content` (markdown body), `state` (structured data, only when a renderer needs typed fields), `presentation` (default `'window'`), `position` (default `{ x: 50, y: 50, locked: false }`), `size` (default `{ width: 600, height: 400 }`), `summary`. Always set `created_by: 'agent'` — the frontend depends on this for the agent glow animation. Returns the created entity (id, type, content, state, presentation, position, size, summary, created_by).

Test: create a note entity, verify it exists in Supabase with correct fields including `content` and `created_by: 'agent'`.

### 1.3 — `read_entity` ✅
Select a single entity by ID, scoped to `space_id`. Returns full row including `content` and `state`.

Test: create an entity, read it back, verify content and state match.

### 1.4 — `query_entities` ✅
Select entities matching filters. Supports: `type`, `search` (full-text on content + summary), `presentation`, `created_after`, `created_before`, `include_archived`, `limit` (default 20). Returns lightweight summaries: `id`, `type`, `summary`, `presentation`, `created_at`.

Test: create several entities with different types, query by type, verify filtering works. Test full-text search on content and summary.

### 1.5 — `update_entity` ✅
Update an entity. Params: `id`, and any combination of `content` (full replacement), `state` (RFC 7396 JSON Merge Patch via `jsonb_merge_patch`), `summary`, `position`, `size`, `presentation`.

Implementation note: the Supabase Python client's `.update()` doesn't natively call `jsonb_merge_patch`. Use `supabase.rpc()` to call the Postgres function, or write a wrapper SQL function that applies the merge patch and returns the updated row.

Test: create an entity, update its state with a partial patch, verify merge semantics (new keys added, existing keys preserved, null keys deleted).

### 1.6 — `execute_tool` dispatcher ✅
A single async function: `execute_tool(name, params, space_id, user_id) → dict`. Routes to the correct tool function. Returns the result or an error dict if the tool name is unknown or execution fails.

Test: dispatch to each tool by name, verify routing works.

---

## Phase 2: Context ✅

The system prompt builder. Queries Supabase for the lightweight context that Claude needs each turn.

### 2.1 — Entity index ✅
`get_entity_index(space_id) → list[dict]`. Query all non-archived entities (including hidden). Return `id`, `type`, `presentation`, `z_index`, `summary` for each.

Test: create several entities (mix of visible and hidden, one archived), verify the index includes hidden but excludes archived.

### 2.2 — Recent conversation turns ✅
`get_recent_turns(space_id, limit=5) → list[dict]`. Query the last N `conversation_turn` entities ordered by `created_at` DESC. Return `state` (which contains `role` and `content`).

Test: create 7 conversation turn entities, verify only the 5 most recent are returned.

### 2.3 — System prompt assembly ✅
`build_system_prompt(space_id, message) → str`. Assembles:
1. Base instructions (agent identity, tool descriptions)
2. Entity index from 2.1
3. Entity state shapes described in system prompt text (e.g., "A note entity has state: { title: string, content: string }"). No schema system or validation layer for v0 — Claude follows the instructions. Schemas and validation arrive when the frontend `/api/schemas` endpoint exists.
4. Recent turns from 2.2

Returns the complete system prompt string. No personality traits or dynamic schema injection yet — those are deferred.

Test: set up a space with entities and turns, verify the assembled prompt contains the expected sections.

---

## Phase 3: Loop ✅

The agent's brain. The `while True` loop that calls Claude, handles tool calls, and streams events.

### 3.1 — Conversation turn persistence ✅
`save_conversation_turn(space_id, user_id, role, content)`. Creates a hidden entity (`type='conversation_turn'`, `presentation='hidden'`) with `state: { role, content }`.

Test: save a user turn and an assistant turn, verify both exist as hidden entities.

### 3.2 — Single-turn agent (no tools) ✅
`run_agent(space_id, user_id, message, on_event)`. Calls Claude with the system prompt and message. If Claude responds with text only (no tool calls): save the user turn, save the assistant turn, done.

`on_event` is an async callback that receives stream events (text deltas, tool indicators). For now, just accumulate text.

Test: mock Anthropic to return a text-only response. Verify both conversation turns are saved, and `on_event` received the text.

### 3.3 — Multi-turn agent (with tools) ✅
Extend `run_agent` to handle tool calls. When Claude emits tool_use blocks: execute all tool calls in parallel via `execute_tool`, append results, loop. Exit when Claude responds with text only.

Test: mock Anthropic to return a tool call (e.g., create_entity), then a text response on the second call. Verify the entity was created AND the conversation turns were saved.

### 3.4 — SSE event formatting ✅
Define the SSE event types the endpoint will stream:
- `text_delta` — `{ type: "text_delta", content: "..." }`
- `tool_call_start` — `{ type: "tool_call_start", tool: "create_entity", id: "..." }`
- `tool_call_result` — `{ type: "tool_call_result", id: "...", result: {...} }`. For `create_entity` and `update_entity`, result includes the full entity payload (id, type, state, presentation, position, size, summary, created_by). This is how the frontend renders agent changes instantly — SSE is the primary channel, CDC confirms.
- `done` — `{ type: "done" }`
- `error` — `{ type: "error", message: "..." }`

Wire `on_event` to format these from Anthropic's stream events.

Test: run the agent with mocked Anthropic, collect SSE events, verify the event sequence matches expectations.

---

## Phase 4: Endpoint ✅

Wire the loop to a real HTTP endpoint.

### 4.1 — `POST /agent` SSE endpoint ✅
Accept the request payload:
```json
{
  "space_id": "uuid",
  "message": "string",
  "viewport": { "width": 1920, "height": 1080 },
  "focused_entity_id": null,
  "visible_entity_ids": []
}
```

Extract `user_id` from the auth-validated request (for v0 without Vercel proxy: accept `user_id` in payload behind service auth). Create an SSE `EventSourceResponse`. Run `run_agent` with `on_event` writing to the SSE stream.

Test: POST to the endpoint with a valid payload, verify SSE events stream back. Integration test with real Anthropic API (mark as slow/optional).

---

## Phase 5: Smoke test

### 5.1 — End-to-end manual test
With the server running locally and a real Supabase project:
1. `curl -N -X POST http://localhost:8000/agent` with a message like "Create a note about testing"
2. Verify SSE events stream back (text deltas + tool call results)
3. Check Supabase dashboard — a note entity should exist
4. Send another message referencing the note — verify the agent sees it in context

This is the "it works" moment. Document the curl commands in OPS.md.

---

## Deferred (build after v0 works)

| What | Depends on |
|------|-----------|
| `web_search` (Perplexity) | v0 loop working — add as 5th tool |
| `image_gen.py` (Gemini) | v0 loop working — intercept `type='image'` in create_entity |
| `memory.py` (compaction) | Conversation turns accumulating — trigger after >40 |
| `graph/` (NetworkX) | Edge entities existing — build graph ops on demand |
| `prompts/builder.py` | Basic entities working — inject for composed apps |
| Atomic `update_entity` | Concurrent turns — add `update_entity_with_patch` SQL function, replace Python read-modify-write with single `rpc()` call to eliminate race conditions |
| Concurrent turns | v0 loop working — add message queue between tool cycles |
| Schema discovery | Frontend `/api/schemas` endpoint existing |
| Usage tracking | Billing tables existing in Supabase |
| Personality traits in context | Fact entities existing in a space |
| Dynamic schema injection | Multiple app types with schemas existing |
