import logging
from typing import TypedDict

from data.domain_services.market_data_service import MarketDataService
from services.news_subscription_service import NewsSubscriptionService
from data.persistence.db_migrator import DatabaseMigrationNeeded
from data.persistence.metadata_manager import MetaDataManager
from services.task_manager import TaskManager
from utils.scheduler_service import SchedulerService
from core.i18n import I18n

logger = logging.getLogger(__name__)


class InitResult(TypedDict):
    success: bool
    error: str | None
    detail: str | None
    current_rev: str | None
    head_rev: str | None


async def initialize_services(cache_manager, show_toast_fn=None) -> InitResult:
    from utils.correlation import ensure_correlation_id

    ensure_correlation_id()

    try:
        await cache_manager.init_db()
    except DatabaseMigrationNeeded as e:
        logger.warning(f"[Bootstrap] Database needs migration: {e}")
        return {
            "success": False,
            "error": "db_upgrade_needed",
            "detail": str(e),
            "current_rev": e.current_rev,
            "head_rev": e.head_rev,
        }
    except Exception as e:
        logger.error(f"[Bootstrap] Database initialization failed: {e}", exc_info=True)
        if show_toast_fn:
            show_toast_fn(I18n.get("error_db_init_failed"), "error")
        return {"success": False, "error": "db_init_failed", "detail": str(e), "current_rev": None, "head_rev": None}

    MetaDataManager.preload_aliases()

    if cache_manager.engine is None:
        logger.error("[Bootstrap] Database engine not created after init_db().")
        if show_toast_fn:
            show_toast_fn(I18n.get("error_db_engine_missing"), "error")
        return {"success": False, "error": "db_engine_missing", "detail": None, "current_rev": None, "head_rev": None}

    try:
        await TaskManager().init_db()
    except Exception as e:
        logger.error(f"[Bootstrap] TaskManager init failed: {e}", exc_info=True)
        if show_toast_fn:
            show_toast_fn(I18n.get("error_task_manager_init_failed"), "error")
        return {
            "success": False,
            "error": "task_manager_init_failed",
            "detail": str(e),
            "current_rev": None,
            "head_rev": None,
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

    return {"success": True, "error": None, "detail": None, "current_rev": None, "head_rev": None}


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
            logger.info(f"[Bootstrap] Loaded {len(cache)} Tushare capabilities from AppState")
        else:
            logger.debug("[Bootstrap] Tushare capability cache empty after load (first startup or token changed)")
    except Exception as e:
        logger.warning(f"[Bootstrap] Tushare capability warmup failed (non-critical): {e}")


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
        logger.debug(f"[Bootstrap] Failover credential validation skipped: {e}")


def check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
    return not db_url or not token or not llm_api_key or not onboarding_complete


def mask_sensitive(value, prefix_len=4):
    if value and len(value) > prefix_len:
        return f"{value[:prefix_len]}****"
    return "None"
