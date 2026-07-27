"""Tests for db_url_override context manager.

P3-M4-DbUrlOverride-Mock-In-Prod: tests verify the new ContextVar-based
override behavior. The override is async-safe thread-local — visible only
in the calling thread / asyncio task and tasks spawned from it via
ThreadPoolManager.run_async() (which propagates contextvars). Concurrent
calls from unrelated threads are unaffected.
"""

import asyncio
import os
import threading

import pytest

import config
from data.persistence.db_url_override import override_db_url
from utils.config_handler import ConfigHandler

pytestmark = pytest.mark.unit


class TestOverrideDbUrl:
    """Test cases for override_db_url context manager."""

    def test_override_returns_target_url_within_context(self, monkeypatch) -> None:
        """Within the context, ConfigHandler.get_db_url() returns the target URL."""
        target_url = "postgresql+asyncpg://user:pass@host/target"

        # Ensure baseline state: no env var, no config.DB_URL interference
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_URL", "postgresql+asyncpg://user:pass@host/original")

        with override_db_url(target_url):
            assert ConfigHandler.get_db_url() == target_url

    def test_override_restores_after_context(self, monkeypatch) -> None:
        """After the context, get_db_url() returns the original value (config.DB_URL)."""
        original_url = "postgresql+asyncpg://user:pass@host/original"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_URL", original_url)

        # Before context: get_db_url falls through to config.DB_URL
        assert ConfigHandler.get_db_url() == original_url

        with override_db_url(target_url):
            assert ConfigHandler.get_db_url() == target_url

        # After context: override is cleared, falls through to config.DB_URL
        assert ConfigHandler.get_db_url() == original_url

    def test_override_does_not_mutate_config_db_url(self, monkeypatch) -> None:
        """P3-M4 fix: override must NOT mutate global config.DB_URL."""
        original_url = "postgresql+asyncpg://user:pass@host/original"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.setattr(config, "DB_URL", original_url)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with override_db_url(target_url):
            # config.DB_URL is NOT mutated by the new implementation
            assert original_url == config.DB_URL

        assert original_url == config.DB_URL

    def test_override_does_not_mutate_env_var_when_absent(self, monkeypatch) -> None:
        """P3-M4 fix: override must NOT set DATABASE_URL env var."""
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.delenv("DATABASE_URL", raising=False)

        with override_db_url(target_url):
            # DATABASE_URL env var is NOT set by the new implementation
            assert "DATABASE_URL" not in os.environ

        assert "DATABASE_URL" not in os.environ

    def test_override_does_not_mutate_existing_env_var(self, monkeypatch) -> None:
        """P3-M4 fix: override must NOT mutate or delete existing DATABASE_URL env var."""
        existing_env_url = "postgresql://user:pass@host/env"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.setenv("DATABASE_URL", existing_env_url)

        with override_db_url(target_url):
            # Existing DATABASE_URL env var is preserved (not overwritten)
            assert os.environ.get("DATABASE_URL") == existing_env_url

        # After context, env var is still preserved
        assert os.environ.get("DATABASE_URL") == existing_env_url

    def test_override_takes_precedence_over_env_var(self, monkeypatch) -> None:
        """Override takes precedence over DATABASE_URL env var."""
        env_url = "postgresql://user:pass@host/env"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.setenv("DATABASE_URL", env_url)

        with override_db_url(target_url):
            # Override wins over env var
            assert ConfigHandler.get_db_url() == target_url

        # After context, env var takes over again
        assert ConfigHandler.get_db_url() == env_url

    def test_override_takes_precedence_over_config_db_url(self, monkeypatch) -> None:
        """Override takes precedence over config.DB_URL."""
        original_url = "postgresql+asyncpg://user:pass@host/original"
        target_url = "postgresql+asyncpg://user:pass@host/target"

        monkeypatch.setattr(config, "DB_URL", original_url)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        with override_db_url(target_url):
            assert ConfigHandler.get_db_url() == target_url

        assert ConfigHandler.get_db_url() == original_url

    def test_nested_overrides(self, monkeypatch) -> None:
        """Nested overrides: inner override wins, outer restored after inner exits."""
        outer_url = "postgresql+asyncpg://user:pass@host/outer"
        inner_url = "postgresql+asyncpg://user:pass@host/inner"

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_URL", "postgresql+asyncpg://user:pass@host/original")

        with override_db_url(outer_url):
            assert ConfigHandler.get_db_url() == outer_url
            with override_db_url(inner_url):
                assert ConfigHandler.get_db_url() == inner_url
            assert ConfigHandler.get_db_url() == outer_url

    def test_concurrent_thread_does_not_see_override(self, monkeypatch) -> None:
        """P3-M4 DoD ③: concurrent calls to get_db_url() from another thread
        are NOT affected by the override set in the main thread.

        ContextVar is thread-local, so a thread spawned WITHOUT context
        propagation (raw threading.Thread) does not see the override.
        """
        target_url = "postgresql+asyncpg://user:pass@host/target"
        original_url = "postgresql+asyncpg://user:pass@host/original"

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_URL", original_url)

        results: list[str | None] = []
        barrier = threading.Barrier(2)

        def concurrent_caller() -> None:
            # Wait until main thread has entered the override context
            barrier.wait()
            # This thread was spawned WITHOUT context propagation, so it does
            # NOT see the ContextVar override. It falls through to config.DB_URL.
            results.append(ConfigHandler.get_db_url())
            barrier.wait()

        t = threading.Thread(target=concurrent_caller)
        t.start()

        with override_db_url(target_url):
            # Main thread sees the override
            assert ConfigHandler.get_db_url() == target_url
            # Let the worker thread run
            barrier.wait()
            # Wait for worker to finish its get_db_url() call
            barrier.wait()

        t.join()

        # Worker thread did NOT see the override — it got the original config.DB_URL
        assert results == [original_url]

    def test_override_propagates_to_run_in_executor(self, monkeypatch) -> None:
        """P3-M4: override propagates to threads spawned via
        ThreadPoolManager.run_async() (which uses contextvars.copy_context()).

        Verifies context propagation to ThreadPoolManager worker threads. The
        production path ``override_db_url`` wraps ``DatabaseMigrator.init_db``
        which calls ``ThreadPoolManager().run_async(TaskType.IO, run_upgrade)``
        to run Alembic in the IO thread pool. The context is propagated, so the
        worker thread sees the override.
        """
        target_url = "postgresql+asyncpg://user:pass@host/target"
        original_url = "postgresql+asyncpg://user:pass@host/original"

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_URL", original_url)

        async def main() -> str | None:
            with override_db_url(target_url):
                # Simulate the production path: run a sync function in the IO
                # thread pool via ThreadPoolManager.run_async(), which copies
                # the current context to the worker thread.
                from utils.thread_pool import TaskType, ThreadPoolManager

                return await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.get_db_url)

        result = asyncio.run(main())
        assert result == target_url

    def test_override_isolated_across_asyncio_tasks(self, monkeypatch) -> None:
        """P3-M4: override in one asyncio task does not leak to sibling tasks.

        asyncio.create_task() copies the current context, so a task created
        BEFORE the override does not see it, while a task created DURING the
        override does.
        """
        target_url = "postgresql+asyncpg://user:pass@host/target"
        original_url = "postgresql+asyncpg://user:pass@host/original"

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setattr(config, "DB_URL", original_url)

        async def get_url() -> str | None:
            return ConfigHandler.get_db_url()

        async def main() -> tuple[str | None, str | None, str | None]:
            # Task created BEFORE the override: snapshots context without override
            outside_task = asyncio.create_task(get_url())
            outside_result = await outside_task

            with override_db_url(target_url):
                # Task created DURING the override: snapshots context WITH override
                inside_task = asyncio.create_task(get_url())
                inside_result = await inside_task

            # Task created AFTER the override: snapshots context without override
            after_task = asyncio.create_task(get_url())
            after_result = await after_task

            return outside_result, inside_result, after_result

        outside, inside, after = asyncio.run(main())
        # Outside the override: original URL
        assert outside == original_url
        # Inside the override: target URL (context propagated to child task)
        assert inside == target_url
        # After the override: original URL
        assert after == original_url
