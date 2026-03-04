# Domus Agent — Internals

How the agent service works. For system-wide architecture, data model, and API contract, see `domus-web/docs/ARCHITECTURE.md`.

---

## Agent Loop (`agent/loop.py`)

A `while True` loop using the Anthropic SDK directly. No framework.

1. `context.py` assembles the system prompt (static instructions, entity index, personality, user facts, conversation summaries, recent turns, situational awareness signals)
2. `_log_prompt_sections()` emits a `prompt_sections` log line — which sections fired this turn
3. Claude (Sonnet) processes the message + system prompt
4. If Claude emits tool calls → execute in parallel → append results → loop
5. If Claude emits only text → save conversation turn → exit loop
6. After exit: fire-and-forget `record_usage(event_type='agent_turn')` with token counts; trigger compaction if >40 turns (skipped for FREE tier)

**Model usage:**
- `claude-sonnet-4-6` — interactive turns (fast, cheap, reliable tool use)
- `claude-opus-4-6` — memory compaction only (better summarization over long context)

**Tier parameter:** `run_agent(..., tier=None)` accepts the resolved `Tier` enum from `main.py`. Passed through to `execute_tool()` for quota enforcement. When `tier=Tier.FREE`, compaction is skipped (no Opus budget).

**Background tasks (`_bg`):** All fire-and-forget coroutines (`record_usage`, compaction, trim) are scheduled via `_bg(coro)` — a thin wrapper around `asyncio.create_task()` that stores the task reference in `_bg_tasks: set` to prevent the garbage collector from cancelling it before it completes. The reference is removed by a done-callback on task completion.

**Streaming:** The loop streams SSE events to the frontend via `on_event` callback. Text deltas and tool call results (including created/updated entities) flow through immediately — the frontend doesn't wait for CDC.

---

## System Prompt (`agent/context.py`)

The system prompt is thin. Three cacheable blocks, assembled fresh each turn.

**Block 0 — static (always the same):**
- `_BASE_INSTRUCTIONS`: entity type schemas, app-specific action flows, presentation modes, singleton app rules
- `IFRAME_BUILDER_CONTEXT` (`prompts/iframe_builder.py`): spec-writing brief for `build_app` — how to write a good spec and delegate to the builder agent. Intentionally thin: design system details belong in the builder agent's prompt, not here.

**Block 1 — semi-static (changes when space state changes):**
- Space name, user name
- User facts: up to 20 `fact` entities sorted by confidence descending
- Conversation summaries: all `conversation_summary` entities, oldest first
- Personality traits: all `personality_trait` entities in the space
- `=== Recently Active ===`: top 3 entities by `updated_at` descending — gives the agent a recency signal without extra queries
- Entity index: `[type] id: summary (presentation)` for all non-archived entities, excluding internal memory types (`conversation_turn`, `conversation_summary`, `fact`, `edge`, `personality_trait`). User-created entities are always included — even docked ones (`presentation='hidden'`).

**Block 2 — dynamic (changes every turn):**
- Canvas context:
  - Focused entity: full state + content (truncated at 2000 chars), type-aware hint if applicable (see `_FOCUS_HINTS`)
  - `Also visible:` — entities in `visible_entity_ids` listed as `[type] id: summary`, focused entity excluded to avoid duplication
- `=== Created this session ===`: entities with `created_at` within the last 90 minutes (`SESSION_WINDOW_MINUTES`) — lets the agent reference things it just created without a `read_entity` call
- Recent turns: last 5 `conversation_turn` entities
- Current date/time in user's timezone

**Not in the system prompt** (agent queries on demand):
- Full entity state of non-focused entities → `read_entity`
- Graph edges → `query_entities(type='edge')`

---

## Prompt Caching (`agent/context.py` + `agent/loop.py`)

Static content (~750 tokens of instructions + builder context) is re-used across all turns and tool-call iterations. Without caching it is re-tokenized on every Anthropic API call — 3× per turn for a 3-tool turn. Prompt caching gives ~60% TPM reduction on static tokens and 10% of normal billing cost for cached reads.

### 4-breakpoint structure per API call

```
┌──────────────────────────────────────────────────────────────┐
│ Tool definitions                cache_control ← breakpoint 1 │
│ System block 0: BASE_INSTRUCTIONS + IFRAME_BUILDER_CONTEXT   │
│                                 cache_control ← breakpoint 2 │
│ System block 1: space + user + facts + summaries + index     │
│                                 cache_control ← breakpoint 3 │
│ System block 2: canvas + recent_turns + temporal context     │
│                                 (no cache_control — dynamic) │
│ Messages: user → assistant[tool_use] → tool_results          │
│           last tool_result      cache_control ← breakpoint 4 │
│           (previous iterations' cache_control stripped)      │
└──────────────────────────────────────────────────────────────┘
```

`build_system_prompt()` returns a `list[dict]` (three text blocks with `cache_control`) rather than a plain string.

### Rolling tool_result cache (breakpoint 4)

Each tool-call iteration in the loop:
1. Strips `cache_control` from all prior `tool_result` message blocks
2. Adds `cache_control: {type: ephemeral}` to the **last** block of the new batch

This keeps exactly one active breakpoint 4 at all times, maximising cache hits without burning extra breakpoints.

### In-process TTL cache (`agent/context.py`)

A module-level dict avoids redundant Supabase round-trips within and between turns:

| Key pattern | TTL | Data |
|-------------|-----|------|
| `entity_index:{space_id}` | 60 s | User-facing entity summaries (`id, type, presentation, z_index, summary, updated_at, created_at`) — internal types excluded, hidden (docked) user entities included |
| `user_profile:{user_id}` | 300 s | User profile row |
| `space_info:{space_id}` | 300 s | Space metadata row |

Cache is populated on first access and served from memory on subsequent calls. `_cache_invalidate(key)` is async (lock-protected) and clears a specific entry. It is awaited by every tool that mutates entities (`create_entity`, `update_entity`, `build_app`, `update_app`, `call_entity_tool`) on success. The `/admin/domus-context` endpoint also awaits it on entry to guarantee live Supabase state in the response.

### Parallel context queries

`build_system_prompt()` runs all 8 independent Supabase queries via a single `asyncio.gather()` call — space info, user profile, entity index, recent turns, focused entity, conversation summaries, personality traits, and user facts all resolve concurrently.

---

## Situational Awareness (`agent/context.py`)

Four signals that close the gap between what the user is doing and what the agent knows, all resolved at prompt-assembly time — no extra tool calls needed.

### Visible entity listing (19.6)

`visible_entity_ids` (passed from the frontend per turn) is cross-referenced against the entity index. Each visible entity is listed as `[type] id: summary` under `Also visible:` in the Canvas Context block. The focused entity is excluded from this list to avoid duplication.

```
Also visible:
- [note] abc-123: Shopping list
- [image] def-456: Vacation photo
```

### Recency signal (19.7)

The entity index query fetches `updated_at` and `created_at` alongside the existing fields. The top 3 entities by `updated_at` are surfaced in `=== Recently Active ===` at the top of Block 1 — above the full entity index. The agent can reference recently-touched entities without scanning the full list.

### Type-aware focus hints (19.8)

`_FOCUS_HINTS` (module-level dict in `context.py`) maps entity types to a hint appended after the focused entity's state block. Currently:

```python
_FOCUS_HINTS = {
    "calendar": "Tip: use query_entities(type='calendar_event') to load this calendar's events.",
}
```

Without this, the agent would say "I don't see any events" when a calendar is focused, because `calendar_event` entities are `presentation='hidden'` and invisible in the index. The hint tells the agent to fetch them. Add entries here for any type whose related data is hidden.

### Session-created signal (19.9)

Entities with `created_at` within the last `SESSION_WINDOW_MINUTES` (90 min) are listed in `=== Created this session ===` in Block 2, before the recent turns. Lets the agent refer to things it created earlier in the session without a `read_entity` call. Entities missing `created_at` are silently skipped.

---

## Debug & Observability

### Section-presence logging (`agent/loop.py`)

After every `build_system_prompt()` call, `_log_prompt_sections()` emits a single `prompt_sections` structured log line. In prod, grep for this event to verify situational awareness signals are firing:

```json
{
  "event": "prompt_sections",
  "space_id": "...",
  "has_recently_active": true,
  "has_canvas_context": true,
  "has_also_visible": false,
  "has_calendar_hint": true,
  "has_session_created": false,
  "has_conversation_history": true,
  "semi_static_chars": 1840,
  "dynamic_chars": 340
}
```

`semi_static_chars` and `dynamic_chars` are a rough token-pressure proxy — watch them if prompt lengths grow.

### Debug prompt endpoint (`main.py`)

`POST /debug/prompt` returns the full assembled system prompt as JSON — block text, char counts, and a `sections_present` map. Protected by service auth + requires `DEBUG_PROMPT_ENABLED=true` in the environment. Returns 404 otherwise.

```bash
curl -X POST http://localhost:8000/debug/prompt \
  -H "Authorization: Bearer $DOMUS_SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"space_id": "...", "message": "test", "user_id": "...", "focused_entity_id": "..."}'
```

**Never set `DEBUG_PROMPT_ENABLED=true` in production** — this endpoint exposes the full system prompt including user data.

---

## Startup & Lifecycle (`main.py`)

FastAPI lifespan creates shared clients **once** at startup:

```python
@asynccontextmanager
async def lifespan(app):
    supabase = await cfg.acreate_client()
    anthropic = cfg.create_anthropic_client()
    cfg.set_shared_clients(anthropic, supabase)
    app.state.supabase = supabase
    app.state.anthropic = anthropic
    yield
    # graceful close
```

The `/agent` endpoint reads `request.app.state.supabase` and `request.app.state.anthropic` — no new clients per request. `config.py` exposes `get_shared_anthropic_client()` / `get_shared_supabase_client()` for modules that need clients outside the request path.

### Request lifecycle (POST /agent)

Before streaming starts, `main.py` runs three synchronous gates:

1. **Tier resolution:** `get_user_tier(supabase, user_id)` — reads `users.plan`, maps to `Tier.FREE / CITIZEN / EXTRA`
2. **Rate limit:** `check_rate_limit(user_id, tier)` — in-memory sliding window; returns `429` with `Retry-After` header if over RPM limit
3. **Quota check:** `check_quota(supabase, user_id, tier, "agent_turn")` — counts `usage_events` rows since midnight UTC; returns `429` with `resets_at` if daily limit exhausted

All three complete before the `StreamingResponse` is created. If any gate fails, the client gets a JSON 429 — no SSE stream is opened.

### RateLimitError handling

When Anthropic returns `RateLimitError` inside the agent loop, the error is caught, a `{"type": "error", "code": "rate_limit"}` SSE event is emitted, and the loop returns `""` cleanly without re-raising.

---

## Tools (`agent/tools.py`)

Nine tools are defined in `TOOL_DEFINITIONS` and dispatched through `execute_tool`. The first five are the core entity primitives; the last four are app-interaction helpers.

| Tool | What it does |
|------|-------------|
| `create_entity` | Create any entity. Internal types bypass the frontend (see below). |
| `update_entity` | Patch entity state via RFC 7396 JSON Merge Patch |
| `query_entities` | Search/filter entities — returns `(id, type, summary, presentation, created_at)` |
| `read_entity` | Get one entity's full state by ID |
| `web_search` | Search the web via Perplexity API — returns sourced answers with citations |
| `get_entity_schema` | Discover what structured actions an app entity supports (hits `domus-web`) |
| `call_entity_tool` | Execute a structured action on an app entity (hits `domus-web`) |
| `build_app` | Create a React + shadcn/ui iframe app entity (`state._code`) |
| `update_app` | Update an existing iframe app's code, schema, or state |

If you're tempted to add a tenth tool, you're probably doing something wrong.

**State merge semantics (RFC 7396):**
- Provided scalar fields overwrite
- Provided object fields merge recursively
- `null` deletes the key
- Arrays are always replaced entirely (agent does read-modify-write to append)
- Omitted fields are preserved

### `create_entity` dual routing

`create_entity` routes based on `_INTERNAL_TYPES`:

```python
_INTERNAL_TYPES = {
    "conversation_turn", "conversation_summary",
    "fact", "personality_trait", "edge",
}
```

- **Internal types** → insert directly to Supabase with `presentation: "hidden"` enforced (no httpx call). The caller's `presentation` param is ignored — these types must never appear on canvas.
- **User-facing types** → POST to `domus-web /api/entities` as before (frontend applies its own presentation rules, Realtime broadcasts to the canvas).

This prevents the frontend from rendering memory/system entities as visible cards when it doesn't recognise the type.

### Quota enforcement in `execute_tool`

`execute_tool(client, name, params, space_id, user_id, tier=None)` enforces per-tool quotas when `tier` is provided:

- **image generation:** before calling `create_entity` with `type='image'` and `state.generation_prompt` present, checks `check_quota(..., "image_generation")`. If exhausted, returns `{"error": "quota_exhausted", ...}` — Gemini is never called.
- **web search:** before calling `web_search`, checks `check_quota(..., "web_search")`. Same short-circuit.

After every execution (success or error), fires `record_usage(event_type='tool_call')` via `_bg()` — never blocks the SSE stream.

### Batch auto-positioning

When the agent calls `create_entity` multiple times in a single tool-call batch and none of the calls includes a `position`, `loop.py` calls `compute_group_positions(count, viewport)` before executing any of them. This tiles the entities in a centered grid using percentage coordinates derived from the current viewport dimensions. Position slots are injected into `params_list` before the parallel `asyncio.gather` runs. Without this, all entities would stack on the default `{x: 50, y: 50}`.

---

## UI Action Mirroring (`agent/action_bridge.py` + `agent/tools.py`)

When `UI_ACTION_MIRRORING=true`, visible entity mutations are routed through the frontend's UI state machine instead of being written directly by the agent.

### Protocol flow

1. Agent calls `create_entity` / `update_entity` / `build_app` / `update_app`
2. `execute_tool` pre-processes server-side (e.g. image generation) then emits a `ui_action` SSE event:
   ```json
   {"type": "ui_action", "action_id": "act_xxx", "turn_id": "turn_yyy", "action": "create_entity", "params": {...}}
   ```
3. Frontend executes through its state machine, then POSTs result:
   ```
   POST /agent/action-result
   {"action_id": "act_xxx", "space_id": "...", "user_id": "...", "success": true, "result": {...}}
   ```
4. Bridge resolves the `asyncio.Future` → agent loop continues

### Timeout and fallback

If the frontend doesn't respond within `UI_ACTION_TIMEOUT_SECONDS` (default 15s, configurable via env var), the agent falls back to direct server-side execution. This preserves continuity — the user sees the result regardless.

### Mirrored tools

Only tools in `_MIRRORED_TOOLS` are intercepted: `create_entity`, `update_entity`, `build_app`, `update_app`. Internal types (`conversation_turn`, `fact`, etc.) always bypass mirroring — they have no UI representation.

### Bridge lifecycle

- One `ActionBridge` per agent turn, registered in a module-level dict keyed by `(space_id, user_id)`.
- Registered at turn start, unregistered in `finally` block on turn end.
- In-memory only — correct for single-worker Railway deployment.

### turn_id correlation

Each agent turn generates a `turn_id` (e.g. `turn_a1b2c3d4e5f6`). It's included in:
- `ui_action` SSE events
- All mirror path structured logs
- `agent_turn_start` log

### Observability

Structured log events emitted during the mirror path:

| Event | Level | When |
|-------|-------|------|
| `ui_action_resolved` | INFO | Frontend responded successfully (includes `latency_ms`) |
| `ui_action_failed` | WARNING | Frontend responded with `success: false` |
| `ui_action_timeout_fallback` | WARNING | No response within timeout; falling back to direct execution |
| `ui_action_callback_received` | INFO | Callback endpoint received a POST from frontend |

All include `tool`, `action_id`, `turn_id`, `space_id` in extras.

---

## File Attachments (`agent/loop.py`)

`run_agent` accepts `context_items: list[dict]`, where each item is a file attachment from the frontend with a base64 data URL:

```python
{"name": "report.pdf", "type": "application/pdf", "data": "data:application/pdf;base64,..."}
```

`_build_multimodal_content(context_items, message)` converts these into Claude content blocks before the first API call:

| MIME type | Claude block type |
|-----------|-------------------|
| `image/*` | `image` (base64 source) |
| `application/pdf` | `document` (base64 source) |
| anything else | `text` (decoded UTF-8 prefixed with `[File: name]`) |

The text message is always appended last. Attachments are **not** persisted in `conversation_turn` state — only the user's text message is saved.

---

## Image Generation (`agent/image_gen.py`)

Uses Gemini, called automatically from `create_entity` in `tools.py` when `type='image'` and `state.generation_prompt` is present. Not part of the agent loop directly.

**Model:** `config.IMAGE_GEN_MODEL` (default: `gemini-2.5-flash-image`) via `response_modalities=["IMAGE"]`
**SDK:** `google-genai` (`client.models.generate_content()`)
**Dependencies:** `google-genai`, `Pillow` (PIL)

**Trigger:** `create_entity(type='image', state={ generation_prompt: "..." })` — `tools.py` detects the combination and calls `generate_image()` before inserting the entity.

**Key name normalization:** `create_entity` also accepts `state.prompt` (common LLM alias) and silently normalizes it to `state.generation_prompt`. The tool description explicitly calls out `generation_prompt` to keep the agent on the right path — if that description is ever weakened, the LLM defaults to `prompt` and generation silently fails (entity is created without an image_url).

**Pipeline (all in-memory):**
```
Prompt → Gemini generate_content(response_modalities=["IMAGE"])
  → response.candidates[0].content.parts[0].inline_data.data
  → PIL Image.open(BytesIO(bytes)) → get width/height → save as PNG
  → Supabase Storage upload to images/{space_id}/{uuid}.png
  → public_url stored in entity state
  → record_usage(event_type='image_generation') [fire-and-forget]
```

**Entity state after generation:**
```json
{
  "generation_prompt": "a serene sunset over mountains",
  "image_url": "https://....supabase.co/storage/v1/object/public/images/space-id/uuid.png",
  "width": 1024,
  "height": 1024
}
```

**Defaults for image entities:** `presentation: "card"`, `size: { width: 232, height: 300 }`

**Error handling:** If Gemini fails, the entity is still created with `state.generation_error` instead of `image_url`. The frontend can display an error state.

---

## Web Search (`tools.py`)

Calls Perplexity API (`POST https://api.perplexity.ai/chat/completions`) via `httpx`. Returns sourced answers with citations. The agent creates entities from results — search results themselves are ephemeral.

**Params:**
- `query` (string, required) — the search query
- `focus` (enum: `"general"` | `"academic"` | `"news"`, default `"general"`) — selects model: `sonar-pro` for academic, `sonar` otherwise

**Return shape (success):**
```json
{ "answer": "...", "citations": [{ "url": "https://..." }] }
```

**Return shape (error):**
```json
{ "error": "web_search_unavailable", "message": "PERPLEXITY_API_KEY not configured" }
{ "error": "web_search_failed", "status": 429 }
{ "error": "quota_exhausted", "type": "web_search", "message": "..." }
```

**Key missing:** If `PERPLEXITY_API_KEY` is empty, returns `web_search_unavailable` immediately — no HTTP call is made.

---

## Knowledge Graph (`graph/`)

Relationships between entities are stored as entities themselves (`type='edge'`, `presentation='hidden'`).

**`graph/store.py`** — Adjacency list using edge entities in the entities table.

**`graph/ops.py`** — NetworkX operations loaded on-demand from edge entities:
- `build_graph(edges)` → `nx.DiGraph`
- `related_entities(G, entity_id, depth=2)` → BFS traversal
- `find_clusters(G)` → strongly connected components

The agent queries edges on demand via `query_entities(type='edge')`. Graph context is not pre-loaded.

---

## Memory System (`agent/memory.py`)

Memory is not a separate system. It's entities with `presentation: 'hidden'`.

**Legitimate `presentation='hidden'` uses** — these are not bugs:

| Who sets it | Entity / situation | Why |
|-------------|-------------------|-----|
| Agent | `conversation_turn`, `conversation_summary`, `fact`, `edge`, `personality_trait` | Internal memory — never on canvas |
| Frontend | Images/notes inside a folder (`gather`/`add_children`) | Folder is the canvas item; children are hidden |
| Frontend | `calendar_event` | Always accessed via the calendar entity, never standalone |
| Frontend | Singleton dock apps (`calendar`, `chat`, `settings`, `sounds`) | Managed by the dock; not canvas windows |
| User / Frontend | Any entity the user docks (minimises) | Restored via dock click or agent `update_entity` → `presentation: 'window'` |

**Bug pattern (now fixed in frontend):** `Window.tsx` close button and `scatterFolder`/`ejectFromFolder` paths were calling `updatePresentation(id, 'hidden')` instead of `archive(id)`. This left ghost entities — visible to neither the user nor (previously) the agent.

| Entity type | Purpose |
|-------------|---------|
| `conversation_turn` | A single user or assistant message |
| `conversation_summary` | Compressed summary of N turns |
| `fact` | Something the agent learned about the user |
| `personality_trait` | How the agent should behave |
| `edge` | Relationship between two entities |

**Compaction** (triggered when turn count >40, skipped for FREE tier):
1. Take turns beyond the recent window
2. Call Opus: "Summarize this conversation segment. Extract any facts about the user."
3. Create `conversation_summary` entity
4. Create `fact`, `personality_trait`, and `edge` entities in parallel via `asyncio.gather`
5. Archive original turns (`archived: true`)
6. Fire-and-forget `record_usage(event_type='compaction')` with Opus token counts via `_bg()`

**No embeddings.** Recency + full-text search + knowledge graph.

---

## Usage Tracking & Tier Limits (`agent/usage.py`)

Every billable action is recorded in the `usage_events` Supabase table. Tier limits and rate limits are enforced before and during agent execution.

### Tier enum

```python
class Tier(str, Enum):
    FREE = "free"       # null/unrecognized plan
    CITIZEN = "citizen" # base subscription
    EXTRA = "extra"     # pay-as-you-go
```

Resolved from `users.plan` via `get_user_tier(client, user_id)` — cached in-process for 5 minutes.

### Daily quotas (`config.TIER_LIMITS`)

| Event type | FREE | CITIZEN | EXTRA |
|------------|------|---------|-------|
| `agent_turn` | 10 | 200 | 1000 |
| `image_generation` | 0 | 20 | 100 |
| `web_search` | 5 | 50 | 200 |

Quotas reset at midnight UTC. `check_quota()` counts `usage_events` rows since midnight for the given user + event_type. Returns `{allowed, remaining, limit, resets_at}`.

### Rate limits (`config.RATE_LIMITS`)

| Tier | Requests/minute |
|------|----------------|
| FREE | 5 |
| CITIZEN | 20 |
| EXTRA | 60 |

`check_rate_limit(user_id, tier)` uses an in-memory sliding window (`_rate_windows` dict, module-level). Returns `(allowed: bool, retry_after_seconds: int)`.

### Concurrent turn slots

`acquire_turn_slot(user_id, tier)` and `release_turn_slot(user_id)` are both **async** and guarded by `_active_turns_lock`. The lock prevents the TOCTOU race in the read-modify-write on the shared `_active_turns` counter. `release_turn_slot` is idempotent — safe to call even if no slot was held. The `_tier_cache` dict is intentionally not lock-protected: a concurrent miss for the same user causes two identical writes, which is benign.

### Usage events recorded

| Event type | Recorded in | Metadata |
|------------|-------------|----------|
| `agent_turn` | `loop.py` after each Anthropic call | `input_tokens`, `output_tokens`, `model` |
| `tool_call` | `tools.py` after every `execute_tool` | `tool_name`, `duration_ms`, `success` |
| `image_generation` | `image_gen.py` after upload | `model`, `prompt_length` |
| `compaction` | `memory.py` after Opus call | `input_tokens`, `output_tokens`, `turns_compacted` |

All `record_usage()` calls go via `_bg()` (module-level helper in `loop.py`) — they never block the SSE stream. `_bg()` keeps a strong reference to in-flight tasks in `_bg_tasks` to prevent premature GC. `record_usage()` catches all exceptions and logs as warnings.

### `usage_events` table schema (migration D-2)

```sql
CREATE TABLE usage_events (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  space_id UUID NOT NULL,
  event_type TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX usage_events_user_type_day ON usage_events (user_id, event_type, created_at DESC);
```

RLS: users can read their own rows. Agent uses service role key to insert.

---

## Custom Apps

Two systems exist for agent-generated apps:

### React Iframe Apps (`build_app` / `update_app`)

The primary system. The Domus agent writes a spec and calls `build_app`; the builder agent handles code generation and design system compliance.

- **Tool:** `build_app(name, icon, description, code, schema, initial_state)` → creates `type='app'` entity
- **Runtime:** React + shadcn/ui in a sandboxed iframe. Code stored in `state._code`, tool schemas in `state._schema`, app metadata in `state._meta`.
- **Iteration:** `update_app(entity_id, code?, schema?, state_patch?)` — Domus describes what to change; builder implements it
- **Interaction:** `get_entity_schema` + `call_entity_tool` to verify and interact with the running app
- **Prompt context:** `IFRAME_BUILDER_CONTEXT` in Block 0 is a thin spec-writing brief (~700 chars). Full design system (color tokens, component API, examples) lives in the builder agent's prompt only.

### Declarative View-Tree Builder (`agent/builder.py`)

A background sub-agent that constructs apps using a declarative view tree + actions map. Launched as `asyncio.create_task(builder_loop(...))` — separate from the main agent loop, no SSE streaming.

- **Tool:** `define_app(view, actions, state, summary_template)` — writes full definition to `state._def` in one shot
- **State shape:** `{ _def: { view: [...], actions: {...}, name, icon }, ...app_data }`
- **Prompt:** `build_builder_prompt(spec)` from `agent/prompts/builder.py`
- **Status:** Implemented and tested. Not yet wired into a main-agent tool.

---

## SSE Streaming

The `/agent` endpoint streams events to the frontend using Server-Sent Events.

`run_agent` pushes events to an `asyncio.Queue`. A separate `asyncio.Task` reads from the queue and yields `StreamingResponse` chunks. This decouples event production from HTTP delivery — tool results are pushed to the queue immediately as they complete, without waiting for the loop to finish.

The endpoint awaits the agent task after a terminal `done` or `error` event to drain any exception before closing the connection (prevents "Task exception was never retrieved" asyncio warnings).

---

## Error Recovery

| Failure | Behavior |
|---------|----------|
| SSE connection drops | Frontend auto-reconnects with backoff. Fetches latest entity state on reconnect. |
| Agent service down | Frontend shows inline error. User can still interact with all entities. |
| Partial tool execution | Already-committed entity writes persist. Agent sees them on next turn. |
| Supabase Realtime disconnects | Client reconnects automatically. Full entity fetch on reconnect. |

---

## Schema Discovery

App schemas are fetched on-demand per entity from the frontend (`GET /api/entities/{id}/schema`) via `get_entity_schema`. The agent calls this before `call_entity_tool` to discover what actions an entity supports. No startup caching — each call hits the frontend, which is authoritative.

Both `get_entity_schema` and `call_entity_tool` handle httpx network errors the same way as `create_entity`/`update_entity`: `TimeoutException` → `{"error": "frontend_timeout"}`, `ConnectError` → `{"error": "frontend_unreachable"}`.

---

## Service Authentication

The agent service is not publicly accessible. Vercel's SSE proxy is the only entry point.

1. **User auth:** Vercel validates Supabase auth cookie → extracts `user_id`
2. **Service auth:** Vercel forwards with `Authorization: Bearer <DOMUS_SERVICE_TOKEN>`

The agent trusts `user_id` and `space_id` from the payload because Vercel already validated the user. RLS provides defense-in-depth.
