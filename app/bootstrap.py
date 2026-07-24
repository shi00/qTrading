from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from data.domain_services.market_data_service import MarketDataService
from services.news_subscription_service import NewsSubscriptionService
from data.persistence.db_migrator import DatabaseMigrationNeeded
from data.persistence.metadata_manager import MetaDataManager
from services.task_manager import TaskManager
from utils.error_classifier import classify_error, classify_severity
from utils.sanitizers import DataSanitizer
from utils.scheduler_service import SchedulerService
from core.i18n import I18n

if TYPE_CHECKING:
    from utils.config_models import AppConfig

logger = logging.getLogger(__name__)


# Phase 2A.1 §3.2.10：距上次 probe 超过此阈值时启动期自动触发 probe
_AUTO_PROBE_INTERVAL = timedelta(days=7)


class InitResult(TypedDict):
    success: bool
    error: str | None
    detail: str | None
    current_rev: str | None
    head_rev: str | None
    # Phase 2A.1 §3.2.9：启动期自动 probe 任务（fire-and-forget），
    # 由 main.py 注册到 ShutdownCoordinator 以便关机时取消
    auto_probe_task: asyncio.Task | None


async def initialize_services(cache_manager, show_toast_fn=None) -> InitResult:
    from utils.correlation import ensure_correlation_id

    ensure_correlation_id()

    try:
        await cache_manager.init_db()
    except DatabaseMigrationNeeded as e:
        logger.warning("[Bootstrap] Database needs migration: %s", e)
        return {
            "success": False,
            "error": "db_upgrade_needed",
            "detail": DataSanitizer.sanitize_error(e),
            "current_rev": e.current_rev,
            "head_rev": e.head_rev,
            "auto_probe_task": None,
        }
    except Exception as e:
        error_info = classify_error(e, context="general")
        severity = classify_severity(e, context="general")
        if severity == "system":
            _log = logger.critical
        elif severity == "recoverable":
            _log = logger.warning
        else:
            _log = logger.error
        _log(
            "[Bootstrap] Database initialization failed (%s): %s",
            error_info["code"],
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )
        if show_toast_fn:
            show_toast_fn(I18n.get("error_db_init_failed"), "error")
        return {
            "success": False,
            "error": "db_init_failed",
            "detail": DataSanitizer.sanitize_error(e),
            "current_rev": None,
            "head_rev": None,
            "auto_probe_task": None,
        }

    MetaDataManager.preload_aliases()

    if cache_manager.engine is None:
        logger.error("[Bootstrap] Database engine not created after init_db().")
        if show_toast_fn:
            show_toast_fn(I18n.get("error_db_engine_missing"), "error")
        return {
            "success": False,
            "error": "db_engine_missing",
            "detail": None,
            "current_rev": None,
            "head_rev": None,
            "auto_probe_task": None,
        }

    try:
        await TaskManager().init_db()
    except Exception as e:
        error_info = classify_error(e, context="general")
        severity = classify_severity(e, context="general")
        if severity == "system":
            _log = logger.critical
        elif severity == "recoverable":
            _log = logger.warning
        else:
            _log = logger.error
        _log(
            "[Bootstrap] TaskManager init failed (%s): %s",
            error_info["code"],
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )
        if show_toast_fn:
            show_toast_fn(I18n.get("error_task_manager_init_failed"), "error")
        return {
            "success": False,
            "error": "task_manager_init_failed",
            "detail": DataSanitizer.sanitize_error(e),
            "current_rev": None,
            "head_rev": None,
            "auto_probe_task": None,
        }

    import os

    if os.environ.get("E2E_TESTING") == "true":
        logger.info("[Bootstrap] E2E testing mode detected, skipping background scheduler and data polling services.")
    else:
        SchedulerService().start()
        await NewsSubscriptionService().start()
        await MarketDataService().start()

    await _warmup_tushare_capabilities()

    _validate_failover_credentials()

    # Phase 2A.1 Task 2A.1.10：启动期校验策略档位覆盖（warning 不 raise）
    _validate_strategy_tier_coverage()

    # Phase 2A.1 Task 2A.1.8：启动期自动 probe（fire-and-forget）
    auto_probe_task = asyncio.create_task(_maybe_auto_probe_on_startup())

    return {
        "success": True,
        "error": None,
        "detail": None,
        "current_rev": None,
        "head_rev": None,
        "auto_probe_task": auto_probe_task,
    }


async def _warmup_tushare_capabilities() -> None:
    """
    Warm up Tushare capability cache from AppState on startup.

    This ensures that API availability status persists across restarts,
    avoiding repeated probe calls that waste API quota.
    """
    from data.external.tushare_client import TushareClient

    client = TushareClient()
    if not client.token:
        logger.debug("[Bootstrap] No Tushare token configured, skipping capability warmup")
        return

    try:
        await client.load_capabilities_from_app_state()

        cache = client.get_capability_cache()
        if cache:
            logger.info("[Bootstrap] Loaded %s Tushare capabilities from AppState", len(cache))
        else:
            logger.debug("[Bootstrap] Tushare capability cache empty after load (first startup or token changed)")
    except Exception as e:
        error_info = classify_error(e, context="general")
        severity = classify_severity(e, context="general")
        if severity == "system":
            _log = logger.critical
        elif severity == "recoverable":
            _log = logger.warning
        else:
            _log = logger.error
        _log(
            "[Bootstrap] Tushare capability warmup failed (non-critical) (%s): %s",
            error_info["code"],
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )


def _validate_failover_credentials() -> None:
    """
    Validate failover provider credentials on startup.

    Logs a warning if any failover provider is missing API key.
    """
    from utils.config_handler import ConfigHandler

    try:
        missing = ConfigHandler.validate_failover_credentials()
        if missing:
            logger.warning(
                "[Bootstrap] Failover providers missing credentials: %s. Cross-provider fallback may fail.",
                ", ".join(missing),
            )
    except Exception as e:
        error_info = classify_error(e, context="general")
        severity = classify_severity(e, context="general")
        if severity == "system":
            _log = logger.critical
        elif severity == "recoverable":
            _log = logger.warning
        else:
            _log = logger.error
        _log(
            "[Bootstrap] Failover credential validation skipped (%s): %s",
            error_info["code"],
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )


async def _maybe_auto_probe_on_startup() -> None:
    """Phase 2A.1 Task 2A.1.8：启动期自动 probe（fire-and-forget，不阻塞 UI）。

    决策逻辑：
    1. Token 未配置时短路跳过（不读 AppState，避免无谓 IO）
    2. 距上次 probe > 7 天（``_AUTO_PROBE_INTERVAL``）时触发 ``probe_api_capabilities``
    3. 失败降级 ``warning`` 日志（不 raise，不影响主流程）
    4. CancelledError 必须 raise（R2 红线，配合优雅停机）

    本函数返回的 Task 由 ``initialize_services`` 保存到 ``InitResult.auto_probe_task``，
    再由 main.py 注册到 ``ShutdownCoordinator`` 以便关机时取消。
    """
    from data.external.tushare_client import TushareClient
    from utils.time_utils import get_now

    try:
        client = TushareClient()
        if not client.token:
            logger.debug("[Bootstrap] No Tushare token configured, skipping auto probe")
            return

        last_probe = client.get_last_probe_time()
        if last_probe is not None and (get_now() - last_probe) < _AUTO_PROBE_INTERVAL:
            logger.debug(
                "[Bootstrap] Last probe %s within %s days, skipping auto probe",
                last_probe.isoformat(),
                _AUTO_PROBE_INTERVAL.days,
            )
            return

        logger.info(
            "[Bootstrap] Auto probe triggered (last_probe=%s)",
            last_probe.isoformat() if last_probe else "never",
        )
        await client.probe_api_capabilities()
    except asyncio.CancelledError:
        # R2 红线：CancelledError 必须 raise 以配合优雅停机
        raise
    except Exception as e:
        # 非取消异常降级 warning，不影响主流程
        error_info = classify_error(e, context="general")
        severity = classify_severity(e, context="general")
        if severity == "system":
            _log = logger.critical
        elif severity == "recoverable":
            _log = logger.warning
        else:
            _log = logger.error
        _log(
            "[Bootstrap] Auto probe failed (non-critical) (%s): %s",
            error_info["code"],
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )


def _validate_strategy_tier_coverage() -> None:
    """Phase 2A.1 Task 2A.1.10：启动期校验已注册策略是否都在 _STRATEGY_MIN_TIER 中登记。

    R1 红线：services/ 不可导入 strategies/（反向依赖禁止），因此由 app/ 层（可同时
    引用 services/ 和 strategies/）查询 ``StrategyManager().strategies.keys()`` 后
    注入 ``services.ai_service.validate_strategy_tier_coverage``。warning 不 raise，
    避免阻断启动。
    """
    try:
        from services.ai_service import validate_strategy_tier_coverage
        from strategies.all_strategies import StrategyManager

        registered_keys = set(StrategyManager().strategies.keys())
    except Exception as e:
        error_info = classify_error(e, context="general")
        severity = classify_severity(e, context="general")
        if severity == "system":
            _log = logger.critical
        elif severity == "recoverable":
            _log = logger.warning
        else:
            _log = logger.error
        _log(
            "[Bootstrap] validate_strategy_tier_coverage skipped (%s): %s",
            error_info["code"],
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )
        return

    try:
        validate_strategy_tier_coverage(registered_keys)
    except Exception as e:
        error_info = classify_error(e, context="general")
        severity = classify_severity(e, context="general")
        if severity == "system":
            _log = logger.critical
        elif severity == "recoverable":
            _log = logger.warning
        else:
            _log = logger.error
        _log(
            "[Bootstrap] validate_strategy_tier_coverage failed (%s): %s",
            error_info["code"],
            DataSanitizer.sanitize_error(e),
            exc_info=True,
        )


def check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
    return not db_url or not token or not llm_api_key or not onboarding_complete


def mask_sensitive(value):
    """R9 一致性：复用 DataSanitizer.sanitize_token 替换私有前缀脱敏实现。

    旧实现固定泄露 token 前 4 字符，对短 token 仍泄露显著片段；改用 sanitize_token
    后短 token（< 32）全部隐藏为 ***，长 token 部分脱敏（前 3 + *** + 后 4）。
    """
    return DataSanitizer.sanitize_token(value)


async def prepare_database_runtime() -> str | None:
    """根据数据库模式准备运行时（Phase 2 §3.4）。

    - embedded: 启动 EmbeddedPostgresService → 返回 ``info.url``，由调用方用
      ``override_db_url(target_url)`` 包裹 ``CacheManager()`` 构造（D15：不再持久化到 config）
    - external: 返回 ``None``（沿用既有 DATABASE_URL/db_* 配置）

    必须在 ``CacheManager()`` 之前调用（CacheManager 构造时建引擎）。

    模式判定：``QTRADING_DATABASE_MODE`` 环境变量（embedded|external，默认 external）。

    Returns:
        embedded 模式且启动成功时返回 sidecar ``ConnectionInfo.url``；
        external 模式 / 未启用 / 跳过时返回 ``None``。

    Raises:
        EmbeddedPostgresStartError: sidecar 启动失败时透传（不吞没，R2 红线要求
            CancelledError 也透传）。
    """
    import os

    mode = os.environ.get("QTRADING_DATABASE_MODE", "external").lower()
    if mode != "embedded":
        # M5: mode=external 但 config.embedded_pg_enabled=True → 记 WARNING（用户可能误配置）
        from utils.config_handler import ConfigHandler
        from utils.config_models import AppConfig

        config = AppConfig.model_validate(ConfigHandler.load_config())
        if config.embedded_pg_enabled:
            logger.warning(
                "[Bootstrap] QTRADING_DATABASE_MODE=%s but embedded_pg_enabled=True; "
                "embedded PostgreSQL service will NOT start (external mode takes precedence)",
                mode,
            )
        return None

    from data.persistence.embedded_postgres.service import EmbeddedPostgresService
    from utils.config_handler import ConfigHandler
    from utils.config_models import AppConfig

    config = AppConfig.model_validate(ConfigHandler.load_config())
    if not config.embedded_pg_enabled:
        logger.warning("[Bootstrap] QTRADING_DATABASE_MODE=embedded but embedded_pg_enabled=False; skip")
        return None

    service = EmbeddedPostgresService.from_config(config)
    # H3: start 失败时清理单例，避免后续 CacheManager 误用残留状态
    try:
        info = await service.start()
    except Exception:
        EmbeddedPostgresService._reset_singleton()
        raise
    logger.info(
        "[Bootstrap] embedded postgres ready on %s:%s",
        config.embedded_pg_listen,
        info.port,
    )
    # D15（pg-plan §22）：返回 URL 供调用方用 override_db_url 包裹 CacheManager 构造，
    # 不再调 ConfigHandler.save_db_config 持久化（embedded URL 不应写 config）。
    return info.url


class EmbeddedPgStartupScenario(Enum):
    """Embedded PostgreSQL 启动场景（UX 改进 spec §启动侧方案 A）。

    用于 LoadingView 差异化文案：
    - ``FIRST_RUN``: 首次启动（需解压 bundled binaries + initdb，预计 30-60s）
    - ``NORMAL``: 普通启动（仅 PG 启动+健康检查，预计 2-5s）
    - ``UNKNOWN``: 异常状态（marker 与 PG_VERSION 不一致，保守按 NORMAL 文案显示）
    """

    FIRST_RUN = "first_run"
    NORMAL = "normal"
    UNKNOWN = "unknown"


def detect_embedded_pg_startup_scenario(config: AppConfig) -> EmbeddedPgStartupScenario | None:
    """检测 embedded PostgreSQL 启动场景，供 LoadingView 显示差异化文案。

    判定逻辑（spec §「Requirement: 启动场景检测」）：
    - ``QTRADING_DATABASE_MODE != "embedded"`` → 返回 ``None``（external 模式不检测）
    - ``config.embedded_pg_enabled == False`` → 返回 ``None``
    - 否则检查 ``<install_dir>/.setup-complete`` 与 ``<data_dir>/PG_VERSION`` 存在性：
      * 两者均不存在 → ``FIRST_RUN``
      * 两者均存在 → ``NORMAL``
      * 不一致 → ``UNKNOWN`` + WARNING 日志（不阻塞启动）

    路径解析复用 ``EmbeddedPostgresService.from_config`` 的逻辑：构造单例后读取
    ``_data_dir`` / ``_install_dir`` 私有属性。单例 idempotent，后续
    ``prepare_database_runtime`` 再次调用 ``from_config`` 会返回同一实例。

    Args:
        config: ``AppConfig`` 实例

    Returns:
        ``EmbeddedPgStartupScenario`` 枚举值；external 模式或未启用时返回 ``None``
    """
    mode = os.environ.get("QTRADING_DATABASE_MODE", "external").lower()
    if mode != "embedded":
        logger.debug("[Bootstrap] detect skipped: QTRADING_DATABASE_MODE=%s (not embedded)", mode)
        return None
    if not config.embedded_pg_enabled:
        logger.debug("[Bootstrap] detect skipped: embedded_pg_enabled=False")
        return None

    from data.persistence.embedded_postgres.service import EmbeddedPostgresService

    # 复用 from_config 路径解析；service 为单例，已初始化时 from_config 直接返回。
    service = EmbeddedPostgresService.from_config(config)
    install_marker = Path(service._install_dir) / ".setup-complete"  # type: ignore[attr-defined]  # [reason: EmbeddedPostgresService 未暴露公开 install_dir 属性，复用 from_config 路径解析需访问私有属性；后续可暴露公开属性重构]
    pg_version = Path(service._data_dir) / "PG_VERSION"  # type: ignore[attr-defined]  # [reason: 同上，复用 from_config 路径解析访问私有 data_dir]

    marker_exists = install_marker.exists()
    pg_version_exists = pg_version.exists()

    if not marker_exists and not pg_version_exists:
        return EmbeddedPgStartupScenario.FIRST_RUN
    if marker_exists and pg_version_exists:
        return EmbeddedPgStartupScenario.NORMAL
    logger.warning(
        "[Bootstrap] embedded PG startup scenario UNKNOWN: "
        "install_marker=%s (%s), pg_version=%s (%s); treating as NORMAL for UX",
        marker_exists,
        install_marker,
        pg_version_exists,
        pg_version,
    )
    return EmbeddedPgStartupScenario.UNKNOWN
