import typing

"""
Database Migration Module.

Encapsulates all Alembic-related schema initialization and migration logic.
Provides automatic detection and handling of legacy database environments.
"""

import logging
import os

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from alembic import command
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class DatabaseMigrationNeeded(Exception):
    """Raised when database needs migration but AUTO_MIGRATE is disabled."""

    def __init__(self, current_rev: str | None, head_rev: str):
        self.current_rev = current_rev
        self.head_rev = head_rev
        super().__init__(f"Database needs migration from {current_rev} to {head_rev}")


class DatabaseMigrator:
    """Handles database schema initialization and migration via Alembic."""

    @classmethod
    def _should_auto_migrate(cls) -> bool:
        """
        Check if automatic migration should be performed.

        Returns:
            True if AUTO_MIGRATE environment variable is "1" or "true" (case-insensitive)
        """
        auto_migrate = os.environ.get("AUTO_MIGRATE", "").lower()
        return auto_migrate in ("1", "true", "yes")

    @classmethod
    async def init_db(cls, engine: typing.Any, auto_migrate: bool | None = None):
        """
        Initialize and optionally upgrade database schema.

        By default, only checks schema state and does NOT perform automatic upgrades.
        To enable automatic upgrades, set AUTO_MIGRATE=1 or pass auto_migrate=True.

        Handles backward compatibility for legacy databases that existed
        before Alembic was introduced.

        Args:
            engine: SQLAlchemy async engine instance
            auto_migrate: Optional override for whether to auto-migrate (takes precedence over env var)

        Raises:
            DatabaseMigrationNeeded: If schema needs migration and auto-migrate is disabled
        """
        logger.debug("[DatabaseMigrator] Checking database schema state...")

        if auto_migrate is None:
            auto_migrate = cls._should_auto_migrate()

        has_alembic, has_old_schema = False, False
        current_rev = None
        head_rev = None

        try:
            async with engine.connect() as conn:

                def _sync_check(c: typing.Any):
                    inspector = inspect(c)
                    tables = inspector.get_table_names()
                    return "alembic_version" in tables, "stock_basic" in tables

                has_alembic, has_old_schema = await conn.run_sync(_sync_check)
        except Exception as e:
            logger.error(
                f"[DatabaseMigrator] Database table inspection failed: {e}",
                exc_info=True,
            )

        def get_head_revision() -> str:
            """Get the latest Alembic revision."""
            alembic_ini_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "alembic.ini",
            )
            alembic_cfg = Config(alembic_ini_path)
            alembic_cfg.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(__file__), "..", "..", "alembic"),
            )
            script_dir = ScriptDirectory.from_config(alembic_cfg)
            head = script_dir.get_current_head()
            return head if head else ""

        try:
            head_rev = await ThreadPoolManager().run_async(TaskType.IO, get_head_revision)
            current_rev = await cls._get_current_revision(engine)
            logger.info(f"[DatabaseMigrator] Schema state: current={current_rev}, head={head_rev}")
        except Exception as e:
            logger.error(
                f"[DatabaseMigrator] Failed to get schema revisions: {e}",
                exc_info=True,
            )
            raise

        needs_migration = current_rev != head_rev

        if not needs_migration:
            logger.info("[DatabaseMigrator] Database schema is up to date.")
            return

        if not auto_migrate:
            logger.warning(
                f"[DatabaseMigrator] Database needs migration from {current_rev} to {head_rev}, "
                f"but AUTO_MIGRATE is disabled. Set AUTO_MIGRATE=1 to enable automatic upgrades."
            )
            raise DatabaseMigrationNeeded(current_rev, head_rev)

        logger.info(f"[DatabaseMigrator] Automatic migration enabled. Upgrading from {current_rev} to {head_rev}")

        def run_alembic_upgrade():
            alembic_ini_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "alembic.ini",
            )
            alembic_cfg = Config(alembic_ini_path)
            alembic_cfg.attributes["configure_logger"] = False
            alembic_cfg.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(__file__), "..", "..", "alembic"),
            )

            if has_old_schema and not has_alembic:
                logger.debug(
                    "[DatabaseMigrator] Legacy database detected, computing baseline...",
                )

                script_dir = ScriptDirectory.from_config(alembic_cfg)
                baseline_rev = None

                for rev in script_dir.walk_revisions():
                    if rev.down_revision is None:
                        baseline_rev = rev.revision
                        break

                if baseline_rev:
                    logger.debug(
                        f"[DatabaseMigrator] Stamping legacy database with baseline: {baseline_rev}",
                    )
                    command.stamp(alembic_cfg, baseline_rev)
                else:
                    logger.warning(
                        "[DatabaseMigrator] Could not find valid baseline revision, skipping stamp!",
                    )

            command.upgrade(alembic_cfg, "head")

        try:
            await ThreadPoolManager().run_async(TaskType.IO, run_alembic_upgrade)

            new_rev = await cls._get_current_revision(engine)
            if new_rev:
                logger.info(f"[DatabaseMigrator] Database schema updated. Current revision: {new_rev}")
            else:
                logger.info("[DatabaseMigrator] Database schema updated to latest version.")
        except Exception as e:
            logger.error(
                f"[DatabaseMigrator] Schema upgrade failed: {e}",
                exc_info=True,
            )
            raise

    @classmethod
    async def check_schema_status(cls, engine: typing.Any) -> tuple[str | None, str, bool]:
        """
        Check if schema needs migration.

        Returns:
            Tuple of (current_revision, head_revision, needs_migration)
        """

        def get_head_rev():
            alembic_ini_path = os.path.join(os.path.dirname(__file__), "..", "..", "alembic.ini")
            alembic_cfg = Config(alembic_ini_path)
            alembic_cfg.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(__file__), "..", "..", "alembic"),
            )
            script_dir = ScriptDirectory.from_config(alembic_cfg)
            head = script_dir.get_current_head()
            return head if head else ""

        head_rev = await ThreadPoolManager().run_async(TaskType.IO, get_head_rev)
        current_rev = await cls._get_current_revision(engine)
        return current_rev, head_rev, current_rev != head_rev

    @classmethod
    async def _get_current_revision(cls, engine: typing.Any) -> str | None:
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
        except Exception as exc:
            logger.debug(f"[DBMigrator] get_alembic_rev failed: {exc}")
            return None
