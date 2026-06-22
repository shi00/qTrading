"""Tests for db_url_override context manager."""

import os

import config
from data.persistence.db_url_override import override_db_url
import pytest


pytestmark = pytest.mark.unit


class TestOverrideDbUrl:
    """Test cases for override_db_url context manager."""

    def test_override_and_restore_basic(self, monkeypatch) -> None:
        """Test basic override and restoration of DB_URL."""
        original_url = "postgresql+asyncpg://user:pass@host/original"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        # Set initial state via monkeypatch for automatic cleanup
        monkeypatch.setattr(config, "DB_URL", original_url)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with override_db_url(target_url):
            assert target_url == config.DB_URL
            assert os.environ.get("DATABASE_URL") == target_url

        # After context, should restore
        assert original_url == config.DB_URL
        assert "DATABASE_URL" not in os.environ

    def test_restore_existing_env_var(self, monkeypatch) -> None:
        """Test restoring existing DATABASE_URL environment variable."""
        original_url = "postgresql+asyncpg://user:pass@host/original"
        existing_env_url = "postgresql://user:pass@host/env"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.setattr(config, "DB_URL", original_url)
        monkeypatch.setenv("DATABASE_URL", existing_env_url)

        with override_db_url(target_url):
            assert target_url == config.DB_URL
            assert os.environ.get("DATABASE_URL") == target_url

        # After context, should restore to existing_env_url
        assert original_url == config.DB_URL
        assert os.environ.get("DATABASE_URL") == existing_env_url

    def test_delete_env_var_when_not_originally_present(self, monkeypatch) -> None:
        """Test deleting DATABASE_URL if it wasn't set before entering context."""
        original_url = "postgresql+asyncpg://user:pass@host/original"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.setattr(config, "DB_URL", original_url)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with override_db_url(target_url):
            # Context sets the env var
            assert os.environ.get("DATABASE_URL") == target_url

        # After exiting, env var should be deleted (not restored to None)
        assert "DATABASE_URL" not in os.environ

    def test_override_with_none_original_db_url(self, monkeypatch) -> None:
        """Test override when original DB_URL is None."""
        target_url = "postgresql+asyncpg://user:pass@host/target"

        # Set DB_URL to None via monkeypatch for automatic cleanup
        monkeypatch.setattr(config, "DB_URL", None)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with override_db_url(target_url):
            assert target_url == config.DB_URL
            assert os.environ.get("DATABASE_URL") == target_url

        # After context, should restore to None
        assert config.DB_URL is None
        assert "DATABASE_URL" not in os.environ
