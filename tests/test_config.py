"""Tests for config.py — environment-based configuration."""

import importlib
import os
from unittest.mock import patch

import pytest


_VALID_ENV = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "test-key",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "DOMUS_SERVICE_TOKEN": "test-token",
}


def _reload_config(env: dict):
    """Reload config module with given env, mocking dotenv so .env file doesn't interfere."""
    with patch.dict(os.environ, env, clear=True), patch("dotenv.load_dotenv"):
        import config

        importlib.reload(config)
        return config


class TestConfigLoadsEnvVars:
    """Config should load required env vars from .env in dev, env vars in production."""

    def test_loads_required_vars(self):
        config = _reload_config(_VALID_ENV)

        assert config.SUPABASE_URL == "https://example.supabase.co"
        assert config.SUPABASE_SERVICE_ROLE_KEY == "test-key"
        assert config.ANTHROPIC_API_KEY == "sk-ant-test"
        assert config.DOMUS_SERVICE_TOKEN == "test-token"

    def test_fails_fast_on_missing_required_var(self):
        """Should raise on startup if a required var is missing."""
        env = {
            "SUPABASE_URL": "https://example.supabase.co",
            # Missing SUPABASE_SERVICE_ROLE_KEY
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "DOMUS_SERVICE_TOKEN": "test-token",
        }
        with pytest.raises(RuntimeError, match="SUPABASE_SERVICE_ROLE_KEY"):
            _reload_config(env)

    def test_optional_vars_default_to_empty_string(self):
        config = _reload_config(_VALID_ENV)

        assert config.GOOGLE_API_KEY == ""
        assert config.PERPLEXITY_API_KEY == ""


class TestClientFactories:
    """Config should expose async client factories for Supabase and Anthropic."""

    def test_supabase_client_factory_exists(self):
        config = _reload_config(_VALID_ENV)
        assert callable(config.acreate_client)

    def test_anthropic_client_factory_exists(self):
        config = _reload_config(_VALID_ENV)
        assert callable(config.create_anthropic_client)

    @pytest.mark.asyncio
    async def test_supabase_client_factory_returns_client(self):
        config = _reload_config(_VALID_ENV)
        client = await config.acreate_client()
        assert client is not None

    def test_anthropic_client_factory_returns_client(self):
        config = _reload_config(_VALID_ENV)
        client = config.create_anthropic_client()
        assert client is not None
