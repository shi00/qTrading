"""SystemSettingsViewModel — SystemTab 配置编排 ViewModel (Task 5.2).

承担 SystemTab 中语言/主题/线程池/DB pool/proxy 等业务编排（CLAUDE.md §3.2 MVVM）。

设计要点：
- frozen state snapshot (SystemSettingsState dataclass)
- subscribe/_notify 通知机制（View 通过 use_viewmodel 订阅触发重渲染）
- commands 作为实例方法（async, 在 Flet 事件循环中执行）
- 同步阻塞 ConfigHandler 写入通过 ThreadPoolManager.run_async offload (R16)
- R2: asyncio.CancelledError 显式 raise, 不被 except Exception 吞没
- 重复提交检测：is_saving=True 时拒绝新提交

不感知 locale：状态字段为字符串值（如 'zh_CN'），View 渲染时按当前 locale 翻译。

注：本 VM 仅承担 system_tab.py 中除 TierApiPanel 之外的配置编排。
档位/probe 编排保留在 SystemViewModel 中（职责分离）。
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


# --- Validation bounds (与原 system_tab 一致) ---
_CONCURRENCY_MIN = 1
_CONCURRENCY_MAX = 32
_DB_POOL_MIN = 1
_DB_POOL_MAX = 50
_DB_OVERFLOW_MIN = 0
_DB_OVERFLOW_MAX = 50
_DB_TIMEOUT_MIN = 5
_DB_TIMEOUT_MAX = 300
_IO_WORKERS_MIN = 4
_IO_WORKERS_MAX = 512
_CPU_WORKERS_MIN = 1
_CPU_WORKERS_MAX = 64


@dataclass(frozen=True)
class SystemSettingsState:
    """SystemSettingsViewModel 的不可变 state snapshot。

    所有字段为字符串（VM 不做类型转换），原始字符串保留以支持 View 显示
    中间输入态（如 '' / 'abc'）。validate/save 时按需解析为 int。
    """

    language_value: str = "zh_CN"
    theme_value: str = "dark"
    concurrency_value: str = "4"
    log_level_value: str = "INFO"
    pool_size_value: str = "5"
    db_overflow_value: str = "10"
    db_timeout_value: str = "30"
    io_workers_value: str = "8"
    cpu_workers_value: str = "4"
    no_proxy_value: str = ""
    is_saving: bool = False


class SystemSettingsViewModel(ObservableViewModelMixin[SystemSettingsState]):
    """SystemTab 配置编排 ViewModel。

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (SystemSettingsState) via subscribe/_notify
    - Commands as async instance methods (save_language/save_theme/...)
    - 同步阻塞 IO 通过 ThreadPoolManager.run_async offload (R16)
    - R2: CancelledError 显式 raise, 不被 except Exception 吞没
    - 重复提交检测：is_saving=True 时拒绝新提交

    消费方 (SystemTab) 通过 use_viewmodel(factory=) 内部模式实例化 VM，
    View 只通过 state.* 读取值，通过 commands 触发保存。
    """

    def __init__(self) -> None:
        # 默认使用模块级 ConfigHandler/ThreadPoolManager（与原 system_tab 行为一致）
        # 通过 _load_config_to_state 同步初始化 state
        self._state = SystemSettingsState()
        self._subscribers: list[Callable[[SystemSettingsState], None]] = []
        self._load_config_to_state()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步, 仅在 __init__ 调用一次）。"""
        self._state = SystemSettingsState(
            language_value=ConfigHandler.get_locale(),
            theme_value=ConfigHandler.get_theme_name(),
            concurrency_value=str(ConfigHandler.get_sync_max_concurrent_heavy()),
            log_level_value=ConfigHandler.get_log_level(),
            pool_size_value=str(ConfigHandler.get_db_connection_pool_size()),
            db_overflow_value=str(ConfigHandler.get_db_max_overflow()),
            db_timeout_value=str(ConfigHandler.get_db_pool_timeout()),
            io_workers_value=str(ConfigHandler.get_max_io_workers()),
            cpu_workers_value=str(ConfigHandler.get_max_cpu_workers()),
            no_proxy_value=",".join(ConfigHandler.get_no_proxy_domains()),
            is_saving=False,
        )

    # --- Update commands (View 通过 set_* 更新本地 state) ---

    def set_language_value(self, value: str) -> None:
        self._set_state(language_value=value)

    def set_theme_value(self, value: str) -> None:
        self._set_state(theme_value=value)

    def set_concurrency_value(self, value: str) -> None:
        self._set_state(concurrency_value=value)

    def set_log_level_value(self, value: str) -> None:
        self._set_state(log_level_value=value)

    def set_pool_size_value(self, value: str) -> None:
        self._set_state(pool_size_value=value)

    def set_db_overflow_value(self, value: str) -> None:
        self._set_state(db_overflow_value=value)

    def set_db_timeout_value(self, value: str) -> None:
        self._set_state(db_timeout_value=value)

    def set_io_workers_value(self, value: str) -> None:
        self._set_state(io_workers_value=value)

    def set_cpu_workers_value(self, value: str) -> None:
        self._set_state(cpu_workers_value=value)

    def set_no_proxy_value(self, value: str) -> None:
        self._set_state(no_proxy_value=value)

    # --- Async save commands (R16: IO offload via ThreadPoolManager) ---

    async def save_language(self, new_locale: str) -> bool:
        """保存语言配置。

        Returns:
            True 保存成功; False 保存失败 (ConfigHandler 返回 False 或 IO 异常)。
        Raises:
            asyncio.CancelledError: 取消时显式 raise (R2)。
        """
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_locale, new_locale)
            if not success:
                logger.warning(
                    "[SystemSettingsVM] set_locale returned False for locale=%s",
                    new_locale,
                )
                return False
            return True
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[SystemSettingsVM] Language | Change failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_theme(self, new_theme: str) -> bool:
        """保存主题配置。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_theme_name, new_theme)
            if not success:
                logger.warning(
                    "[SystemSettingsVM] set_theme_name returned False for theme=%s",
                    new_theme,
                )
                return False
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[SystemSettingsVM] Theme | Change failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_log_level(self, new_level: str) -> bool:
        """保存日志级别配置。"""
        if self._state.is_saving:
            return False
        self._set_state(is_saving=True)
        try:
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_log_level, new_level)
            from utils.logger import update_log_level

            update_log_level(new_level)
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[SystemSettingsVM] LogLevel | Change failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_concurrency(self, raw_val: str) -> bool:
        """保存同步重任务并发数 (范围 1-32)。"""
        if self._state.is_saving:
            return False
        try:
            val = int(raw_val)
        except (ValueError, TypeError):
            return False
        if not (_CONCURRENCY_MIN <= val <= _CONCURRENCY_MAX):
            return False
        self._set_state(is_saving=True)
        try:
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_sync_max_concurrent_heavy, val)
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[SystemSettingsVM] Concurrency | Save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_db_pool(self, pool_size_str: str, max_overflow_str: str, timeout_str: str) -> bool:
        """保存 DB 连接池配置 (pool_size 1-50, overflow 0-50, timeout 5-300)。"""
        if self._state.is_saving:
            return False
        try:
            pool_size = int(pool_size_str)
            max_overflow = int(max_overflow_str)
            timeout = int(timeout_str)
        except (ValueError, TypeError):
            return False
        if not (_DB_POOL_MIN <= pool_size <= _DB_POOL_MAX):
            return False
        if not (_DB_OVERFLOW_MIN <= max_overflow <= _DB_OVERFLOW_MAX):
            return False
        if not (_DB_TIMEOUT_MIN <= timeout <= _DB_TIMEOUT_MAX):
            return False
        self._set_state(is_saving=True)

        def _save_db_pool_sync() -> None:
            ConfigHandler.set_db_connection_pool_size(pool_size)
            ConfigHandler.set_db_max_overflow(max_overflow)
            ConfigHandler.set_db_pool_timeout(timeout)

        try:
            await ThreadPoolManager().run_async(TaskType.IO, _save_db_pool_sync)
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[SystemSettingsVM] DBPool | Save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_thread_pool(self, io_str: str, cpu_str: str) -> bool:
        """保存线程池配置 (io 4-512, cpu 1-64), 保存后 reload ThreadPoolManager。"""
        if self._state.is_saving:
            return False
        if not io_str or not cpu_str:
            return False
        try:
            io_val = int(io_str)
            cpu_val = int(cpu_str)
        except (ValueError, TypeError):
            return False
        if not (_IO_WORKERS_MIN <= io_val <= _IO_WORKERS_MAX):
            return False
        if not (_CPU_WORKERS_MIN <= cpu_val <= _CPU_WORKERS_MAX):
            return False
        self._set_state(is_saving=True)

        def _save_thread_pool_sync() -> None:
            ConfigHandler.set_max_io_workers(io_val)
            ConfigHandler.set_max_cpu_workers(cpu_val)

        try:
            await ThreadPoolManager().run_async(TaskType.IO, _save_thread_pool_sync)
            await asyncio.to_thread(ThreadPoolManager().reload_config)
            logger.info("Updated ThreadPool: IO=%s, CPU=%s", io_val, cpu_val)
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[SystemSettingsVM] ThreadPool | Save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)

    async def save_no_proxy(self, raw_text: str) -> bool:
        """保存 no-proxy 域名列表 (逗号分隔), 保存后 reapply proxy policy。"""
        if self._state.is_saving:
            return False
        if not raw_text:
            domains: list[str] = []
        else:
            domains = [d.strip() for d in raw_text.split(",") if d.strip()]
        self._set_state(is_saving=True)
        try:
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_no_proxy_domains, domains)
            from utils.proxy_manager import ProxyManager

            ThreadPoolManager().submit(TaskType.IO, ProxyManager.reapply_proxy_policy)
            logger.info("No-Proxy domains updated: %s", domains)
            return True
        except asyncio.CancelledError:
            raise  # R2
        except Exception as ex:
            logger.error("[SystemSettingsVM] No-proxy domains save failed: %s", ex, exc_info=True)
            return False
        finally:
            self._set_state(is_saving=False)
