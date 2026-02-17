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

# Optional — deferred tools
GOOGLE_API_KEY: str = os.environ.get("GOOGLE_API_KEY", "")
PERPLEXITY_API_KEY: str = os.environ.get("PERPLEXITY_API_KEY", "")


async def acreate_client():
    """Create an async Supabase client with service role key (bypasses RLS)."""
    from supabase._async.client import create_client

    return await create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def create_anthropic_client():
    """Create an Anthropic AsyncAnthropic client."""
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=ANTHROPIC_API_KEY, max_retries=5)
