# utils/db_utils.py
from utils.config_handler import ConfigHandler

# 同步连接驱动：SQLAlchemy 2.1 起 ``postgresql://`` 的默认驱动改为 psycopg 3，
# 而项目同步路径只依赖 psycopg2-binary（未安装 psycopg），故必须显式声明。
SYNC_PG_SCHEME = "postgresql+psycopg2://"
ASYNC_PG_SCHEME = "postgresql+asyncpg://"


def to_sync_db_url(url: str) -> str:
    """把 asyncpg / 裸 PostgreSQL URL 规范为同步 psycopg2 URL。

    SQLAlchemy 2.1 之前 ``postgresql://`` 的隐式默认驱动是 psycopg2，2.1 起改为
    psycopg 3（见官方 migration_21「Default PostgreSQL driver changed to psycopg」）。
    同步 engine 与 Alembic offline 迁移依赖 psycopg2-binary，因此同步连接串必须
    显式携带 ``+psycopg2``，否则会因缺少 psycopg 抛 ModuleNotFoundError。
    """
    if url.startswith(ASYNC_PG_SCHEME):
        return SYNC_PG_SCHEME + url[len(ASYNC_PG_SCHEME) :]
    if url.startswith("postgresql://"):
        return SYNC_PG_SCHEME + url[len("postgresql://") :]
    return url


def to_async_db_url(url: str) -> str:
    """把 psycopg2 / 裸 PostgreSQL URL 规范为 asyncpg URL（Alembic env 用）。"""
    if url.startswith(SYNC_PG_SCHEME):
        return ASYNC_PG_SCHEME + url[len(SYNC_PG_SCHEME) :]
    if url.startswith("postgresql://"):
        return ASYNC_PG_SCHEME + url[len("postgresql://") :]
    return url


def get_db_pool_config() -> dict:
    """提取数据库连接池配置。

    ConfigHandler.get_typed 已内置类型转换与默认值回退，
    此函数仅负责组装为 dict 供 create_engine / create_async_engine 使用。
    """
    return {
        "pool_size": ConfigHandler.get_db_connection_pool_size(),
        "max_overflow": ConfigHandler.get_db_max_overflow(),
        "pool_timeout": ConfigHandler.get_db_pool_timeout(),
        "pool_recycle": ConfigHandler.get_db_pool_recycle(),
        "pool_pre_ping": ConfigHandler.get_db_pool_pre_ping(),
    }
