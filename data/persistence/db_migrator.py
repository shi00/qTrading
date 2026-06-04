"""
Database Migration Module.

Encapsulates schema initialization and migration logic.
For fresh installations, uses SQLAlchemy metadata.create_all() for simplicity.
For upgrades, delegates to Alembic for version-controlled migrations.
"""

import asyncio
import logging
import os
import typing

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from alembic import command
from data.persistence.models import Base
from utils.error_classifier import classify_error, classify_severity
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

# 连接级异常：这些异常表示数据库不可达或认证失败，
# 必须上抛而不能被吞没为"全新数据库"。
_CONNECTION_EXCEPTIONS: tuple[type[Exception], ...] = (
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    ConnectionError,
    OSError,
)

# ProgrammingError 中表示"表/关系不存在"的关键词，
# 仅这些情况才应返回 None（视为全新数据库）。
# 其他 ProgrammingError（语法错误、权限不足等）必须上抛。
_RELATION_NOT_FOUND_KEYWORDS = ("does not exist", "not found", "no such")


class DatabaseMigrationNeeded(Exception):
    """Raised when database needs migration but AUTO_MIGRATE is disabled."""

    def __init__(self, current_rev: str | None, head_rev: str):
        self.current_rev = current_rev
        self.head_rev = head_rev
        super().__init__(f"Database needs migration from {current_rev} to {head_rev}")


class DatabaseMigrator:
    """Handles database schema initialization and migration.

    For fresh installations (no alembic_version table), uses SQLAlchemy
    metadata.create_all() for fast schema creation, then records the version.

    For existing databases needing upgrades, delegates to Alembic.
    """

    @classmethod
    def _get_alembic_config(cls) -> Config:
        """Create Alembic config with correct project paths.

        Shared by _get_head_revision and _run_alembic_upgrade to avoid
        duplicate path construction.
        """
        project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
        alembic_ini_path = os.path.join(project_root, "alembic.ini")
        cfg = Config(alembic_ini_path)
        cfg.set_main_option("script_location", os.path.join(project_root, "alembic"))
        return cfg

    @classmethod
    def _should_auto_migrate(cls) -> bool:
        """Check if automatic migration should be performed.

        Returns:
            True if AUTO_MIGRATE environment variable is "1" or "true" (case-insensitive)
        """
        auto_migrate = os.environ.get("AUTO_MIGRATE", "").lower()
        return auto_migrate in ("1", "true", "yes")

    @classmethod
    async def init_db(cls, engine: typing.Any, auto_migrate: bool | None = None):
        """Initialize and optionally upgrade database schema.

        For fresh databases, creates all tables via SQLAlchemy metadata
        and records the schema version.

        For existing databases, checks if migration is needed and optionally
        runs Alembic upgrade.

        Args:
            engine: SQLAlchemy async engine instance
            auto_migrate: Optional override for whether to auto-migrate
                (takes precedence over env var)

        Raises:
            DatabaseMigrationNeeded: If schema needs migration and auto-migrate is disabled
        """
        logger.debug("[DatabaseMigrator] Checking database schema state...")

        if auto_migrate is None:
            auto_migrate = cls._should_auto_migrate()

        current_rev = await cls._get_current_revision(engine)

        # Fresh database: no alembic_version table
        if current_rev is None:
            await cls._init_fresh_database(engine)
            return

        # Existing database: check for pending migrations
        head_rev = await cls._get_head_revision()

        if current_rev == head_rev:
            logger.info(f"[DatabaseMigrator] Database schema is up to date (rev={current_rev}).")
            return

        logger.info(f"[DatabaseMigrator] Schema state: current={current_rev}, head={head_rev}")

        if not auto_migrate:
            logger.warning(
                f"[DatabaseMigrator] Database needs migration from {current_rev} to {head_rev}, "
                f"but AUTO_MIGRATE is disabled. Set AUTO_MIGRATE=1 to enable automatic upgrades."
            )
            raise DatabaseMigrationNeeded(current_rev, head_rev)

        await cls._run_alembic_upgrade(engine)

    @classmethod
    async def _init_fresh_database(cls, engine: typing.Any) -> None:
        """Initialize a fresh database using SQLAlchemy metadata.

        Creates all tables and records the schema version in a single
        transaction to ensure atomicity. If the process crashes mid-way,
        no partial state will be left in the database.

        Handles concurrent initialization by catching PostgreSQL
        UniqueViolationError (pg_type conflict) and retrying after
        checking if another process completed the init.
        """
        logger.info("[DatabaseMigrator] Initializing fresh database...")

        head_rev = await cls._get_head_revision()

        try:
            # Single transaction: create tables + record version atomically
            async with engine.begin() as conn:

                def _create_tables(sync_conn: typing.Any) -> None:
                    Base.metadata.create_all(sync_conn)

                await conn.run_sync(_create_tables)

                # Record schema version within the same transaction
                await conn.execute(
                    text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
                )
                await conn.execute(
                    text(
                        "INSERT INTO alembic_version (version_num) VALUES (:version) "
                        "ON CONFLICT (version_num) DO UPDATE SET version_num = EXCLUDED.version_num"
                    ),
                    {"version": head_rev},
                )

            logger.info(f"[DatabaseMigrator] Fresh database initialized with schema version {head_rev}")

        except Exception as e:
            # Check if this is a concurrent initialization conflict
            # PostgreSQL raises UniqueViolationError when two processes
            # try to create tables with the same name simultaneously
            # (pg_type.typname namespace conflict)
            error_name = type(e).__name__
            error_str = str(e)

            if "UniqueViolationError" in error_name or "pg_type" in error_str or "typname" in error_str:
                logger.warning(
                    "[DatabaseMigrator] Concurrent initialization detected (pg_type conflict). "
                    "Checking if schema was initialized by another process..."
                )

                # Wait a moment for the other process to complete its transaction
                await asyncio.sleep(0.5)

                # Re-check the schema status
                current_rev = await cls._get_current_revision(engine)
                if current_rev == head_rev:
                    logger.info(
                        "[DatabaseMigrator] Schema was successfully initialized by another process. "
                        f"Current version: {current_rev}"
                    )
                    return

                # If still not initialized, re-raise the original error
                logger.error(
                    f"[DatabaseMigrator] Schema still not initialized after concurrent conflict. Original error: {e}"
                )

            raise

    @classmethod
    async def _get_head_revision(cls) -> str:
        """Get the latest Alembic revision."""
        alembic_cfg = cls._get_alembic_config()
        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head = script_dir.get_current_head()
        return head if head else ""

    @classmethod
    async def _run_alembic_upgrade(cls, engine: typing.Any) -> None:
        """Run Alembic upgrade to head.

        Uses standard error classification to distinguish system-level
        failures from recoverable errors.

        Note: The caller is responsible for ensuring that Alembic's env.py
        can resolve the correct database URL via ConfigHandler, config.DB_URL,
        or DATABASE_URL environment variable. Use override_db_url() context
        manager at the call site if needed (e.g., in tests).
        """

        def run_upgrade() -> None:
            cfg = cls._get_alembic_config()
            cfg.attributes["configure_logger"] = False
            command.upgrade(cfg, "head")

        try:
            await ThreadPoolManager().run_async(TaskType.IO, run_upgrade)
        except asyncio.CancelledError:
            logger.warning("[DatabaseMigrator] Migration cancelled during shutdown.")
            raise  # R2: CancelledError must propagate for graceful shutdown
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(
                    f"[DatabaseMigrator] SYSTEM-LEVEL migration failure ({error_info['code']}): {e}",
                    exc_info=True,
                )
            else:
                logger.error(
                    f"[DatabaseMigrator] Migration failed ({error_info['code']}): {e}",
                    exc_info=True,
                )
            raise

        # 升级后验证版本是否到达 head（不在 try/except 内，
        # RuntimeError 表示内部一致性检查失败，不需要经过 classify_error）
        new_rev = await cls._get_current_revision(engine)
        head_rev = await cls._get_head_revision()
        if new_rev and new_rev != head_rev:
            raise RuntimeError(
                f"Migration completed but schema version mismatch: "
                f"current={new_rev}, expected={head_rev}. "
                f"Partial migration may have occurred."
            )
        if new_rev:
            logger.info(f"[DatabaseMigrator] Database schema updated. Current revision: {new_rev}")
        else:
            logger.warning("[DatabaseMigrator] Schema version is None after migration, this is unexpected.")

    @classmethod
    async def check_schema_status(cls, engine: typing.Any) -> tuple[str | None, str, bool]:
        """Check if schema needs migration.

        Returns:
            Tuple of (current_revision, head_revision, needs_migration)
        """
        head_rev = await cls._get_head_revision()
        current_rev = await cls._get_current_revision(engine)
        return current_rev, head_rev, current_rev != head_rev

    @classmethod
    async def _get_current_revision(cls, engine: typing.Any) -> str | None:
        """Get current schema version from alembic_version table.

        Returns None if alembic_version table doesn't exist (fresh database).
        Raises connection-level exceptions instead of swallowing them,
        so that callers can distinguish "table not found" from "database unreachable".
        """
        try:
            async with engine.connect() as conn:

                def _sync_get_rev(c: typing.Any):
                    inspector = inspect(c)
                    if "alembic_version" not in inspector.get_table_names():
                        return None
                    result = c.execute(text("SELECT version_num FROM alembic_version"))
                    row = result.fetchone()
                    return row[0] if row else None

                return await conn.run_sync(_sync_get_rev)
        except _CONNECTION_EXCEPTIONS as exc:
            # 连接级错误必须上抛，不能吞没为"全新数据库"
            logger.error(f"[DBMigrator] Connection error getting revision: {exc}")
            raise
        except ProgrammingError as exc:
            # ProgrammingError 需要区分：仅 "relation does not exist" 类错误
            # 表示全新数据库，返回 None；其他（语法错误、权限等）必须上抛。
            msg = str(exc).lower()
            if any(kw in msg for kw in _RELATION_NOT_FOUND_KEYWORDS):
                logger.debug(f"[DBMigrator] ProgrammingError (likely fresh DB): {exc}")
                return None
            logger.error(f"[DBMigrator] ProgrammingError (not relation-not-found): {exc}")
            raise
        except OperationalError as exc:
            # OperationalError 可能是权限/结构损坏等严重问题，上抛
            logger.error(f"[DBMigrator] OperationalError getting revision: {exc}")
            raise
        except Exception as exc:
            # 其他未知异常：记录 warning 并上抛，避免误判为全新数据库
            logger.warning(f"[DBMigrator] Unexpected error getting revision, re-raising: {exc}")
            raise
