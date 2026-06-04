"""Database URL override context manager.

Provides a thread-safe way to temporarily override the database URL
used by config.DB_URL, DATABASE_URL environment variable, and
ConfigHandler.get_db_url(). This is needed because Alembic's env.py
reads the URL from these sources at import time, and we need to ensure
it uses the correct URL during migrations run from within the application.
"""

import os
from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def override_db_url(target_url: str):
    """Temporarily override database URL in config, environment, and ConfigHandler.

    This ensures that Alembic's env.py (which reads from ConfigHandler,
    config.DB_URL, or DATABASE_URL env var) uses the correct URL during migrations.

    The override covers all three priority levels used by env.py's
    get_database_url():
      1. ConfigHandler.get_db_url()  (highest priority)
      2. config.DB_URL
      3. os.environ["DATABASE_URL"]  (lowest priority)

    Args:
        target_url: The database URL to use temporarily.

    Usage:
        with override_db_url("postgresql+asyncpg://user:pass@host/db"):
            await DatabaseMigrator.init_db(engine, auto_migrate=True)
    """
    import config

    original_db_url = config.DB_URL
    original_env_db_url = os.environ.get("DATABASE_URL")

    config.DB_URL = target_url
    os.environ["DATABASE_URL"] = target_url

    with patch("utils.config_handler.ConfigHandler.get_db_url", return_value=target_url):
        try:
            yield
        finally:
            config.DB_URL = original_db_url
            if original_env_db_url is not None:
                os.environ["DATABASE_URL"] = original_env_db_url
            elif "DATABASE_URL" in os.environ:
                del os.environ["DATABASE_URL"]
