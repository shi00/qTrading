"""数据库域：db_url/pool/embedded 模式/_db_url_override。

迁移动期为 review05-E11 拆分产物：逻辑原属 ``utils/config_handler.py`` 的
``ConfigHandler`` 的 db 相关方法，仅按域搬移、不改行为。本模块所有共享状态与
跨方法访问一律经 ``cfg = utils.config_handler`` 间接引用，以保持现有单测 mock 有效。
"""

from __future__ import annotations

from collections.abc import Mapping
import contextlib
from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import re
from typing import Any

from utils import config_handler as cfg
from utils.sanitizers import DataSanitizer

DEFAULTS = cfg.ConfigHandler.DEFAULT_CONFIG

DATABASE_MODE_ENV = "QTRADING_DATABASE_MODE"


class DatabaseMode(StrEnum):
    """数据库模式（内置 sidecar / 外置 PostgreSQL）。"""

    EMBEDDED = "embedded"
    EXTERNAL = "external"


@dataclass(frozen=True)
class DatabaseModeResolution:
    """数据库模式解析结果（启动期判定与设置页身份展示的唯一数据源）。

    Attributes:
        mode: 当前进程实际生效的模式（与 ``prepare_database_runtime`` 判定一致）。
        source: 生效模式的决定来源——``env`` / ``config`` / ``fallback_standard_variant``。
        configured_mode: 磁盘持久化的用户偏好模式（下次启动的期望模式）。
        env_mode: 环境变量显式指定的模式；未设置或非法值为 ``None``。
        conflict: 环境变量与生效模式不一致（环境强制覆盖了用户偏好）。
        fallback_reason: 非用户偏好导致的降级原因（目前仅 ``standard_variant``）。
    """

    mode: DatabaseMode
    source: str
    configured_mode: DatabaseMode
    env_mode: DatabaseMode | None = None
    conflict: bool = False
    fallback_reason: str | None = None

    @property
    def restart_required(self) -> bool:
        """持久化偏好与当前生效模式不一致 → 需重启应用才能生效。"""
        return self.configured_mode is not self.mode


def _parse_mode(value: object) -> DatabaseMode | None:
    raw = str(value or "").strip().lower()
    for mode in DatabaseMode:
        if raw == mode.value:
            return mode
    return None


def _config_field(raw_config: Mapping[str, Any] | object | None, key: str, default: object = None) -> object:
    if raw_config is None:
        return default
    if isinstance(raw_config, Mapping):
        return raw_config.get(key, default)
    return getattr(raw_config, key, default)


def _explicit_env_mode() -> DatabaseMode | None:
    """环境变量显式指定的模式；未设置/空/非法值视为未指定（保持默认 embedded）。"""
    return _parse_mode(os.environ.get(DATABASE_MODE_ENV, ""))


def _configured_mode(raw_config: Mapping[str, Any] | object | None) -> tuple[DatabaseMode, bool]:
    """读取磁盘持久化模式，返回 ``(模式, 是否由显式 db_mode 键给出)``。

    旧配置无 ``db_mode`` 键时按 ``embedded_pg_enabled`` 推导，避免升级后出现
    「偏好内置 / 实际外置」的假冲突。
    """
    explicit = _parse_mode(_config_field(raw_config, "db_mode"))
    if explicit is not None:
        return explicit, True
    enabled = bool(_config_field(raw_config, "embedded_pg_enabled", True))
    return (DatabaseMode.EMBEDDED if enabled else DatabaseMode.EXTERNAL), False


def resolve_database_mode(raw_config: Mapping[str, Any] | object | None = None) -> DatabaseModeResolution:
    """解析数据库模式（R-B1 单一入口）。

    优先级与 ``app.bootstrap.prepare_database_runtime`` 的真实启动判定保持一致：
    1. ``QTRADING_DATABASE_MODE`` 显式设置时由环境变量决定（external 恒优先）；
    2. 否则由持久化的 ``db_mode``（旧配置回退 ``embedded_pg_enabled``）决定；
    3. ``embedded_pg_enabled=False`` 时内置库不启动（生效外置，且与偏好冲突时标记 conflict）；
    4. 标准版 frozen 包（无内置 sidecar 二进制）自动降级外置（``fallback_reason``）。

    Args:
        raw_config: 已加载配置（Mapping 或 AppConfig）；None 时自行加载 ConfigHandler 配置。
    """
    if raw_config is None:
        try:
            raw_config = cfg.ConfigHandler.load_config()
        except Exception as e:
            cfg.logger.warning(
                "[ConfigHandler] resolve_database_mode load_config failed: %s",
                DataSanitizer.sanitize_error(e),
            )
            raw_config = None

    enabled = bool(_config_field(raw_config, "embedded_pg_enabled", True))
    configured, _explicit = _configured_mode(raw_config)
    env_mode = _explicit_env_mode()

    if env_mode is DatabaseMode.EXTERNAL:
        return DatabaseModeResolution(
            mode=DatabaseMode.EXTERNAL,
            source="env",
            configured_mode=configured,
            env_mode=env_mode,
            conflict=configured is DatabaseMode.EMBEDDED,
        )

    if not enabled:
        return DatabaseModeResolution(
            mode=DatabaseMode.EXTERNAL,
            source="config",
            configured_mode=configured,
            env_mode=env_mode,
            conflict=env_mode is DatabaseMode.EMBEDDED,
        )

    if env_mode is DatabaseMode.EMBEDDED:
        return DatabaseModeResolution(
            mode=DatabaseMode.EMBEDDED,
            source="env",
            configured_mode=configured,
            env_mode=env_mode,
            conflict=configured is DatabaseMode.EXTERNAL,
        )

    if configured is DatabaseMode.EXTERNAL:
        return DatabaseModeResolution(
            mode=DatabaseMode.EXTERNAL,
            source="config",
            configured_mode=configured,
            env_mode=env_mode,
        )

    if is_frozen_standard_variant_without_sidecar(raw_config):
        return DatabaseModeResolution(
            mode=DatabaseMode.EXTERNAL,
            source="fallback_standard_variant",
            configured_mode=configured,
            env_mode=env_mode,
            conflict=False,
            fallback_reason="standard_variant",
        )

    return DatabaseModeResolution(
        mode=DatabaseMode.EMBEDDED,
        source="config",
        configured_mode=configured,
        env_mode=env_mode,
    )


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


def is_frozen_standard_variant_without_sidecar(raw_config: Mapping[str, Any] | object | None = None) -> bool:
    """判定当前是否为不含内置 PostgreSQL sidecar 二进制的标准版打包发行物（B01 / MAJOR-1 / MAJOR-2）。

    触发自动降级为 external 模式的条件（缺一不可）：
    1. ``sys.frozen`` 为 ``True``（PyInstaller 打包运行态）；
    2. 环境变量 ``QTRADING_DATABASE_MODE`` 未显式设为 ``embedded``（用户未强制要求内置库）；
    3. ``embedded_pg_sidecar_path`` 与 ``embedded_pg_data_root`` 未配置自定义路径；
    4. ``sys._MEIPASS`` 存在，且默认 ``_MEIPASS/sidecars/qtrading-pg-sidecar[.exe]`` 二进制不存在；
    5. 安装目录下不存在 ``resources/maintenance``（区分标准版 cpu/cuda 安装包与 sidecar 被误删的 embedded-* 安装包）。
    """
    import sys

    if not getattr(sys, "frozen", False):
        return False
    explicit_mode = os.environ.get("QTRADING_DATABASE_MODE", "").strip().lower()
    if explicit_mode == "embedded":
        return False

    sidecar_cfg = ""
    data_root_cfg = ""
    if raw_config is not None:
        if isinstance(raw_config, Mapping):
            sidecar_cfg = str(raw_config.get("embedded_pg_sidecar_path") or "")
            data_root_cfg = str(raw_config.get("embedded_pg_data_root") or "")
        else:
            sidecar_cfg = str(getattr(raw_config, "embedded_pg_sidecar_path", "") or "")
            data_root_cfg = str(getattr(raw_config, "embedded_pg_data_root", "") or "")
    if sidecar_cfg or data_root_cfg:
        return False

    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return False
    meipass_path = Path(meipass)
    exe_suffix = ".exe" if os.name == "nt" else ""
    sidecar_path = meipass_path / "sidecars" / f"qtrading-pg-sidecar{exe_suffix}"
    if sidecar_path.exists():
        return False

    # MAJOR-2 守卫：installer.iss 仅对 embedded-cpu / embedded-cuda 变体安装 {app}/resources/maintenance，
    # 若该目录存在说明原本是 embedded 变体但 sidecar 二进制丢失/被杀软隔离，禁止静默降级
    return not (
        (meipass_path.parent / "resources" / "maintenance").is_dir()
        or (meipass_path / "resources" / "maintenance").is_dir()
    )


def is_embedded_mode() -> bool:
    """判断当前进程是否按内置 PostgreSQL 模式运行（R-B1 单一入口点）。

    判定统一委托 :func:`resolve_database_mode`，保证「设置页展示的模式」与
    「启动期实际生效的模式」不会出现两套判定。
    """
    return resolve_database_mode().mode is DatabaseMode.EMBEDDED


def save_database_mode(mode: str | DatabaseMode) -> bool:
    """持久化下一次启动希望使用的数据库模式（需重启应用后生效）。

    ``db_mode`` 与 ``embedded_pg_enabled`` 同步写入，兼容仅读取
    ``embedded_pg_enabled`` 的既有代码路径。返回严格布尔结果，调用方不得
    把非 ``True`` 返回值当作保存成功。
    """
    normalized = mode if isinstance(mode, DatabaseMode) else _parse_mode(mode)
    if normalized is None:
        raise ValueError(f"unsupported database mode: {mode!r}")
    saved = cfg.ConfigHandler.save_config(
        {
            "db_mode": normalized.value,
            "embedded_pg_enabled": normalized is DatabaseMode.EMBEDDED,
        }
    )
    return saved is True


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
        from utils.config.secrets import (
            SaveOutcome,
        )  # lazy-import: 避免 config_handler↔db 循环内再引入 secrets 顶部依赖

        outcome = cfg.ConfigHandler.save_db_password(password)
        # save_db_password 现返回 SaveOutcome（StrEnum，任何值均 truthy），不能直接作 bool 返回
        # （否则 FAILED / FAILED_NO_SECURE_STORE 会被误判为成功，重新引入 D8-2 假报成功）。
        # OVERRIDDEN_BY_ENV 视为成功：DB_PASSWORD 环境变量优先，get_db_password 运行时仍可读到密码。
        return outcome in (SaveOutcome.SAVED, SaveOutcome.OVERRIDDEN_BY_ENV)
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
