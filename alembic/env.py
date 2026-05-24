import asyncio
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


def get_database_url() -> str:
    """
    Get database URL from multiple sources.
    Priority: config.DB_URL > ConfigHandler.get_db_url() > environment variable
    """
    if config.DB_URL:
        return config.DB_URL

    url = os.environ.get("DATABASE_URL")
    if url:
        return url

    try:
        from utils.config_handler import ConfigHandler

        url = ConfigHandler.get_db_url()
        if url:
            return url
    except Exception:
        pass

    raise ValueError(
        "🛑 Database URL is not configured.\n"
        "Please run the astock UI wizard to configure PostgreSQL, "
        "or set 'DATABASE_URL' environment variable manually to run Alembic."
    )


db_url = get_database_url()
sync_db_url = db_url.replace("+asyncpg", "") if db_url else ""
async_db_url = db_url if "+asyncpg" in db_url else db_url.replace("postgresql://", "postgresql+asyncpg://")
alembic_config.set_main_option("sqlalchemy.url", sync_db_url)

if alembic_config.config_file_name is not None:
    if alembic_config.attributes.get("configure_logger", True):
        fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = context.config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
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
    if sync_db_url and sync_db_url.startswith("sqlite"):
        from sqlalchemy import create_engine as sync_create_engine

        connectable = sync_create_engine(sync_db_url, poolclass=pool.NullPool)
        with connectable.connect() as connection:
            do_run_migrations(connection)
        connectable.dispose()
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
