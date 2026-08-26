"""Database URL override context manager.

Provides an async-safe, thread-local way to temporarily override the database
URL returned by ``ConfigHandler.get_db_url()``. The override is visible to any
code that calls ``ConfigHandler.get_db_url()`` within the context — including
``EngineManager.get_connection_string`` (review01-A4: 原 CacheManager._get_connection_string
已移入 EngineManager；used in tests to redirect to test DBs)
and Alembic's ``env.py`` fallback path.

P3-M4-DbUrlOverride-Mock-In-Prod: previously this module used
``unittest.mock.patch`` to patch ``ConfigHandler.get_db_url`` globally, which
risked concurrent pollution if other threads called ``get_db_url()`` during the
context. The new implementation delegates to ``ConfigHandler.with_db_url_override``
which uses ``contextvars.ContextVar`` for async-safe thread-local storage.
``ThreadPoolManager.run_async()`` propagates the context to worker threads via
``contextvars.copy_context()``, so code running in the IO thread pool still sees
the override while concurrent calls from unrelated threads do not.
"""

from contextlib import contextmanager

from utils.config_handler import ConfigHandler


@contextmanager
def override_db_url(target_url: str):
    """Temporarily override the database URL returned by ``ConfigHandler.get_db_url()``.

    The override is async-safe and thread-local (via ``contextvars.ContextVar``).
    It is only visible in the current asyncio task / thread and tasks / threads
    spawned from it via ``ThreadPoolManager.run_async()`` (which propagates the
    context via ``contextvars.copy_context()``). Concurrent calls to
    ``get_db_url()`` from unrelated threads are unaffected.

    Note: direct ``loop.run_in_executor`` calls (bypassing ``ThreadPoolManager``)
    do NOT propagate the override. All current production callers go through
    ``ThreadPoolManager.run_async()``.

    Args:
        target_url: The database URL to use temporarily.

    Usage::

        with override_db_url("postgresql+asyncpg://user:pass@host/db"):
            await DatabaseMigrator.init_db(engine, auto_migrate=True)
    """
    with ConfigHandler.with_db_url_override(target_url):
        yield
