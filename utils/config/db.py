"""数据库域：db_url/pool/embedded 模式/_db_url_override。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的 db 相关方法，仅按域搬移、不改行为。本模块所有共享状态与
跨方法访问一律经 ``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

import contextlib
import os
import re

from utils import config_handler as cfg
from utils.sanitizers import DataSanitizer

DEFAULTS = cfg.ConfigHandler.DEFAULT_CONFIG


def set_embedded_db_url(url: str) -> None:
    """启用 embedded 运行时模块级完整 URL override（跨 task/线程可靠，非 ContextVar）。

    进程级生命周期：应用启动时由 main.py embedded 分支设置一次，存续整个进程，
    不随 asyncio task / 会话回收。Flet 多 session 仅在有独立 embedded 启动路径时
    set 一次，故不会跨 session 泄漏（embedded UID 依赖 keyring-like 运行时注入）。
    """
    cfg.ConfigHandler._embedded_db_url = url


def clear_embedded_db_url() -> None:
    """停用 embedded 模块级 override（进程运行期显式停用/测试隔离时调用）。

    生产路径通常不调用——embedded 运行时进程级注入一次即存续；本方法主要服务于
    单测 R7 隔离（见 tests/unit/conftest.py 的 autouse reset fixture）。
    """
    cfg.ConfigHandler._embedded_db_url = None


@contextlib.contextmanager
def with_db_url_override(url: str):
    """P3-M4-DbUrlOverride-Mock-In-Prod: temporarily override get_db_url() return value.

    Uses ``contextvars.ContextVar`` for async-safe thread-local storage. The
    override is only visible in the current asyncio task / thread (and tasks
    / threads spawned from it). Concurrent calls from unrelated threads are unaffected.

    Args:
        url: The database URL to return from ``get_db_url()`` within the context.
    """
    token = cfg.ConfigHandler._db_url_override.set(url)
    try:
        yield
    finally:
        cfg.ConfigHandler._db_url_override.reset(token)


def is_embedded_mode() -> bool:
    """判断是否为 embedded PostgreSQL 模式（R-B1 单一入口点）。

    判定条件：QTRADING_DATABASE_MODE == "embedded" AND AppConfig.embedded_pg_enabled == True。
    `.get()` default True 与 AppConfig.embedded_pg_enabled field default 保持一致。
    """
    mode = os.environ.get("QTRADING_DATABASE_MODE", "embedded").lower()
    if mode != "embedded":
        return False
    try:
        return bool(cfg.ConfigHandler.load_config().get("embedded_pg_enabled", True))
    except Exception as e:
        cfg.logger.warning(
            "[ConfigHandler] is_embedded_mode load_config failed: %s",
            DataSanitizer.sanitize_error(e),
        )
        # 与 QTRADING_DATABASE_MODE 默认 "embedded" 保持一致
        return True


def get_db_url():
    """Get PostgreSQL connection URL。

    Resolution priority (12-factor app compliance):
    0. ``_db_url_override`` ContextVar — highest.
    1.5 ``_embedded_db_url`` module-level override — embedded 恒定胜 env。
    1. ``DATABASE_URL`` environment variable。
    2. Rebuild from stored components via ``DatabaseConfigService.build_url()``。
    3. Fall back to ``config.DB_URL`` for pre-onboarding scenarios。
    """
    override = cfg.ConfigHandler._db_url_override.get()
    if override is not None:
        return override

    embedded_url = cfg.ConfigHandler._embedded_db_url
    if embedded_url:
        return embedded_url

    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url

    host = cfg.ConfigHandler.get_typed("db_host", str, "")
    if host:
        from data.persistence.db_config_service import (  # lazy-import: 打破 ConfigHandler ↔ DatabaseConfigService 循环依赖
            DatabaseConfigService,
        )

        password = cfg.ConfigHandler.get_db_password()
        return DatabaseConfigService.build_url(
            host=host,
            port=cfg.ConfigHandler.get_typed("db_port", int, DEFAULTS["db_port"]),
            user=cfg.ConfigHandler.get_typed("db_user", str, DEFAULTS["db_user"]),
            password=password,
            database=cfg.ConfigHandler.get_typed("db_name", str, DEFAULTS["db_name"]),
            async_driver=True,
        )

    # Priority 3: fallback to config.DB_URL
    return cfg.config.DB_URL


def save_db_config(host: str, port: int, user: str, password: str, database: str) -> bool:
    """Save database configuration.

    All runtime URL resolution should go through get_db_url(), which rebuilds the
    URL from stored components + keyring password on every call.
    """
    from data.persistence.db_config_service import (  # lazy-import: 打破 ConfigHandler ↔ DatabaseConfigService 循环依赖
        DatabaseConfigService,
    )

    db_url = DatabaseConfigService.build_url(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        async_driver=True,
    )

    if not cfg.ConfigHandler.save_config(
        {
            "db_host": host,
            "db_port": port,
            "db_user": user,
            "db_name": database,
            "db_url": re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", db_url),
        }
    ):
        return False

    if password:
        return cfg.ConfigHandler.save_db_password(password)
    return True


def get_db_config() -> dict:
    """Get database configuration components."""
    password = cfg.ConfigHandler.get_db_password()
    return {
        "host": cfg.ConfigHandler.get_typed("db_host", str, ""),
        "port": cfg.ConfigHandler.get_typed("db_port", int, DEFAULTS["db_port"]),
        "user": cfg.ConfigHandler.get_typed("db_user", str, DEFAULTS["db_user"]),
        "password": password,
        "database": cfg.ConfigHandler.get_typed("db_name", str, DEFAULTS["db_name"]),
    }


def get_db_connection_pool_size():
    return cfg.ConfigHandler.get_typed("db_connection_pool_size", int, DEFAULTS["db_connection_pool_size"])


def set_db_connection_pool_size(size):
    return cfg.ConfigHandler.set_typed("db_connection_pool_size", int(size))


def get_db_pool_pre_ping():
    return cfg.ConfigHandler.get_typed("db_pool_pre_ping", bool, DEFAULTS["db_pool_pre_ping"])


def get_db_pool_recycle():
    return cfg.ConfigHandler.get_typed("db_pool_recycle", int, DEFAULTS["db_pool_recycle"])


def get_db_pool_timeout():
    return cfg.ConfigHandler.get_typed("db_pool_timeout", int, DEFAULTS["db_pool_timeout"])


def set_db_pool_timeout(timeout):
    return cfg.ConfigHandler.set_typed("db_pool_timeout", int(timeout))


def get_db_max_overflow():
    return cfg.ConfigHandler.get_typed("db_max_overflow", int, DEFAULTS["db_max_overflow"])


def set_db_max_overflow(overflow):
    return cfg.ConfigHandler.set_typed("db_max_overflow", int(overflow))


def get_max_io_workers():
    """Get max IO threads from config, capped by DB connection pool capacity."""
    db_pool_size = cfg.ConfigHandler.get_typed("db_connection_pool_size", int, DEFAULTS["db_connection_pool_size"])
    db_max_overflow = cfg.ConfigHandler.get_typed("db_max_overflow", int, DEFAULTS["db_max_overflow"])
    db_capacity = db_pool_size + db_max_overflow

    io_workers = cfg.ConfigHandler.get_typed("max_io_workers", int, DEFAULTS["max_io_workers"])

    if io_workers <= 0:
        io_workers = min(os.cpu_count() or 4, db_capacity)

    if io_workers > db_capacity:
        should_warn = False
        with cfg.ConfigHandler._lock.gen_wlock():
            if not cfg.ConfigHandler._io_workers_cap_warned:
                cfg.ConfigHandler._io_workers_cap_warned = True
                should_warn = True
        if should_warn:
            cfg.logger.warning(
                "[Config] IO workers (%s) exceeds DB connection capacity (%s). Capping to %s.",
                io_workers,
                db_capacity,
                db_capacity,
            )
        io_workers = db_capacity

    return io_workers


def _reset_io_cap_warning():
    """Reset IO workers cap warning flag. Called by ThreadPoolManager.reload_config."""
    with cfg.ConfigHandler._lock.gen_wlock():
        cfg.ConfigHandler._io_workers_cap_warned = False


def set_max_io_workers(count):
    """Set max IO workers in config."""
    return cfg.ConfigHandler.set_typed("max_io_workers", int(count))
