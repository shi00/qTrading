"""AsyncEngine 生命周期管理（review01-A4 Step2 拆分）。

从 ``CacheManager`` 拆出引擎创建/销毁职责：连接串解析、AsyncEngine 创建与 dispose、
``engine_provider`` 引用同步（R5 守卫状态）。``CacheManager`` 作为组合根持有本管理器，
自身保留 ``self.engine`` 便捷引用与业务方法（调用方零变更）。
"""

from __future__ import annotations

import logging
import re

import config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from data.persistence import engine_provider
from utils.config_handler import ConfigHandler
from utils.db_utils import get_db_pool_config

logger = logging.getLogger(__name__)


class EngineManager:
    """AsyncEngine 生命周期管理（创建 / dispose / 连接串解析 / engine_provider 同步）。"""

    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None

    def get_connection_string(self) -> str | None:
        """Get database connection string from config."""
        if hasattr(ConfigHandler, "get_db_url"):
            url = ConfigHandler.get_db_url()
            if url:
                return url
        return config.DB_URL

    @staticmethod
    def sanitize_url(url: str) -> str:
        """Sanitize URL for logging (hide password)."""
        if not url:
            return "None"
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)

    def create_engine(self, connection_string: str) -> None:
        """Create async engine and sync engine_provider (R5 守卫恢复可用状态)."""
        # review03-C11 Step2: 同步 engine_provider，DAO 侧 R5 守卫恢复可用状态
        engine_provider.mark_disposed(False)

        pool_config = get_db_pool_config()

        self.engine = create_async_engine(
            connection_string,
            echo=False,
            future=True,
            **pool_config,
        )
        # review03-C11 Step2: 记录引擎引用（语义化标记，与 disposed 状态同临界区维护）
        engine_provider.set_engine(self.engine)
        logger.debug("[CacheManager] Engine created: %s", self.sanitize_url(connection_string))

    async def dispose(self) -> None:
        """Dispose engine and clear engine_provider reference.

        ``_disposed`` 布尔标记由组合根（CacheManager.close）维护——它同时管理
        maintenance event 与 DAO.engine 清空；本管理器仅负责引擎对象本身。
        """
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            # review03-C11 Step2: 引擎引用已释放，同步清空 provider 标记
            engine_provider.set_engine(None)
