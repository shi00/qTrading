import asyncio
import logging
from datetime import timedelta
from typing import TypedDict

from data.domain_services.market_data_service import MarketDataService
from services.news_subscription_service import NewsSubscriptionService
from data.persistence.db_migrator import DatabaseMigrationNeeded
from data.persistence.metadata_manager import MetaDataManager
from services.task_manager import TaskManager
from utils.scheduler_service import SchedulerService
from core.i18n import I18n

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
            "detail": str(e),
            "current_rev": e.current_rev,
            "head_rev": e.head_rev,
            "auto_probe_task": None,
        }
    except Exception as e:
        logger.error("[Bootstrap] Database initialization failed: %s", e, exc_info=True)
        if show_toast_fn:
            show_toast_fn(I18n.get("error_db_init_failed"), "error")
        return {
            "success": False,
            "error": "db_init_failed",
            "detail": str(e),
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
        logger.error("[Bootstrap] TaskManager init failed: %s", e, exc_info=True)
        if show_toast_fn:
            show_toast_fn(I18n.get("error_task_manager_init_failed"), "error")
        return {
            "success": False,
            "error": "task_manager_init_failed",
            "detail": str(e),
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
        logger.warning("[Bootstrap] Tushare capability warmup failed (non-critical): %s", e)


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
        logger.debug("[Bootstrap] Failover credential validation skipped: %s", e)


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

        last_probe = client._last_probe_time  # noqa: SLF001 — 同包内访问，避免新增公共 getter
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
        from utils.sanitizers import DataSanitizer

        logger.warning("[Bootstrap] Auto probe failed (non-critical): %s", DataSanitizer.sanitize_error(e))


def _validate_strategy_tier_coverage() -> None:
    """Phase 2A.1 Task 2A.1.10：启动期校验已注册策略是否都在 _STRATEGY_MIN_TIER 中登记。

    委托给 ``services/ai_service.validate_strategy_tier_coverage`` 实现（R1 红线：
    app/ 可引用 services/，但 strategies/ 不可引用 services/，因此校验逻辑必须经
    app/ 中转）。warning 不 raise，避免阻断启动。
    """
    try:
        from services.ai_service import validate_strategy_tier_coverage

        validate_strategy_tier_coverage()
    except Exception as e:
        logger.warning("[Bootstrap] validate_strategy_tier_coverage skipped: %s", e)


def check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
    return not db_url or not token or not llm_api_key or not onboarding_complete


def mask_sensitive(value, prefix_len=4):
    if value and len(value) > prefix_len:
        return f"{value[:prefix_len]}****"
    return "None"
