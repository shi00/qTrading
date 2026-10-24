"""AutomationSettingsViewModel — AutomationTab/NotificationsTab 配置编排 ViewModel (Task 5.2).

承担自动化任务/通知设置中的计划任务/新闻提醒业务编排（CLAUDE.md §3.2 MVVM）。

设计要点：
- frozen state snapshot (AutomationSettingsState dataclass)
- subscribe/_notify 通知机制
- commands 作为 async 实例方法 (save_auto_update_enabled/save_auto_update_time/
  save_concept_enabled/save_concept_time/
  save_news_enabled/save_news_interval)
- 同步阻塞 ConfigHandler 写入通过 ThreadPoolManager.run_async offload (R16)
- R2: asyncio.CancelledError 显式 raise
- 重复提交检测：is_saving=True 时拒绝新提交

不感知 locale：状态字段为字符串/布尔值，View 渲染时按当前 locale 翻译。
"""

import asyncio
import logging
from dataclasses import dataclass

from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AutomationSettingsState:
    """AutomationSettingsViewModel 的不可变 state snapshot。"""

    # 计划任务
    auto_enabled: bool = False
    auto_time: str = "16:30"
    # 概念同步任务
    concept_enabled: bool = False
    concept_time: str = "20:00"
    # Task 7.3: 夜间 AI 预测时辰 (与 scheduler_service 硬编码 20:30 对齐, 提升为可配项)
    nightly_prediction_time: str = "20:30"
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
        self._init_mixin_fields()
        self._load_config_to_state()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步, 仅在 __init__ 调用一次）。"""
        enable_news = ConfigHandler.get_config("enable_news_alerts", True)
        news_interval = ConfigHandler.get_config("news_poll_interval", 60)
        self._state = AutomationSettingsState(
            auto_enabled=ConfigHandler.is_auto_update_enabled(),
            auto_time=ConfigHandler.get_auto_update_time(),
            concept_enabled=ConfigHandler.is_concept_schedule_enabled(),
            concept_time=ConfigHandler.get_concept_schedule_time(),
            nightly_prediction_time=ConfigHandler.get_nightly_prediction_time(),
            news_enabled=bool(enable_news),
            news_interval=str(news_interval),
            is_saving=False,
        )

    # --- Update commands (View 通过 set_* 更新本地 state) ---

    def set_auto_enabled(self, value: bool) -> None:
        self._set_state(auto_enabled=value)

    def set_auto_time(self, value: str) -> None:
        self._set_state(auto_time=value)

    def set_concept_enabled(self, value: bool) -> None:
        self._set_state(concept_enabled=value)

    def set_concept_time(self, value: str) -> None:
        self._set_state(concept_time=value)

    def set_nightly_prediction_time(self, value: str) -> None:
        """Task 7.3: 更新本地 state 的夜间预测时辰 (View 乐观更新)."""
        self._set_state(nightly_prediction_time=value)

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
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_auto_update_time(self, new_time: str) -> bool:
        """保存自动更新时间。

        P-AUTO-01: 返回前统一回读磁盘对齐 state — 成功时写盘已同步完成, 读数必为
        新值; 失败/异常/并发早退时磁盘未被本次改写, 读数为旧值 → 回滚 View 的
        乐观更新, 保证「返回 False 时 state 与磁盘一致」, 消除 UI 显示未生效值
        的漂移 (重启后跳变)。
        """
        if self._state.is_saving:
            self._set_state(auto_time=ConfigHandler.get_auto_update_time())
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_config,
                {"auto_update_time": new_time},
            )
            if not success:
                logger.warning(
                    "[AutomationSettingsVM] save_config returned False for auto_update_time=%s",
                    new_time,
                )
            self._set_state(auto_time=ConfigHandler.get_auto_update_time())
            return success
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] schedule time save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
            )
            self._set_state(auto_time=ConfigHandler.get_auto_update_time())
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_concept_enabled(self, new_enabled: bool) -> bool:
        """保存概念同步任务开关。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.set_concept_schedule_enabled,
                new_enabled,
            )
            if not success:
                logger.warning(
                    "[AutomationSettingsVM] set_concept_schedule_enabled returned False for enabled=%s",
                    new_enabled,
                )
                return False
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] concept toggle save failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_concept_time(self, new_time: str) -> bool:
        """保存概念同步任务时间。

        P-AUTO-01: 返回前统一回读磁盘对齐 state (同 save_auto_update_time 契约)。
        """
        if self._state.is_saving:
            self._set_state(concept_time=ConfigHandler.get_concept_schedule_time())
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.set_concept_schedule_time,
                new_time,
            )
            if not success:
                logger.warning(
                    "[AutomationSettingsVM] set_concept_schedule_time returned False for time=%s",
                    new_time,
                )
            self._set_state(concept_time=ConfigHandler.get_concept_schedule_time())
            return success
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] concept time save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
            )
            self._set_state(concept_time=ConfigHandler.get_concept_schedule_time())
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_nightly_prediction_time(self, new_time: str) -> bool:
        """Task 7.3: 保存夜间 AI 预测时辰。

        P-AUTO-01: 返回前统一回读磁盘对齐 state (同 save_auto_update_time 契约)。
        """
        if self._state.is_saving:
            self._set_state(nightly_prediction_time=ConfigHandler.get_nightly_prediction_time())
            return False
        self._set_state(is_saving=True)
        try:
            result = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.set_nightly_prediction_time,
                new_time,
            )
            if not result:
                logger.warning(
                    "[AutomationSettingsVM] set_nightly_prediction_time returned False for time=%s",
                    new_time,
                )
            self._set_state(nightly_prediction_time=ConfigHandler.get_nightly_prediction_time())
            return bool(result)
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] nightly prediction time save failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._set_state(nightly_prediction_time=ConfigHandler.get_nightly_prediction_time())
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
            logger.error(
                "[AutomationSettingsVM] news toggle save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
            )
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_news_interval(self, new_val: str) -> bool:
        """保存新闻拉取间隔 (秒, 字符串解析为 int)。

        P-AUTO-01: 返回前统一回读磁盘对齐 state (同 save_auto_update_time 契约)。
        """
        if self._state.is_saving:
            self._set_state(news_interval=str(ConfigHandler.get_config("news_poll_interval", 60)))
            return False
        try:
            val = int(new_val)
        except (ValueError, TypeError):
            # 解析失败时 View 已乐观更新 state, 同样回读磁盘对齐 (统一契约)
            self._set_state(news_interval=str(ConfigHandler.get_config("news_poll_interval", 60)))
            return False
        # M14-F1 成对写侧防御: 本路径 KV 直写 (save_config) 绕过 app_prefs
        # set_news_poll_interval 的 max(10) 钳制; 1-9s 正数会绕过消费侧 <=0
        # 回退直接生效为高频轮询, 故写侧补同值下界 (消费侧保留失真兜底)。
        val = max(10, val)
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_config,
                {"news_poll_interval": val},
            )
            if not success:
                logger.warning(
                    "[AutomationSettingsVM] save_config returned False for news_poll_interval=%s",
                    val,
                )
            self._set_state(news_interval=str(ConfigHandler.get_config("news_poll_interval", 60)))
            return success
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error(
                "[AutomationSettingsVM] interval save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
            )
            self._set_state(news_interval=str(ConfigHandler.get_config("news_poll_interval", 60)))
            return False
        finally:
            self._set_state(is_saving=False)
