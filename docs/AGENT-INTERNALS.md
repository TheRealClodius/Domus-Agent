# Domus Agent — Internals

How the agent service works. For system-wide architecture, data model, and API contract, see `domus-web/docs/ARCHITECTURE.md`.

---

## Agent Loop (`agent/loop.py`)

A `while True` loop using the Anthropic SDK directly. No framework.

1. `context.py` assembles a lightweight system prompt (entity index, relevant schemas, personality, recent turns)
2. Claude (Sonnet) processes the message + system prompt
3. If Claude emits tool calls → execute in parallel → append results → loop
4. If Claude emits only text → save conversation turn → exit loop
5. After exit: check turn count, trigger compaction if >40 turns

**Model usage:**
- `claude-sonnet-4-5-20250929` — interactive turns (fast, cheap, reliable tool use)
- `claude-opus-4-6` — memory compaction only (better summarization over long context)

**Streaming:** The loop streams SSE events to the frontend via `on_event` callback. Text deltas and tool call results (including created/updated entities) flow through immediately — the frontend doesn't wait for CDC.

---

## System Prompt (`agent/context.py`)

The system prompt is thin. The agent discovers details on demand via tool calls.

**Always included:**
- Entity index: `[id] type (presentation, z:z_index) — summary` for all non-archived entities (including hidden)
- Personality traits: all `personality_trait` entities in the space
- Recent turns: last 3–5 `conversation_turn` entities

**Dynamically injected:**
- App schemas (1–3): only types relevant to this turn, determined by message content + visible entity types
- Builder prompt (`prompts/builder.py`): injected when user intent implies composed app creation OR focused entity has `state.blocks`

**Not in the system prompt** (agent queries on demand):
- Full entity state → `read_entity`
- Graph edges → `query_entities(type='edge')`
- Facts → `query_entities(type='fact', search='...')`
- Conversation summaries → `query_entities(type='conversation_summary')`
- All app schemas → only relevant ones injected; agent discovers others via tool calls

---

## Five Tools (`agent/tools.py`)

| Tool | What it does |
|------|-------------|
| `create_entity` | Create any entity (notes, calendar events, images, composed apps, edges, facts) |
| `update_entity` | Patch entity state via RFC 7396 JSON Merge Patch |
| `query_entities` | Search/filter entities — returns lightweight summaries (id, type, summary) |
| `read_entity` | Get one entity's full state by ID |
| `web_search` | Search the web via Perplexity API — returns sourced answers with citations |

If you're tempted to add a sixth tool, you're doing something wrong.

**State merge semantics (RFC 7396):**
- Provided scalar fields overwrite
- Provided object fields merge recursively
- `null` deletes the key
- Arrays are always replaced entirely (agent does read-modify-write to append)
- Omitted fields are preserved

**Validation:** `tools.py` validates entity state against the app's JSON schema (fetched from `domus-web/api/schemas`) before writing to Postgres. Composed apps with `state.blocks` are validated per-block against required fields.

---

## Image Generation (`agent/image_gen.py`)

Uses Gemini, called as a backend service from `tools.py`. Not part of the agent loop.

**Model:** `gemini-2.5-flash-image` (GA, $0.039/image, 1024x1024 max)
**SDK:** `google-genai` (`client.models.generate_content()`)

**Three intents** (Claude decides from conversation context):

| Intent | Trigger | Gemini call |
|--------|---------|-------------|
| Generate | `create_entity(type='image', state={ generation_prompt })` | `generate_content([prompt])` — text only |
| Edit | `update_entity(id, state={ edit_prompt })` | `generate_content([edit_prompt, current_image])` — text + image |
| Inspire | `create_entity(type='image', state={ generation_prompt, reference_entity_ids })` | `generate_content([prompt, ref_images...])` — text + reference images |

**Pipeline (all in-memory):**
```
Supabase Storage → download bytes → BytesIO → PIL Image.open()
  → Gemini generate_content → response.parts[0].as_image()
  → PIL Image → BytesIO → Supabase Storage upload → store URL in entity state
```

Claude manages multi-turn editing context. After ~5 edits (cumulative quality degradation), Claude regenerates from scratch with a comprehensive prompt.

---

## Web Search (`tools.py`)

Calls Perplexity API (`POST https://api.perplexity.ai/chat/completions`) via `httpx`. Returns sourced answers with citations. The agent creates entities from results — search results themselves are ephemeral.

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

| Entity type | Purpose |
|-------------|---------|
| `conversation_turn` | A single user or assistant message |
| `conversation_summary` | Compressed summary of N turns |
| `fact` | Something the agent learned about the user |
| `personality_trait` | How the agent should behave |
| `edge` | Relationship between two entities |

**Compaction** (triggered when turn count >40):
1. Take turns beyond the recent window
2. Call Opus: "Summarize this conversation segment. Extract any facts about the user."
3. Create `conversation_summary` entity
4. Create `fact` entities for new facts
5. Create `edge` entities for discovered relationships
6. Archive original turns (`archived: true`)

**No embeddings.** Recency + full-text search + knowledge graph.

---

## Composed Apps (`agent/prompts/builder.py`)

The agent creates app types on the fly using declarative composition — block-based specs as entity state, interpreted by a generic block renderer on the frontend.

**Iteration protocol (no new tools):**
1. Plan — Claude's reasoning decides what blocks to create
2. Execute — `create_entity` with initial blocks
3. Verify — `read_entity` to check what was created
4. Extend — `update_entity` with full updated blocks array (read-modify-write)
5. Loop until complete

The builder prompt (~30–40 lines) is injected by `context.py` only when composing. Contains: block primitive reference, iteration protocol, validation rules, compact example.

---

## Concurrent Turns

The agent does not stop when the user sends a new message mid-turn.

| Scenario | Behavior |
|----------|----------|
| User sends message while agent is idle | Normal turn |
| User sends message while agent is working | Queued. Delivered after current tool call completes. |
| User sends "stop" / "cancel" | Streaming terminated. Committed DB writes persist. |
| User sends modification ("also change...") | Delivered as follow-up. Agent modifies plan without restarting. |

FastAPI manages a per-space message queue. The loop checks for new queued messages between tool call cycles.

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

App schemas are served by the frontend (`GET domus-web/api/schemas`). The agent service fetches on startup and caches in-memory. Schemas are always current truth — no build step, no manual copy.

---

## Service Authentication

The agent service is not publicly accessible. Vercel's SSE proxy is the only entry point.

1. **User auth:** Vercel validates Supabase auth cookie → extracts `user_id`
2. **Service auth:** Vercel forwards with `Authorization: Bearer <DOMUS_SERVICE_TOKEN>`

The agent trusts `user_id` and `space_id` from the payload because Vercel already validated the user. RLS provides defense-in-depth.
