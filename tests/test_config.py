"""Tests for config.py — environment-based configuration."""

import importlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

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
        mock_client = MagicMock()
        mock_supabase_async = MagicMock()
        mock_supabase_async.create_client = AsyncMock(return_value=mock_client)
        with patch.dict(sys.modules, {
            "supabase._async": MagicMock(),
            "supabase._async.client": mock_supabase_async,
        }):
            client = await config.acreate_client()
        assert client is not None

    def test_anthropic_client_factory_returns_client(self):
        config = _reload_config(_VALID_ENV)
        client = config.create_anthropic_client()
        assert client is not None


class TestSharedClients:
    """config.py must expose a shared-client singleton pattern."""

    def test_get_shared_anthropic_raises_before_init(self):
        """get_shared_anthropic_client raises RuntimeError when not yet initialized."""
        import config
        config._shared_anthropic_client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            config.get_shared_anthropic_client()

    def test_get_shared_supabase_raises_before_init(self):
        """get_shared_supabase_client raises RuntimeError when not yet initialized."""
        import config
        config._shared_supabase_client = None
        with pytest.raises(RuntimeError, match="not initialized"):
            config.get_shared_supabase_client()

    def test_set_and_get_shared_clients(self):
        """set_shared_clients stores clients; getters return the same objects."""
        import config
        mock_a, mock_s = MagicMock(), MagicMock()
        config.set_shared_clients(mock_a, mock_s)
        assert config.get_shared_anthropic_client() is mock_a
        assert config.get_shared_supabase_client() is mock_s

    def test_anthropic_client_has_timeout(self):
        """create_anthropic_client must configure an httpx Timeout."""
        config = _reload_config(_VALID_ENV)
        client = config.create_anthropic_client()
        assert client.timeout is not None
