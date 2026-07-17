"""AutomationSettingsViewModel — AutomationTab/NotificationsTab 配置编排 ViewModel (Task 5.2).

承担自动化任务/通知设置中的计划任务/新闻提醒业务编排（CLAUDE.md §3.2 MVVM）。

设计要点：
- frozen state snapshot (AutomationSettingsState dataclass)
- subscribe/_notify 通知机制
- commands 作为 async 实例方法 (save_auto_update_enabled/save_auto_update_time/
  save_ai_concept_enabled/save_ai_concept_time/save_ai_concept_engine/
  save_news_enabled/save_news_interval)
- 同步阻塞 ConfigHandler 写入通过 ThreadPoolManager.run_async offload (R16)
- R2: asyncio.CancelledError 显式 raise
- 重复提交检测：is_saving=True 时拒绝新提交

不感知 locale：状态字段为字符串/布尔值，View 渲染时按当前 locale 翻译。
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutomationSettingsState:
    """AutomationSettingsViewModel 的不可变 state snapshot。"""

    # 计划任务
    auto_enabled: bool = False
    auto_time: str = "16:30"
    # AI 概念任务
    ai_enabled: bool = False
    ai_time: str = "20:00"
    ai_engine: str = "search_std"
    # 新闻提醒
    news_enabled: bool = True
    news_interval: str = "60"
    # 保存中标志
    is_saving: bool = False


class AutomationSettingsViewModel(ObservableViewModelMixin[AutomationSettingsState]):
    """AutomationTab/NotificationsTab 配置编排 ViewModel。

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (AutomationSettingsState) via subscribe/_notify
    - Commands as async instance methods
    - 同步阻塞 IO 通过 ThreadPoolManager.run_async offload (R16)
    - R2: CancelledError 显式 raise, 不被 except Exception 吞没
    - 重复提交检测：is_saving=True 时拒绝新提交

    消费方 (AutomationTab/NotificationsTab) 通过 use_viewmodel(factory=) 内部模式实例化 VM。
    """

    def __init__(self) -> None:
        self._state = AutomationSettingsState()
        self._subscribers: list[Callable[[AutomationSettingsState], None]] = []
        self._load_config_to_state()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步, 仅在 __init__ 调用一次）。"""
        enable_news = ConfigHandler.get_config("enable_news_alerts", True)
        news_interval = ConfigHandler.get_config("news_poll_interval", 60)
        self._state = AutomationSettingsState(
            auto_enabled=ConfigHandler.is_auto_update_enabled(),
            auto_time=ConfigHandler.get_auto_update_time(),
            ai_enabled=ConfigHandler.is_ai_concept_schedule_enabled(),
            ai_time=ConfigHandler.get_ai_concept_schedule_time(),
            ai_engine=ConfigHandler.get_ai_concept_search_engine(),
            news_enabled=bool(enable_news),
            news_interval=str(news_interval),
            is_saving=False,
        )

    # --- Update commands (View 通过 set_* 更新本地 state) ---

    def set_auto_enabled(self, value: bool) -> None:
        self._set_state(auto_enabled=value)

    def set_auto_time(self, value: str) -> None:
        self._set_state(auto_time=value)

    def set_ai_enabled(self, value: bool) -> None:
        self._set_state(ai_enabled=value)

    def set_ai_time(self, value: str) -> None:
        self._set_state(ai_time=value)

    def set_ai_engine(self, value: str) -> None:
        self._set_state(ai_engine=value)

    def set_news_enabled(self, value: bool) -> None:
        self._set_state(news_enabled=value)

    def set_news_interval(self, value: str) -> None:
        self._set_state(news_interval=value)

    # --- Async save commands (R16: IO offload via ThreadPoolManager) ---

    async def save_auto_update_enabled(self, new_enabled: bool) -> bool:
        """保存自动更新开关。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_config,
                {"auto_update_enabled": new_enabled},
            )
            if not success:
                logger.warning(
                    "[AutomationSettingsVM] save_config returned False for auto_update_enabled=%s",
                    new_enabled,
                )
                return False
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] schedule toggle save failed: %s",
                ex,
                exc_info=True,
            )
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_auto_update_time(self, new_time: str) -> bool:
        """保存自动更新时间。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_config,
                {"auto_update_time": new_time},
            )
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[AutomationSettingsVM] schedule time save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_ai_concept_enabled(self, new_enabled: bool) -> bool:
        """保存 AI 概念任务开关。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.set_ai_concept_schedule_enabled,
                new_enabled,
            )
            if not success:
                logger.warning(
                    "[AutomationSettingsVM] set_ai_concept_schedule_enabled returned False for enabled=%s",
                    new_enabled,
                )
                return False
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] ai concept toggle save failed: %s",
                ex,
                exc_info=True,
            )
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_ai_concept_time(self, new_time: str) -> bool:
        """保存 AI 概念任务时间。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.set_ai_concept_schedule_time,
                new_time,
            )
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[AutomationSettingsVM] ai concept time save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_ai_concept_engine(self, new_engine: str) -> bool:
        """保存 AI 概念任务搜索引擎。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.set_ai_concept_search_engine,
                new_engine,
            )
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] ai concept search engine save failed: %s",
                ex,
                exc_info=True,
            )
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_news_enabled(self, new_enabled: bool) -> bool:
        """保存新闻提醒开关。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_config,
                {"enable_news_alerts": new_enabled},
            )
            if not success:
                logger.warning(
                    "[AutomationSettingsVM] save_config returned False for enable_news_alerts=%s",
                    new_enabled,
                )
                return False
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[AutomationSettingsVM] news toggle save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_news_interval(self, new_val: str) -> bool:
        """保存新闻拉取间隔 (秒, 字符串解析为 int)。"""
        if self._state.is_saving:
            return False
        try:
            val = int(new_val)
        except (ValueError, TypeError):
            return False
        self._set_state(is_saving=True)
        try:
            await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_config,
                {"news_poll_interval": val},
            )
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[AutomationSettingsVM] interval save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)
