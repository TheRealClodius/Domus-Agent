# Review: Latest merge on `main` (`d791753`)

## Scope reviewed

Merge commit `d791753` introduces UI action mirroring for entity mutations, an action-result callback endpoint, bridge lifecycle handling in the agent loop, and expanded tests.

Files in scope:
- `agent/action_bridge.py`
- `agent/tools.py`
- `agent/loop.py`
- `main.py`
- `config.py`
- tests in `tests/test_action_bridge.py`, `tests/test_tools.py`, `tests/test_loop.py`, `tests/test_endpoint.py`

## Overall assessment

This is a strong functional step forward: it creates the foundations for frontend-coordinated entity mutations and visible agent presence while preserving server-side fallback behavior.

The main risks are not feature-level correctness, but reliability and operability at scale:
- in-memory bridge state across workers,
- fixed timeout handling and duplicated timeout logic,
- missing correlation/idempotency guarantees,
- limited observability and metrics around mirror path health.

## What is working well

1. **Clean bridge abstraction**
   - `ActionBridge` keeps pending actions simple and future-based.
   - Registry APIs are explicit and easy to reason about.

2. **Thoughtful fallback behavior**
   - When mirroring times out, direct execution fallback preserves continuity.

3. **Preprocessing before mirror emission**
   - Server-side image generation and app payload preparation occur before emitting `ui_action`, keeping frontend payloads deterministic.

4. **Lifecycle hygiene in the loop**
   - Bridge register/unregister is wrapped in the turn lifecycle with `finally` cleanup.

5. **Good initial tests**
   - Tests cover bridge basics, endpoint integration, attention event emission, and non-bridge compatibility.

## Improvement opportunities (prioritized)

### P0 — Cross-process reliability for action callbacks

**Issue**: Active bridges are stored in-process (`_active_bridges`). In a multi-worker deployment, `/agent` SSE and `/agent/action-result` can land on different workers, causing spurious 404 (`No active agent turn`).

**Recommendation**:
- Replace or augment in-memory registry with a shared coordination layer (Redis recommended).
- Store action state and result channels in Redis keyed by `(space_id, user_id, turn_id, action_id)`.
- Keep local futures for fast path, but use pub/sub or polling bridge for cross-worker delivery.

**Outcome**: Eliminates worker-affinity bugs and enables horizontal scaling.

### P0 — Introduce turn-scoped correlation + idempotency

**Issue**: `action_id` uniqueness is strong, but callback payload lacks explicit `turn_id` and idempotency semantics.

**Recommendation**:
- Add `turn_id` to emitted `ui_action` event and callback request.
- Validate `turn_id` when resolving.
- Persist a short-lived callback receipt ledger for idempotent retries.

**Outcome**: Prevents misrouting and hardens retried frontend callbacks.

### P1 — Unify timeout behavior and make it configurable

**Issue**: `execute_tool` currently waits directly on `action.future` with a hard-coded 15s timeout, while `ActionBridge.wait_for_result` also has timeout behavior. This duplicates logic and makes tests/ops tuning harder.

**Recommendation**:
- Route waits through `bridge.wait_for_result(...)` only.
- Add `UI_ACTION_TIMEOUT_SECONDS` in `config.py`.
- Emit timeout metric with tool/action labels.

**Outcome**: Single timeout source of truth, easier testability and safer rollout tuning.

### P1 — Improve observability for mirror path

**Issue**: Logging exists for timeout fallback, but lacks complete mirror path telemetry.

**Recommendation**:
- Add structured logs + metrics for:
  - `ui_action_emitted`
  - `ui_action_resolved`
  - `ui_action_failed`
  - `ui_action_timeout`
  - `ui_action_fallback_direct`
  - end-to-end latency histograms
- Include `space_id`, `user_id` (or hashed), `tool`, `action_id`, `turn_id`.

**Outcome**: Faster diagnosis and reliable SLO monitoring.

### P1 — Security and trust boundaries on callback endpoint

**Issue**: Endpoint is service-token protected, but still trusts request body identity fields.

**Recommendation**:
- Verify that callback identity matches a currently active action envelope (space/user/turn/action).
- Reject callbacks with mismatched tuple even when `action_id` exists.
- Add replay window TTL and nonce/idempotency key support.

**Outcome**: Better defensive posture and reduced accidental/malicious misresolution.

### P2 — Clarify mirroring policy and unsupported tools

**Issue**: Mirrored tool set is hardcoded and implicit.

**Recommendation**:
- Centralize mirroring policy in a dedicated strategy function with explicit rationale per tool.
- Add tests to assert mirrored vs non-mirrored matrix.

**Outcome**: Easier maintenance as tool surface grows.

### P2 — Expand test matrix for operational edge cases

**Missing scenarios**:
- callback arrives after fallback direct execution,
- duplicate callback delivery,
- callback with wrong `space_id/user_id` for same `action_id`,
- bridge cleanup with pending unresolved futures,
- concurrent mirrored actions in same turn.

**Recommendation**:
- Add targeted async tests for these edge cases.

**Outcome**: Better confidence under real network and retry behavior.

## Recommended implementation plan (full scope)

### Phase 1 — Stabilize protocol (1–2 days)
1. Add `turn_id` to emitted `ui_action` and callback schema.
2. Add configurable `UI_ACTION_TIMEOUT_SECONDS`.
3. Refactor `execute_tool` to use `bridge.wait_for_result` exclusively.
4. Add explicit mirror-path logs and timeout metrics.

### Phase 2 — Harden reliability (2–4 days)
1. Introduce Redis-backed action registry + result relay.
2. Implement callback idempotency ledger (short TTL).
3. Add validation for `(space_id, user_id, turn_id, action_id)` tuple.

### Phase 3 — Validate at scale (1–2 days)
1. Add edge-case tests listed above.
2. Run load scenario with multiple workers and synthetic callback delays.
3. Define SLOs for mirror success rate and timeout fallback rate.

### Phase 4 — Rollout safety
1. Keep `UI_ACTION_MIRRORING` behind feature flag.
2. Deploy with canary cohorts.
3. Gate promotion on timeout/fallback/error thresholds.

## Suggested success criteria

- <1% mirror timeout fallback rate during steady-state.
- 0 cross-worker callback misses in multi-worker load test.
- 100% callback idempotency under retried delivery test.
- End-to-end mirrored action p95 latency tracked and within target.

## Bottom line

The merge is directionally correct and well-structured for an initial version. The biggest improvement is shifting from process-local coordination to protocol-grade, turn-scoped, cross-worker-safe action resolution with stronger observability.
