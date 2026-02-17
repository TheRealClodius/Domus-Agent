# Domus Agent — Ops

How to run, test, and deploy the agent service. For system-wide architecture, see `.../Documents/Projects/Domus/docs/`.

---

## Runtime

- **Python:** 3.11+ (floor set by NetworkX 3.6)
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
| `networkx` | >=3.6 | Entity graph operations |

**Dev:**

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=9.0 | Test runner (async support) |

---

## Environment Variables

| Variable | Where | Purpose |
|----------|-------|---------|
| `DOMUS_SERVICE_TOKEN` | Railway + Vercel | Shared secret for service-to-service auth |
| `SUPABASE_URL` | Railway | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Railway | Supabase service role key (bypasses RLS for agent writes) |
| `ANTHROPIC_API_KEY` | Railway | Claude API key |
| `GOOGLE_API_KEY` | Railway | Gemini API key (image generation) |
| `PERPLEXITY_API_KEY` | Railway | Perplexity API key (web search) |

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

## Usage Tracking

The agent service logs usage events to the `usage_events` table on every tool execution:

| Event type | Trigger |
|-----------|---------|
| `agent_turn` | Each agent loop invocation |
| `image_generation` | Each Gemini generate call |
| `image_edit` | Each Gemini edit call |
| `file_processing` | Each file sent to Claude for parsing |
| `web_search` | Each Perplexity API call |

Uses the Supabase service role key (not user auth) to insert usage events.
