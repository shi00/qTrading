import logging

from data.domain_services.market_data_service import MarketDataService
from data.external.news_subscription import NewsSubscriptionService
from data.persistence.db_migrator import DatabaseMigrationNeeded
from data.persistence.metadata_manager import MetaDataManager
from services.task_manager import TaskManager
from utils.scheduler_service import SchedulerService

logger = logging.getLogger(__name__)


async def initialize_services(cache_manager, show_toast_fn=None):
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
            show_toast_fn(f"数据库初始化失败: {e}", "error")
        return {"success": False, "error": "db_init_failed", "detail": str(e)}

    MetaDataManager.preload_aliases()

    if cache_manager.engine is None:
        logger.error("[Bootstrap] Database engine not created after init_db().")
        if show_toast_fn:
            show_toast_fn("数据库引擎未创建，请检查配置", "error")
        return {"success": False, "error": "db_engine_missing"}

    try:
        await TaskManager().init_db()
    except Exception as e:
        logger.error(f"[Bootstrap] TaskManager init failed: {e}", exc_info=True)
        if show_toast_fn:
            show_toast_fn(f"TaskManager 初始化失败: {e}", "error")
        return {"success": False, "error": "task_manager_init_failed", "detail": str(e)}

    SchedulerService().start()
    NewsSubscriptionService().start()
    MarketDataService().start()

    return {"success": True}


def check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
    return not db_url or not token or not llm_api_key or not onboarding_complete


def mask_sensitive(value, prefix_len=4):
    if value and len(value) > prefix_len:
        return f"{value[:prefix_len]}****"
    return "None"
