# Revised frontend plan for Agent UI Mirroring — Phase 2 (ui_action protocol)

The agent will no longer write visible entities to Supabase directly. Instead, the SSE stream will include a new event type `ui_action`:

```json
{
  "type": "ui_action",
  "action_id": "act_a1b2c3d4",
  "action": "create_entity",
  "params": { "type": "note", "content": "...", "state": {}, "presentation": "window" }
}
```

And two fire-and-forget events for attention state:

```json
{ "type": "agent_attention", "entity_id": "abc-123", "intent": "reading" | "editing" }
{ "type": "agent_attention_clear" }
```

## What changes from your current plan:

### 1. Trigger source changes.
Your `agentActionInterpreter` no longer listens to `tool_call_start` / `tool_call_result` for entity mutations. It listens to `ui_action` events instead. The action and params are explicit — no need to infer intent from raw tool args.

### 2. The frontend is the sole writer.
When a `ui_action` arrives, the interpreter routes it through the same code path as user interactions (entity store mutations, animations, selection). After the action completes (or fails), the frontend POSTs the result back:

```
POST /agent/action-result
{
  "action_id": "act_a1b2c3d4",
  "space_id": "...",
  "user_id": "...",
  "success": true,
  "result": { "id": "entity-uuid", "type": "note", ... }
}
```

The agent loop is blocked waiting for this callback before continuing to the next Claude turn. This is what makes it sequential and race-free — no CDC suppression needed for mirrored entities.

### 3. CDC suppression simplifies.
You may still want a brief suppression window for the entity the frontend just wrote (since the frontend's own Supabase write will trigger CDC back to itself), but it's deterministic now — you control when the write happens, so you know exactly which CDC event to ignore. No more racing between two independent writers.

### 4. `tool_call_start` / `tool_call_result` still exist
For non-mirrored operations (reads, web search, internal entities). Your conversation store dispatching (`startToolCall`, `resolveToolCall`) stays the same for those. Only entity mutations move to the `ui_action` path.

### 5. `agent_attention` events
Replace the focus management you had in `interpretToolCallResult`. The agent emits these before it acts (when reading or about to edit an entity), giving the frontend a signal to show the orange attention ring proactively, not just after the fact.

### 6. Error/timeout contract.
If the frontend can't execute the action, it posts back `{ success: false, error: "..." }`. If the frontend never responds (disconnect, crash), the agent times out after 15s and falls back to direct Supabase write as a safety net. Your interpreter should handle this gracefully — if a `tool_call_result` arrives for an entity the interpreter didn't create via `ui_action`, treat it as a fallback and upsert normally.

### 7. Animation queue stays.
Your `AgentActionQueue` with sequential drain for spatial animations is still the right pattern. The trigger is just `ui_action` events instead of tool call events. The queue executes the animation, performs the Supabase write, and then fires the callback POST.
