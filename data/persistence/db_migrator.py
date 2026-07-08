"""
Database Migration Module.

Encapsulates schema initialization and migration logic.
All schema creation and upgrades go through Alembic so fresh installs and
incremental upgrades use the same version-controlled DDL path.
"""

import asyncio
import logging
import os
import threading
import typing

import asyncpg
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError, ProgrammingError

from alembic import command
from utils.error_classifier import classify_error, classify_severity
from utils.log_decorators import PerfThreshold, log_async_operation
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

    For fresh installations (no alembic_version table), runs Alembic from
    base to head. Existing databases with pending revisions also run Alembic
    to head.
    """

    _alembic_lock = threading.Lock()

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
    @log_async_operation(threshold_ms=PerfThreshold.GLOBAL_INIT)
    async def init_db(cls, engine: typing.Any, auto_migrate: bool | None = None):
        """Initialize and optionally upgrade database schema.

        For fresh databases and existing databases with pending revisions,
        runs Alembic upgrade to head.

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

        # Fresh database: run the full Alembic chain from base to head.
        if current_rev is None:
            await cls._run_alembic_upgrade(engine)
            return

        # Heal orphaned revision before checking head
        await cls._heal_orphaned_revision(engine)
        # Re-read current_rev after potential heal
        current_rev = await cls._get_current_revision(engine)

        # Existing database: check for pending migrations
        head_rev = await cls._get_head_revision()

        if current_rev == head_rev:
            logger.info("[DatabaseMigrator] Database schema is up to date (rev=%s).", current_rev)
            return

        logger.info("[DatabaseMigrator] Schema state: current=%s, head=%s", current_rev, head_rev)

        if not auto_migrate:
            logger.warning(
                "[DatabaseMigrator] Database needs migration from %s to %s, "
                "but AUTO_MIGRATE is disabled. Set AUTO_MIGRATE=1 to enable automatic upgrades.",
                current_rev,
                head_rev,
            )
            raise DatabaseMigrationNeeded(current_rev, head_rev)

        await cls._run_alembic_upgrade(engine)

    @classmethod
    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _heal_orphaned_revision(cls, engine: typing.Any) -> None:
        """Detect and fix orphaned alembic revision.

        When a migration script is deleted from alembic/versions/ but the
        database still records that revision in alembic_version, Alembic
        will crash with 'Can't locate revision'. This method detects that
        condition and stamps the database to the current head revision.

        重要假设：本项目采用严格线性的单链迁移策略（0001 → 0002 → …），
        因此被删除的孤立版本必然是当前 head 的后代，拨回 head 后数据库
        schema 是 head 的超集，跳过升级是安全的。如果未来引入 Alembic
        多分支（Multiple Heads），此方法的"直接拨回 head"逻辑需要重新
        评估，以防 schema 漂移。
        """
        current_rev = await cls._get_current_revision(engine)
        if current_rev is None:
            return  # Fresh database, nothing to heal

        cfg = cls._get_alembic_config()
        script_dir = ScriptDirectory.from_config(cfg)
        known_revisions = {rev.revision for rev in script_dir.walk_revisions()}

        if current_rev in known_revisions:
            return  # Revision is valid, no healing needed

        head_rev = script_dir.get_current_head() or ""
        logger.warning(
            "[DatabaseMigrator] Orphaned revision detected: database is at '%s' "
            "which does not exist in alembic/versions/. "
            "Known revisions: %s. Auto-stamping to head '%s'.",
            current_rev,
            sorted(known_revisions),
            head_rev,
        )

        # 采用精准的外科手术式更新，只修改出错的特定游标，避免在未来多分支场景下误删其他健康分支
        async with engine.begin() as conn:
            if head_rev:
                await conn.execute(
                    text("UPDATE alembic_version SET version_num = :head WHERE version_num = :current"),
                    {"head": head_rev, "current": current_rev},
                )
            else:
                await conn.execute(
                    text("DELETE FROM alembic_version WHERE version_num = :current"),
                    {"current": current_rev},
                )

        logger.info(
            "[DatabaseMigrator] Orphaned revision healed: '%s' -> '%s' (stamped).",
            current_rev,
            head_rev,
        )

    @classmethod
    def _get_sync_database_url(cls, engine: typing.Any) -> str:
        """Build a sync SQLAlchemy URL for Alembic from the checked async engine."""
        engine_url = getattr(engine, "url", None)
        if engine_url is None:
            raise RuntimeError("Database engine does not expose a URL for Alembic migration.")

        if isinstance(engine_url, URL):
            driver_name = engine_url.drivername.replace("+asyncpg", "")
            return engine_url.set(drivername=driver_name).render_as_string(hide_password=False)

        return str(engine_url).replace("+asyncpg", "")

    @classmethod
    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _get_head_revision(cls) -> str:
        """Get the latest Alembic revision."""
        alembic_cfg = cls._get_alembic_config()
        script_dir = ScriptDirectory.from_config(alembic_cfg)
        head = script_dir.get_current_head()
        return head if head else ""

    @classmethod
    @log_async_operation(threshold_ms=PerfThreshold.GLOBAL_INIT)
    async def _run_alembic_upgrade(cls, engine: typing.Any) -> None:
        """Run Alembic upgrade to head.

        Uses standard error classification to distinguish system-level
        failures from recoverable errors.

        Uses the URL from the already-checked engine instead of letting env.py
        resolve configuration again. This keeps schema status checks and Alembic
        writes bound to the same database.
        """
        sync_database_url = cls._get_sync_database_url(engine)

        def run_upgrade() -> None:
            cfg = cls._get_alembic_config()
            # Use attributes to pass URL directly, avoiding ConfigParser interpolation issues
            # with special characters like '%40' (URL-encoded '@')
            cfg.attributes["database_url"] = sync_database_url
            cfg.attributes["configure_logger"] = False
            with cls._alembic_lock:
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
                    "[DatabaseMigrator] SYSTEM-LEVEL migration failure (%s): %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error(
                    "[DatabaseMigrator] Migration failed (%s): %s",
                    error_info["code"],
                    e,
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
            logger.info("[DatabaseMigrator] Database schema updated. Current revision: %s", new_rev)
        else:
            logger.warning("[DatabaseMigrator] Schema version is None after migration, this is unexpected.")

    @classmethod
    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def check_schema_status(cls, engine: typing.Any) -> tuple[str | None, str, bool]:
        """Check if schema needs migration.

        Returns:
            Tuple of (current_revision, head_revision, needs_migration)
        """
        head_rev = await cls._get_head_revision()
        current_rev = await cls._get_current_revision(engine)
        return current_rev, head_rev, current_rev != head_rev

    @classmethod
    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
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
            logger.error("[DBMigrator] Connection error getting revision: %s", exc)
            raise
        except ProgrammingError as exc:
            # ProgrammingError 需要区分：仅 "relation does not exist" 类错误
            # 表示全新数据库，返回 None；其他（语法错误、权限等）必须上抛。
            msg = str(exc).lower()
            if any(kw in msg for kw in _RELATION_NOT_FOUND_KEYWORDS):
                logger.debug("[DBMigrator] ProgrammingError (likely fresh DB): %s", exc)
                return None
            logger.error("[DBMigrator] ProgrammingError (not relation-not-found): %s", exc)
            raise
        except OperationalError as exc:
            # OperationalError 可能是权限/结构损坏等严重问题，上抛
            logger.error("[DBMigrator] OperationalError getting revision: %s", exc)
            raise
        except Exception as exc:
            # 其他未知异常：记录 warning 并上抛，避免误判为全新数据库
            logger.warning("[DBMigrator] Unexpected error getting revision, re-raising: %s", exc)
            raise
