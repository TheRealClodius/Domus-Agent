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

## Phase 6: Memory

Without compaction, conversations fill the context window and eventually break. This is the most critical post-v0 feature.

### 6.1 — Compaction logic (`agent/memory.py`)

`compact_conversation(space_id, user_id) → dict`. When triggered:

1. Count `conversation_turn` entities in the space
2. If ≤40, return early (no compaction needed)
3. Take all turns except the most recent 5 (the "recent window")
4. Call Opus (`claude-opus-4-6`) with those turns: "Summarize this conversation segment. Extract any facts about the user. Identify relationships between entities."
5. Parse Opus response into three outputs:
   - **Summary text** → create a `conversation_summary` entity (`presentation='hidden'`, `state: { summary, turn_count, first_turn_at, last_turn_at }`)
   - **Facts** → create `fact` entities (`presentation='hidden'`, `state: { content, confidence, source_turn_ids }`)
   - **Edges** → create `edge` entities (`presentation='hidden'`, `state: { source_id, target_id, relation, weight }`)
6. Archive the compacted turns (`update_entity(id, archived=True)`)
7. Return `{ summary_id, fact_count, edge_count, turns_archived }`

Test: mock Anthropic (Opus call) to return a structured compaction response. Create 45 conversation turns, run compaction, verify: summary entity created, fact entities created, old turns archived, recent 5 preserved.

### 6.2 — Wire compaction into context

Update `agent/context.py`:
1. `get_conversation_summaries(space_id) → list[dict]`. Query `conversation_summary` entities ordered by `created_at`. Return `state.summary` for each.
2. Update `build_system_prompt` to include summaries between base instructions and recent turns (chronological: summaries first, then recent turns — gives Claude the full arc).
3. Include `personality_trait` entities in system prompt (query by type, include `state.content` for each).

Update `agent/loop.py`:
4. After `run_agent` completes, call `compact_conversation` if turn count exceeds threshold. Run as a background task — don't block the SSE response.

Test: build system prompt with summaries and personality traits present, verify they appear in the correct sections. Test that compaction triggers after the agent loop completes when turn count >40.

---

## Phase 7: Web Search

Completes the "5 tools, not 15" contract.

### 7.1 — `web_search` tool

Add `web_search` to `agent/tools.py`:
- Params: `query` (string, required), `focus` (enum: `"general"` | `"academic"` | `"news"`, default `"general"`)
- Implementation: `httpx.AsyncClient` POST to `https://api.perplexity.ai/chat/completions` with model `sonar` (or `sonar-pro` for academic). API key from `config.py` (`PERPLEXITY_API_KEY`).
- Return: `{ answer, citations: [{ title, url }] }`
- The agent creates entities from results — search results themselves are ephemeral (not stored as entities).

Add `PERPLEXITY_API_KEY` to `config.py` (optional — agent works without it, `web_search` returns an error if unconfigured).

Add `web_search` to the tool definitions list and `execute_tool` dispatcher.

Test: mock `httpx` to return a Perplexity-shaped response. Verify answer and citations are extracted. Test error case when API key is missing. Test dispatch via `execute_tool`.

---

## Phase 8: Image Generation

The image pipeline. Fully in-memory: Supabase Storage ↔ BytesIO ↔ PIL ↔ Gemini.

### 8.1 — Generate from prompt (`agent/image_gen.py`)

`generate_image(prompt, space_id) → dict`. Pipeline:
1. Call Gemini (`gemini-2.5-flash-image`) via `google-genai` SDK: `client.models.generate_content([prompt])`, with `response_modalities=["IMAGE"]`
2. Extract image from response: `response.candidates[0].content.parts[0].inline_data`
3. Load into PIL `Image.open(BytesIO(image_bytes))`
4. Convert to PNG bytes via `BytesIO`
5. Upload to Supabase Storage (`images/{space_id}/{uuid}.png`)
6. Return `{ storage_path, public_url, width, height }`

Add `GEMINI_API_KEY` to `config.py` (optional — like Perplexity).

Test: mock Gemini client and Supabase Storage. Verify the full pipeline: prompt in → image bytes extracted → uploaded to storage → URL returned.

### 8.2 — Wire into `create_entity`

When `create_entity` is called with `type='image'` and `state.generation_prompt` is present:
1. Call `generate_image(state.generation_prompt, space_id)`
2. Set `state.image_url` to the returned `public_url`
3. Set `state.width` and `state.height` from generation result
4. Preserve `state.generation_prompt` for reference
5. Create the entity with the enriched state

**Inspire intent (generate with references):** If `state.reference_entity_ids` is present, download those entities' images from Supabase Storage, convert to PIL, and include them alongside the prompt in the `generate_content` call: `generate_content([prompt, ref_image_1, ref_image_2, ...])`. Same pipeline, same function — just with extra image inputs. Gemini uses them as visual context when generating.

Test: mock Gemini + Storage. Call `create_entity(type='image', state={ generation_prompt: 'a sunset' })`. Verify entity is created with `image_url` populated.

### 8.3 — Edit existing images

`edit_image(edit_prompt, current_image_url, space_id) → dict`. Pipeline:
1. Download current image from Supabase Storage → `BytesIO` → PIL
2. Call Gemini: `generate_content([edit_prompt, current_image])` — text + image input
3. Same output pipeline as generate (PIL → BytesIO → Storage upload)
4. Return new `{ storage_path, public_url, width, height }`

Wire into `update_entity`: when updating a `type='image'` entity and `state.edit_prompt` is provided, call `edit_image` with the current `state.image_url` and the edit prompt. Update `state.image_url` to the new image. Append to `state.edit_history` (list of `{ prompt, previous_url }`).

Test: mock Gemini + Storage. Create an image entity, then update with an edit prompt. Verify new image URL is set and edit history is tracked.

---

## Phase 9: Knowledge Graph

Relationships between entities, stored as entities. Enables "related to this" queries and clustering.

### 9.1 — Edge storage (`graph/store.py`)

Edge entities are regular entities with `type='edge'`, `presentation='hidden'`, and state:
```json
{ "source_id": "uuid", "target_id": "uuid", "relation": "string", "weight": 1.0 }
```

`create_edge(source_id, target_id, relation, space_id, user_id, weight=1.0) → dict`. Wrapper around `create_entity` that enforces the edge schema. Deduplicates: if an edge with the same `source_id`, `target_id`, and `relation` exists, update its weight instead of creating a duplicate.

`get_edges(space_id) → list[dict]`. Query all non-archived edge entities. Return `state` for each.

Test: create edges between entities, verify deduplication. Query edges, verify all returned.

### 9.2 — Graph operations (`graph/ops.py`)

`build_graph(edges) → nx.DiGraph`. Takes edge dicts from `get_edges`, returns a NetworkX directed graph with `relation` and `weight` as edge attributes.

`related_entities(G, entity_id, depth=2) → list[str]`. BFS traversal from `entity_id` up to `depth` hops. Returns list of related entity IDs, ordered by proximity (depth 1 first, then depth 2).

`find_clusters(G) → list[set[str]]`. Strongly connected components. Returns clusters of entity IDs that are densely interconnected.

Test: build a graph with known edges, verify BFS returns correct related entities at each depth. Test clusters with a graph that has two distinct groups.

### 9.3 — Wire graph into context

Update `agent/context.py`:
1. When `focused_entity_id` is provided, call `get_edges` → `build_graph` → `related_entities(G, focused_entity_id, depth=2)`
2. Include related entity summaries in the system prompt under a "Related entities" section
3. Only fetch graph context when there's a focused entity — skip for general messages

Test: set up entities with edges, build system prompt with a focused entity, verify related entities appear in the prompt.

---

## Phase 10: Composed Apps

The agent creates app types on the fly using declarative block-based composition.

### 10.1 — Builder prompt (`agent/prompts/builder.py`)

`get_builder_prompt() → str`. Returns the ~30–40 line prompt containing:
- Block primitive reference — content: heading, text, list, divider, callout; media: image, file; data: table, key-value, stat, progress, chart; interactive: checklist, toggle; input: text-input, number-input, date-input, select; reference: entity-ref; layout: columns, section
- Iteration protocol (plan → create → verify → extend → loop)
- Validation rules (required fields per block type)
- A compact example of a composed entity

Test: verify the prompt contains the key sections (primitives, protocol, example). Verify it's concise (<50 lines).

### 10.2 — Inject builder prompt + composed app context in context

Update `agent/context.py`:
1. Detect when builder prompt is needed: user message implies app creation (heuristic: keywords like "build", "create an app", "make a tracker") OR `focused_entity_id` points to an entity with `state.blocks`
2. When detected, append `get_builder_prompt()` to the system prompt
3. Keep it lightweight — only inject when relevant
4. **Composed app context parity:** Include composed app block summaries in the "Relevant App Types" section alongside built-in schemas. For each composed type in the space, derive a block summary from entity data (block types + counts, not full content — e.g., "heading, checklist (5 items), progress"). Same relevance filtering as built-in schemas (visible entities + message intent). This gives the agent equal structural awareness for composed types — no `read_entity` needed just to understand what blocks a type uses.

Test: message "build me a habit tracker" triggers injection. Message "what's the weather" does not. Focused entity with `state.blocks` triggers injection. Composed app types that are visible appear in "Relevant App Types" with block summaries.

---

## Phase 11: Hardening

Production correctness under real-world conditions.

### 11.1 — Atomic `update_entity` via RPC

Create a Supabase SQL function `update_entity_with_patch(entity_id uuid, patch jsonb) → jsonb` that applies RFC 7396 merge patch atomically in Postgres. No read-modify-write race condition.

Update `update_entity` in `tools.py` to call `supabase.rpc('update_entity_with_patch', ...)` instead of the current Python-side merge.

Test: verify merge semantics still pass (same test cases as 1.5). Test concurrent updates don't lose writes (two patches to the same entity in parallel should both apply).

### 11.2 — Schema discovery

`get_app_schemas(frontend_url) → dict`. Fetch schemas from `GET {frontend_url}/api/schemas`. Cache in-memory with TTL (5 min). Return `{ type_name: json_schema }`.

Update `tools.py`: validate entity state against the matching schema before writing. Return a clear error if validation fails.

Update `config.py`: add `DOMUS_WEB_URL` (the frontend origin).

Test: mock the schemas endpoint. Verify schemas are fetched and cached. Verify validation rejects invalid state. Verify cache TTL works.

### 11.3 — Concurrent turn handling

Add a per-space message queue in `main.py`:
1. If a message arrives while the agent is processing a turn for the same space, queue it
2. Between tool call cycles in `loop.py`, check the queue for new messages
3. If a queued message exists, append it to the conversation and continue
4. Handle "stop"/"cancel" messages: terminate the current loop, commit what's done

Test: simulate two messages arriving for the same space. Verify the second is queued and delivered between tool cycles. Test cancel behavior.

---

## Phase 12: Usage Tracking & Limits

You can't charge for what you can't measure. Three tiers: guest (no auth), subscribed (paying), subscribed+extra (overage/power user).

### 12.1 — Usage event recording

`record_usage(space_id, user_id, event_type, metadata) → dict`. Write to the `usage_events` table on every billable action:

| Event type | Metadata | Trigger |
|------------|----------|---------|
| `agent_turn` | `{ input_tokens, output_tokens, model, duration_ms }` | Every Claude API call in the loop |
| `tool_call` | `{ tool_name, duration_ms }` | Every tool execution |
| `image_generation` | `{ model, prompt_length }` | Every Gemini call |
| `web_search` | `{ query, model }` | Every Perplexity call |
| `compaction` | `{ input_tokens, output_tokens, turns_compacted }` | Every Opus compaction call |

Wire into `agent/loop.py`: record `agent_turn` after each Claude response. Wire into `execute_tool`: record `tool_call` per execution. Wire into `image_gen.py` and `web_search`: record their respective events.

Test: run the agent loop with mocked Anthropic, verify usage events are recorded with correct event types and metadata. Test that events are recorded even when the tool call fails (track the attempt, not just success).

### 12.2 — Tier resolution

`get_user_tier(user_id) → Tier`. Query the user's subscription status from Supabase (a `subscriptions` table or auth metadata — depends on payment provider integration). Return one of three tiers:

```python
class Tier(str, Enum):
    GUEST = "guest"              # No auth, or auth but no subscription
    SUBSCRIBED = "subscribed"    # Active subscription
    EXTRA = "extra"              # Subscription + extra usage add-on
```

| | Guest | Subscribed | Extra |
|---|---|---|---|
| Messages / day | 10 | 200 | 1000 |
| Image generations / day | 0 | 20 | 100 |
| Web searches / day | 5 | 50 | 200 |
| Compaction | No (ephemeral) | Yes | Yes |
| Models | Sonnet | Sonnet + Opus (compaction) | Sonnet + Opus |
| Spaces | 1 | 10 | Unlimited |

Store limits as a config dict, not hardcoded — easy to adjust without code changes.

Test: mock subscription query. Verify correct tier returned for each case. Verify unknown/expired subscriptions resolve to `GUEST`.

### 12.3 — Quota enforcement

`check_quota(user_id, tier, event_type) → QuotaResult`. Count today's usage events for the user, compare against tier limits. Return `{ allowed: bool, remaining: int, limit: int, resets_at: datetime }`.

Wire into `main.py` before entering the agent loop:
1. Resolve tier via `get_user_tier`
2. Check `agent_turn` quota — if exhausted, return 429 with `{ error, remaining, resets_at }`
3. Pass tier into the agent loop so tool-level limits can be enforced inline

Wire into `execute_tool`:
4. Before `image_generation` or `web_search`, check the tool-specific quota
5. If exhausted, return an error result to Claude (not an HTTP error — let the agent explain the limit to the user gracefully)

Guest compaction bypass:
6. Skip compaction trigger for `GUEST` tier — their conversations are ephemeral. Conversation turns still persist for the session but won't trigger Opus calls.

Test: create usage events up to the limit, verify next request is blocked. Verify tool-level quota returns error to Claude (not HTTP 429). Verify guest skips compaction. Verify quota resets at midnight UTC.

### 12.4 — Rate limiting

Per-user request throttling independent of quotas. Prevents abuse even within quota limits.

| Tier | Requests/min | Concurrent turns/space |
|---|---|---|
| Guest | 5 | 1 |
| Subscribed | 20 | 2 |
| Extra | 60 | 5 |

Implementation: in-memory sliding window in `main.py` (no Redis needed at this scale). Return 429 with `Retry-After` header when exceeded.

Test: fire requests above the rate limit, verify 429 returned with correct `Retry-After`. Verify different tiers have different limits.

---

## Phase 13: Observability

Beyond structured logs — full request lifecycle tracing and metrics.

### 13.1 — OpenTelemetry tracing

Add `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-fastapi` to deps.

Create spans for the full request lifecycle:
- **Root span:** `POST /agent` → includes `space_id`, `user_id`, `tier`
- **Child span:** `build_system_prompt` → includes entity count, summary count
- **Child span:** `claude_api_call` (per loop iteration) → includes `model`, `input_tokens`, `output_tokens`
- **Child span:** `execute_tool` (per tool call) → includes `tool_name`, `duration_ms`, `success`
- **Child span:** `image_generation` / `web_search` → includes provider-specific metadata

Export to stdout in dev (JSON), OTLP endpoint in production (Railway supports this).

Test: run the agent loop, verify spans are created with correct parent-child relationships. Verify span attributes contain the expected metadata.

### 13.2 — Metrics endpoint

`GET /metrics` returning Prometheus-format metrics:
- `domus_agent_requests_total` (counter, labels: `tier`, `status`)
- `domus_agent_turn_duration_seconds` (histogram, labels: `model`)
- `domus_agent_tool_calls_total` (counter, labels: `tool_name`, `success`)
- `domus_agent_tokens_total` (counter, labels: `model`, `direction` [input/output])
- `domus_agent_active_connections` (gauge)

Use `prometheus-client` library. Instrument in the agent loop and tool dispatcher.

Test: trigger agent runs, scrape `/metrics`, verify counters increment and histograms record values.

---

## Phase 14: Multi-modal Input

Users send images and files in the chat, not just text.

### 14.1 — Image input (vision)

Update the `/agent` endpoint payload to accept attachments:
```json
{
  "message": "what's in this image?",
  "attachments": [
    { "type": "image", "storage_path": "uploads/{space_id}/{uuid}.png" }
  ]
}
```

Frontend uploads to Supabase Storage first, sends the `storage_path` in the payload.

In `agent/loop.py`:
1. Download image bytes from Supabase Storage
2. Base64-encode and include as an `image` content block in the Claude message
3. Claude (Sonnet) processes text + image together via vision

Test: mock Supabase Storage download + Anthropic. Send a message with an image attachment. Verify Claude receives an image content block. Verify the agent can respond about the image.

### 14.2 — File processing

Handle file attachments (PDF, CSV, plain text):
```json
{ "type": "file", "storage_path": "uploads/...", "mime_type": "application/pdf", "filename": "report.pdf" }
```

`process_attachment(storage_path, mime_type, filename) → content_block`. Download from Storage, build the appropriate Claude content block:

- **PDF:** Send as a `document` content block (base64-encoded). Claude natively parses PDFs — understands text, tables, charts, images, and layout. No `pypdf`, no external parsing. Max 100 pages, 32MB. Each page costs ~1,500–3,000 input tokens (text) + image tokens.
- **CSV:** Read raw text, include as a text block with a `[File: {filename}]` header. Claude handles tabular data natively.
- **Plain text / markdown:** Read raw text, include as a text block with a `[File: {filename}]` header.

The agent decides what to do with file content — create entities, summarize, extract structured data, answer questions. The original file stays in Supabase Storage, linked to any extracted entities via edges.

Test: mock Supabase Storage download + Anthropic. Send a PDF attachment — verify Claude receives a `document` content block (not extracted text). Send a CSV — verify it arrives as a text block. Verify the agent can create entities from file content.

---

## Phase 15: Proactive Agent

The agent acts without a user message. Scheduled tasks, reminders, triggered automations.

### 15.1 — Scheduled task entities

`scheduled_task` entity type with `presentation='hidden'` and state:
```json
{
  "cron": "0 9 * * *",
  "action": "summarize",
  "action_params": { "scope": "yesterday" },
  "next_run_at": "2026-02-16T09:00:00Z",
  "enabled": true,
  "created_by_turn_id": "uuid"
}
```

The agent creates these via `create_entity(type='scheduled_task', ...)` when the user says things like "remind me every morning" or "summarize my week on Fridays." Claude decides the cron expression and action from conversation context.

Add `croniter` to deps for cron parsing and next-run calculation.

Test: create a scheduled task entity, verify state schema. Verify `croniter` correctly computes next run times for various cron expressions.

### 15.2 — Background scheduler

A background async task running alongside FastAPI:
1. Every 60 seconds, query `scheduled_task` entities where `next_run_at <= now()` and `enabled = true`
2. For each due task, trigger an agent run: call `run_agent(space_id, user_id, synthetic_message)` where `synthetic_message` is derived from `action` + `action_params` (e.g., "Summarize what happened yesterday in this space")
3. After execution, update `next_run_at` to the next occurrence via `croniter`
4. Record a `scheduled_run` usage event

The agent run is identical to a user-triggered run — it creates entities, uses tools, streams nothing (no SSE listener). Results appear in the space as new entities.

Test: create a due scheduled task, run the scheduler tick, verify `run_agent` is called with the correct synthetic message. Verify `next_run_at` is advanced. Verify overdue tasks don't double-fire (lock mechanism).

### 15.3 — Reminder shortcut

A common case: "remind me to X at Y." The agent should:
1. Parse the reminder from conversation context (Claude does this naturally)
2. Create a `scheduled_task` with a one-shot cron (or a `run_once_at` field for non-recurring)
3. When triggered, create a visible `note` entity with the reminder text + mark the scheduled task as `enabled: false`

Test: mock the agent creating a reminder scheduled task. Trigger the scheduler, verify a note entity is created and the task is disabled.

---

## Phase 16: CI/CD & Deploy Pipeline

Ship with confidence.

### 16.1 — GitHub Actions: test & lint

`.github/workflows/ci.yml`:
- Trigger on push to `main` and all PRs
- Python 3.11, install deps, `ruff check`, `ruff format --check`, `pytest`
- Fail the PR if any step fails

Add `ruff` to dev deps. Add a `pyproject.toml` with ruff config (line length 100, target Python 3.11).

Test: verify the workflow file is valid YAML. Verify `ruff` and `pytest` pass locally before pushing.

### 16.2 — Deploy to Railway

`.github/workflows/deploy.yml`:
- Trigger on push to `main` (after CI passes)
- Use Railway CLI or GitHub integration to deploy
- Wait for health check (`GET /health` returns 200) before marking deploy as successful
- If health check fails, roll back automatically

Add `Dockerfile` (or `railway.toml` / `nixpacks` config) if not already present.

Test: verify the deploy workflow is valid. Verify `Dockerfile` builds and runs the app correctly.

### 16.3 — Environment promotion

Two Railway environments: `staging` and `production`.
- PRs auto-deploy to staging
- Merges to `main` deploy to production
- Staging uses a separate Supabase project (or the same project with a `staging` schema)
- Production deploys require health check + smoke test pass

Document the promotion flow in OPS.md.

---

## Phase 17: Guest Mode & Onboarding

The agent needs to understand guest sessions and guide new users into the product.

### 17.1 — Guest session detection

`is_guest_session(user_id) → bool`. Query Supabase Auth to determine if the user is an anonymous (guest) session. Expose as a field on the request context so the agent loop and tools can check it.

Wire into `main.py`: resolve guest status alongside tier resolution (12.2). Pass into the agent loop context.

Test: mock Supabase Auth responses for anonymous vs. authenticated users. Verify correct detection for each case.

### 17.2 — Guest interaction counting

`count_guest_interactions(user_id) → int`. Count the user's `agent_turn` and `file_processing` usage events (opens, drags, reads don't count). Compare against the guest interaction limit N.

Wire into `agent/loop.py`: after each turn completes, check the count. When the user approaches or hits the limit, inject a hint into the next system prompt so the agent prompts sign-in conversationally — not as an error, not as a modal. The agent says something like "You've been exploring a lot — sign in to keep everything you've created."

Wire into `execute_tool`: when guest limit is reached, return an error result to Claude for creation tools (same pattern as 12.3 quota enforcement). The agent explains the limit naturally.

Test: create usage events up to the limit. Verify the agent receives the sign-in hint in the system prompt. Verify creation tools return quota errors after the limit. Verify non-creation interactions (read_entity, query_entities) still work.

### 17.3 — Starter template welcome sequence

When the agent detects a brand-new space (no conversation history, space was just created), run a welcome sequence:

1. Detect: `get_recent_turns` returns empty AND space has template-seeded entities
2. Inject a one-time welcome instruction into the system prompt: "This is a new space. Greet the user, briefly explain what Domus is and what you can do. Be warm, concise, not overwhelming."
3. The agent's first response in a new space is a welcome message — not a blank prompt waiting for input

For guest spaces (sample/demo space), the welcome is slightly different: "This is a guest space. Show what's possible. Mention that signing in preserves their work."

Test: mock a new space with no turns. Verify the welcome instruction is injected. Verify it's NOT injected on subsequent turns (only the first). Test guest vs. authenticated welcome variants.

### 17.4 — Guest data re-parenting

`reparent_guest_data(anonymous_user_id, new_user_id) → dict`. When a guest signs up (first-time Google auth):

1. Query all spaces owned by `anonymous_user_id`
2. Update `spaces.user_id` and `entities.user_id` to `new_user_id`
3. Return `{ spaces_transferred, entities_transferred }`

This is called by the frontend (via a Vercel API route) after Supabase Auth confirms a new signup that had an anonymous session. The agent service exposes it as an internal endpoint (`POST /internal/reparent`, behind service auth).

Guard: only transfer if the `new_user_id` has zero existing spaces (first-time signup). If the user already has spaces (existing account, cleared cookies), do NOT merge — the anonymous data is orphaned.

Test: create entities under an anonymous user. Call reparent with a new user ID. Verify ownership transferred. Test the guard: call reparent with a user who already has spaces, verify no transfer occurs.

---

## Phase 18: Notifications & Email

The agent acts proactively (Phase 15) but needs a way to reach users who aren't in the app.

### 18.1 — Email delivery service

`send_email(to, subject, body_html, body_text) → dict`. Thin wrapper around an email API (Resend, SendGrid, or SES — pick one).

Add `EMAIL_API_KEY` and `FROM_EMAIL` to `config.py` (optional — like Perplexity/Gemini).

Return `{ message_id, status }`. Log every send with `space_id` and `user_id` correlation.

Test: mock the email API. Verify correct payload sent. Test error handling (API down, invalid address). Test graceful degradation when `EMAIL_API_KEY` is not configured.

### 18.2 — User activity tracking

`update_last_active(space_id, user_id)`. Update a `last_active_at` timestamp on the space (or user) on every agent turn.

`is_user_active(space_id, threshold_minutes=15) → bool`. Check if the user has been active within the threshold.

Wire `update_last_active` into `agent/loop.py` — call at the start of every `run_agent`.

Test: update last active, verify timestamp. Check `is_user_active` immediately (should be true). Check after threshold passes (should be false).

### 18.3 — Reminder email triggers

Wire into Phase 15's background scheduler (15.2):

1. When a scheduled task fires (reminder), check `is_user_active`
2. If the user IS active: create the reminder entity as normal (the user will see it)
3. If the user is NOT active: create the reminder entity AND send an email notification

Email content: "Reminder: {reminder_text}" with a link back to the space.

Add a `notification_sent` flag to the scheduled task state to prevent duplicate emails on retry.

Test: mock scheduler + email service. Fire a reminder with user inactive — verify email sent AND entity created. Fire with user active — verify entity created, no email. Verify duplicate prevention.

### 18.4 — Email templates

Minimal set for v1:

| Template | Trigger | Content |
|----------|---------|---------|
| `reminder` | Scheduled task fires, user inactive | Reminder text + link to space |
| `usage_warning` | User at 80% of daily quota | Current usage + what's remaining |
| `welcome` | First sign-up (not guest) | Brief intro + link to space |

Templates are plain Python string formatting — no template engine. HTML + plain text variants.

Test: render each template with sample data. Verify HTML and plain text output contain expected content. Verify links are correctly formatted.

---

## Deferred (build after above phases)

| What | Depends on |
|------|-----------|
| Dynamic schema injection | Schema discovery (11.2) + multiple app types with schemas existing |
| Payment provider integration | Tier system (12.2) — Stripe/Lemon Squeezy webhook → `subscriptions` table |
| Cross-space agent memory | Memory system (Phase 6) — facts that persist across spaces for the same user |
| Agent eval suite | CI pipeline (16.1) — golden conversations, automated prompt regression testing |
| Multi-model routing | Usage tracking (12.1) — route by complexity, cost, or user tier |
