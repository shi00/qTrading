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
from sqlalchemy import inspect

from alembic import command
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class DatabaseMigrator:
    """Handles database schema initialization and migration via Alembic."""

    @classmethod
    async def init_db(cls, engine: typing.Any):
        """
        Initialize and upgrade database schema.

        Handles backward compatibility for legacy databases that existed
        before Alembic was introduced.

        Args:
            engine: SQLAlchemy async engine instance
        """
        logger.debug("[DatabaseMigrator] Checking database schema state...")

        has_alembic, has_old_schema = False, False
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

        def run_alembic_upgrade():
            alembic_ini_path = os.path.join(
                os.path.dirname(__file__),
                "..",
                "alembic.ini",
            )
            alembic_cfg = Config(alembic_ini_path)
            alembic_cfg.attributes["configure_logger"] = False
            alembic_cfg.set_main_option(
                "script_location",
                os.path.join(os.path.dirname(__file__), "..", "alembic"),
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
            logger.debug(
                "[DatabaseMigrator] Database schema updated to latest version."
            )
        except Exception as e:
            logger.error(
                f"[DatabaseMigrator] Schema upgrade failed: {e}",
                exc_info=True,
            )
            raise
