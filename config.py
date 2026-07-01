"""Environment-based configuration. Loads from .env in dev, env vars in production."""

import os

from dotenv import load_dotenv

load_dotenv()

_REQUIRED = [
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "ANTHROPIC_API_KEY",
    "DOMUS_SERVICE_TOKEN",
]

_missing = [v for v in _REQUIRED if not os.environ.get(v)]
if _missing:
    raise RuntimeError(f"Missing required environment variables: {', '.join(_missing)}")

SUPABASE_URL: str = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY: str = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
DOMUS_SERVICE_TOKEN: str = os.environ["DOMUS_SERVICE_TOKEN"]

# Optional — admin/observability token (local dev tooling only, not required in prod)
DOMUS_ADMIN_TOKEN: str = os.environ.get("DOMUS_ADMIN_TOKEN", "")

# Optional — Supabase JWT secret for verifying user tokens forwarded by the proxy.
# When set, the agent verifies X-User-Token headers and asserts user_id matches the JWT sub.
# Absent → payload trust (safe for local dev, no breaking change).
SUPABASE_JWT_SECRET: str = os.environ.get("SUPABASE_JWT_SECRET", "")

# Optional — deferred tools
GOOGLE_API_KEY: str = os.environ.get("GOOGLE_API_KEY", "")
PERPLEXITY_API_KEY: str = os.environ.get("PERPLEXITY_API_KEY", "")

DOMUS_FRONTEND_URL: str = os.environ.get("DOMUS_FRONTEND_URL", "http://localhost:3000")

# Model selection — override via .env to switch without code changes
AGENT_MODEL: str = os.environ.get("AGENT_MODEL", "claude-sonnet-5")
BUILDER_MODEL: str = os.environ.get("BUILDER_MODEL", "claude-sonnet-5")
IMAGE_GEN_MODEL: str = os.environ.get("IMAGE_GEN_MODEL", "gemini-3.1-flash-image")
COMPACTION_MODEL: str = os.environ.get("COMPACTION_MODEL", "claude-opus-4-6")

# Memory compaction — trigger when accumulated turns exceed heuristic
COMPACTION_TURN_THRESHOLD: int = 40

# Debug — expose /debug/prompt endpoint (off by default, never enable in prod)
DEBUG_PROMPT_ENABLED: bool = os.getenv("DEBUG_PROMPT_ENABLED", "false").lower() == "true"

# UI action mirroring — when enabled, agent emits ui_action SSE events for visible
# entity mutations instead of writing directly. Frontend executes through its UI
# state machine and POSTs results back to /agent/action-result.
UI_ACTION_MIRRORING: bool = os.getenv("UI_ACTION_MIRRORING", "false").lower() == "true"

# Timeout (seconds) for frontend to respond to a ui_action before falling
# back to direct server-side execution.
UI_ACTION_TIMEOUT_SECONDS: float = float(os.getenv("UI_ACTION_TIMEOUT_SECONDS", "15.0"))

# ---------------------------------------------------------------------------
# Billing tier limits (daily quotas per event_type)
# ---------------------------------------------------------------------------

TIER_LIMITS: dict = {
    "free":    {"agent_turn": 10,   "image_generation": 0,   "web_search": 5},
    "citizen": {"agent_turn": 200,  "image_generation": 20,  "web_search": 50},
    "extra":   {"agent_turn": 1000, "image_generation": 100, "web_search": 200},
}

RATE_LIMITS: dict = {
    "free":    {"requests_per_minute": 5,  "concurrent_turns": 1},   # concurrent_turns: defined, not yet enforced
    "citizen": {"requests_per_minute": 20, "concurrent_turns": 2},
    "extra":   {"requests_per_minute": 60, "concurrent_turns": 5},
}


# ---------------------------------------------------------------------------
# Client factories
# ---------------------------------------------------------------------------


async def acreate_client():
    """Create an async Supabase client with service role key (bypasses RLS)."""
    from supabase._async.client import create_client

    return await create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def create_anthropic_client():
    """Create an Anthropic AsyncAnthropic client with timeout and reduced retries."""
    import httpx
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(
        api_key=ANTHROPIC_API_KEY,
        max_retries=3,  # Reduced from 5; RateLimitError is handled explicitly in the loop
        timeout=httpx.Timeout(60.0, connect=10.0),
    )


# ---------------------------------------------------------------------------
# Shared client singleton (initialized once at app startup via lifespan)
# ---------------------------------------------------------------------------

_shared_anthropic_client = None
_shared_supabase_client = None


def set_shared_clients(anthropic_client, supabase_client) -> None:
    """Store the shared clients created during app startup."""
    global _shared_anthropic_client, _shared_supabase_client
    _shared_anthropic_client = anthropic_client
    _shared_supabase_client = supabase_client


def get_shared_anthropic_client():
    """Return the shared Anthropic client. Raises if not initialized."""
    if _shared_anthropic_client is None:
        raise RuntimeError("Shared Anthropic client not initialized")
    return _shared_anthropic_client


def get_shared_supabase_client():
    """Return the shared Supabase client. Raises if not initialized."""
    if _shared_supabase_client is None:
        raise RuntimeError("Shared Supabase client not initialized")
    return _shared_supabase_client
