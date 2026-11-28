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
# DAT-19: alembic/ 目录不在 prepend_sys_path（`=`）内，注册到 sys.path，
# 使迁移可经顶层 `from _shared_helpers import column_exists` 复用幂等 helper。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sqlalchemy import pool
from sqlalchemy.engine import Connection

import config
from alembic import context
from data.persistence.migration_lock import acquire_migration_lock, release_migration_lock
from data.persistence.models import Base
from utils.db_utils import to_async_db_url, to_sync_db_url

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
# SQLAlchemy 2.1 起光秃 postgresql:// 默认驱动为 psycopg 3；同步路径（offline 迁移、
# alembic check）依赖 psycopg2-binary，故显式规范化两侧 driver。
sync_db_url = to_sync_db_url(db_url) if db_url else ""
async_db_url = to_async_db_url(db_url) if db_url else ""
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

    try:
        async with connectable.connect() as connection:
            # 跨进程互斥：DatabaseMigrator._alembic_lock 只是进程内 threading.Lock，
            # 无法覆盖 CLI `alembic upgrade head`、第二个应用实例或 E2E 子进程。
            # 会话级 advisory lock 绑定本连接（连接关闭即释放），且必须在
            # run_migrations 之前取得，Alembic 才会在锁内读取 alembic_version。
            await acquire_migration_lock(connection)
            # D06-003 关键：acquire 的 SELECT 经 autobegin 开启了隐式事务；若不先提交，
            # Alembic 的 begin_transaction 会复用这个无主事务且不再 commit，
            # connectable.connect() 退出时整个迁移（含 alembic_version 写入）被回滚。
            # 会话级 advisory lock 跨 COMMIT 保持，此处提交只终结锁事务、不释放互斥。
            await connection.commit()
            try:
                await connection.run_sync(do_run_migrations)
            except asyncio.CancelledError:
                # R2：取消必须继续传播。取消中的连接可能已失效，此处不做
                # rollback / commit：NullPool 连接随后关闭，回滚未提交事务并
                # 释放会话级 advisory lock。
                raise
            except BaseException:
                # D1-09：迁移失败时提交事务既违背回滚语义，也会在失效 /
                # InFailedSqlTransaction 连接上抛出次生异常覆盖真实根因。
                # 先尽力回滚把连接拉出失败事务（advisory lock 是会话级的，不随
                # 回滚释放），再释放锁；清理自身失败只脱敏记录，绝不覆盖原始异常。
                try:
                    await connection.rollback()
                except Exception as cleanup_error:
                    # R9: 异常消息可能含连接串（含密码），须脱敏后再记录
                    from utils.sanitizers import DataSanitizer

                    logger.warning(
                        "Failed to roll back Alembic migration transaction: %s",
                        DataSanitizer.sanitize_error(cleanup_error),
                    )
                await release_migration_lock(connection)
                raise
            else:
                await release_migration_lock(connection)
                await connection.commit()
    finally:
        original_error = sys.exception()
        try:
            await connectable.dispose()
        except Exception as dispose_error:
            # 原始故障优先：dispose 失败不得覆盖 in-flight 异常。CancelledError 属
            # BaseException，不被此处捕获，仍按 R2 继续向上传播。
            if original_error is None:
                raise
            # R9: dispose 异常消息可能含连接串（含密码），须脱敏后再记录
            from utils.sanitizers import DataSanitizer

            logger.warning(
                "Failed to dispose Alembic migration engine: %s",
                DataSanitizer.sanitize_error(dispose_error),
            )


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
