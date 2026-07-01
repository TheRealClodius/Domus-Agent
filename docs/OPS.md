# Domus Agent — Ops

How to run, test, and deploy the agent service. For system-wide architecture, see `.../Documents/Projects/Domus/docs/`.

---

## Runtime

- **Python:** 3.11+
- **Framework:** FastAPI + Uvicorn
- **Deploy:** Railway (persistent process, not serverless)

---

## Dependencies

**Runtime:**

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | >=0.129 | API framework |
| `uvicorn[standard]` | >=0.40 | ASGI server |
| `anthropic` | >=0.79 | Claude SDK (agent loop) |
| `google-genai` | >=1.63 | Image generation (Gemini) |
| `Pillow` | >=11.0 | Image processing (PNG validation, dimensions) |
| `httpx` | >=0.28 | Perplexity API calls |
| `supabase` | >=2.28 | DB client (async via `acreate_client()`) |

**Dev:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=9.0 | Test runner |
| `pytest-asyncio` | >=0.25 | Async test support (`@pytest.mark.asyncio`) |

---

## Environment Variables

**Required:**

| Variable | Where | Purpose |
|----------|-------|---------|
| `DOMUS_SERVICE_TOKEN` | Railway + Vercel | Shared secret for service-to-service auth |
| `SUPABASE_URL` | Railway | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Railway | Supabase service role key (bypasses RLS for agent writes) |
| `ANTHROPIC_API_KEY` | Railway | Claude API key |

**Optional (features degrade gracefully when absent):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOOGLE_API_KEY` | `""` | Gemini API key — image generation disabled if missing |
| `PERPLEXITY_API_KEY` | `""` | Perplexity API key — web search returns `unavailable` if missing |
| `DOMUS_FRONTEND_URL` | `http://localhost:3000` | Frontend base URL for schema/tool-call proxying |
| `SUPABASE_JWT_SECRET` | `""` | Supabase JWT secret — when set, agent verifies `X-User-Token` header and asserts `sub` matches `user_id`. Absent = payload trust (safe for local dev). |
| `DOMUS_ADMIN_TOKEN` | `""` | Token for admin/observability endpoints (`/admin/domus-context`, `/admin/builder-context`). Required to use those endpoints; separate from service token. |

**Model overrides (optional):**

Set in `.env` locally or in Railway env vars to switch models without code changes.

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENT_MODEL` | `claude-sonnet-5` | Main agent loop model |
| `BUILDER_MODEL` | `claude-sonnet-5` | Declarative app builder model |
| `IMAGE_GEN_MODEL` | `gemini-3.1-flash-image` | Gemini image generation model |
| `COMPACTION_MODEL` | `claude-opus-4-6` | Memory compaction model |

**Feature flags:**

| Variable | Default | Purpose |
|----------|---------|---------|
| `UI_ACTION_MIRRORING` | `false` | Route visible entity mutations through frontend UI state machine |
| `UI_ACTION_TIMEOUT_SECONDS` | `15.0` | Seconds to wait for frontend callback before falling back to direct execution |

**Debug (never enable in prod):**

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEBUG_PROMPT_ENABLED` | `false` | Enables `POST /debug/prompt` endpoint |

Store in Railway's environment variables. Local dev uses `.env`.

---

## Supabase CLI

Both repos share one Supabase project (**Fram Design org → Domos project**). The CLI is available for migrations, seeding, and direct DB access.

```bash
# Install
brew install supabase/tap/supabase

# Link to existing project
supabase link --project-ref pffhflsnswotnedrtbbi

# Run SQL against remote
supabase db push

# Dump schema
supabase db dump --schema public
```

Use the CLI for running `001_init.sql`, seeding test data, and debugging schema issues. The dashboard works too — the CLI is faster for scripted/repeatable operations.

---

## Local Development

```bash
# Create venv
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy env template
cp .env.example .env
# Fill in API keys

# Run
uvicorn main:app --reload --port 8000
```

---

## Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_tools.py
```

Tests use pytest with async support. Fixtures for Supabase client mocking and tool execution.

---

## Deployment

Railway auto-deploys from the `main` branch. Railpack detects Python + FastAPI, installs from `requirements.txt`, and runs `uvicorn main:app --host 0.0.0.0 --port $PORT`. Railway provides the `PORT` env var.

---

## CI

GitHub Actions on every push:
- Lint (if configured)
- `pytest` — all tests must pass

Separate from the domus-web CI pipeline.

---

## Health Check

Railway pings the health endpoint. FastAPI exposes:

```
GET /health → { "status": "ok" }
```

---

## Logging

Structured JSON logs via `agent/logging.py`. Activated at startup in `main.py` via `setup_logging()`. Modules use `get_logger("agent.<module>")` for named loggers. Include `space_id` and `user_id` as correlation fields for tracing agent turns through tool calls. Tool executions are logged via `log_tool_execution()` with timing data.

---

## Usage Tracking & Billing Tiers

`agent/usage.py` implements the full billing stack: tier resolution, quota enforcement, rate limiting, and event recording.

### Tiers

Resolved from `users.plan` at request time, cached 5 minutes in-process:

| `users.plan` value | Resolved tier | Agent turns/day | Images/day | Searches/day | RPM |
|--------------------|--------------|----------------|------------|--------------|-----|
| `null` / unknown | `FREE` | 10 | 0 | 5 | 5 |
| `'citizen'` | `CITIZEN` | 200 | 20 | 50 | 20 |
| `'extra'` | `EXTRA` | 1000 | 100 | 200 | 60 |

### Request gates (enforced in `main.py` before streaming)

1. Tier resolution → `get_user_tier()`
2. Rate limit → `check_rate_limit()` — in-memory sliding window, returns `429 + Retry-After` if over RPM
3. Daily quota → `check_quota()` for `agent_turn` — returns `429 + resets_at` if exhausted

### Recorded events

| Event type | Recorded in | Notes |
|------------|-------------|-------|
| `agent_turn` | `loop.py` | After each Anthropic call; includes token counts |
| `tool_call` | `tools.py` | After every `execute_tool`; includes tool name, duration, success |
| `image_generation` | `image_gen.py` | After Gemini upload; quota also checked before call |
| `compaction` | `memory.py` | After Opus compaction call; includes token counts |

All inserts are fire-and-forget via `_bg()` (module-level helper in `loop.py`). `record_usage()` never raises.

### Required Supabase migration (D-2)

The `usage_events` table must exist before deploying Phase 12:

```sql
CREATE TABLE usage_events (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  space_id UUID NOT NULL,
  event_type TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX usage_events_user_type_day
  ON usage_events (user_id, event_type, created_at DESC);
```

RLS: users read own rows; service role inserts (agent uses service role key).
