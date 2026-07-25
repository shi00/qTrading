import asyncio
import logging
import os
import sys
from logging.config import fileConfig

# S5-5 fix: Ensure alembic runs in UTC to match DB storage convention
os.environ["TZ"] = "UTC"
try:
    import time

    time.tzset()
except AttributeError:
    pass  # Windows doesn't have tzset

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from sqlalchemy import pool
from sqlalchemy.engine import Connection

import config
from alembic import context
from data.persistence.models import Base

alembic_config = context.config

logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """
    Get database URL from multiple sources.
    Priority: Alembic config/attributes > ConfigHandler.get_db_url() > config.DB_URL > DATABASE_URL env var

    ConfigHandler.get_db_url() 内部优先级：DATABASE_URL env > rebuild from db_host > config.DB_URL。
    embedded 模式下 main.py 永久设置 config.DB_URL（spec.md §1.4），由 get_db_url() Priority 3 兜底。

    This lets application code bind migrations to the same engine it has already
    checked, while keeping CLI Alembic and onboarding configuration working.
    """
    attr_url = alembic_config.attributes.get("database_url")
    if attr_url:
        return attr_url

    configured_url = alembic_config.get_main_option("sqlalchemy.url")
    if configured_url and configured_url != "driver://user:pass@localhost/dbname":
        return configured_url

    try:
        from utils.config_handler import ConfigHandler
        from utils.sanitizers import DataSanitizer

        url = ConfigHandler.get_db_url()
        if url:
            return url
    except Exception as e:
        # R9: 异常消息可能含连接串（含密码），须经 DataSanitizer.sanitize_error 脱敏
        logger.warning("Alembic env setup failed: %s", DataSanitizer.sanitize_error(e))

    if config.DB_URL:
        return config.DB_URL

    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    raise ValueError(
        "Database URL is not configured.\n"
        "Please run the astock UI wizard to configure PostgreSQL, "
        "or set 'DATABASE_URL' environment variable manually to run Alembic."
    )


db_url = get_database_url()
sync_db_url = db_url.replace("+asyncpg", "") if db_url else ""
async_db_url = db_url if "+asyncpg" in db_url else db_url.replace("postgresql://", "postgresql+asyncpg://")
# Use attributes instead of set_main_option to avoid ConfigParser interpolation
# issues with URL-encoded special characters like '%40' (URL-encoded '@')
# This is consistent with db_migrator.py's approach
alembic_config.attributes["database_url"] = sync_db_url

if alembic_config.config_file_name is not None:
    if alembic_config.attributes.get("configure_logger", True):
        fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    # Get URL from attributes first (avoids ConfigParser interpolation issues),
    # fallback to get_main_option for CLI usage
    url = alembic_config.attributes.get("database_url")
    if not url:
        url = context.config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    connectable = create_async_engine(
        async_db_url,
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
