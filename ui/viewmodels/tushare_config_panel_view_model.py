"""TushareConfigPanelViewModel — TushareConfigPanel 的 ViewModel（CLAUDE.md §3.2 MVVM）。

声明式渲染范式：
- 不可变 state snapshot（TushareConfigState frozen dataclass）
- subscribe/_notify 通知机制（View 通过 use_state + use_effect 订阅）
- commands 作为实例方法（消费方可直接调用 verify_token/update_tier）

VM 不感知 locale：status_message 用 Message dataclass 产出 (key, params)，
View 渲染时 I18n.get(msg.key, **msg.params)。动态错误消息用 _RAW_MSG_KEY
+ default=params 传递，I18n.get 对不存在的 key 返回 default。

线程模型：
- verify_token/update_tier 是 async，在 Flet 事件循环中执行
- ts.pro_api/trade_cal/ConfigHandler.save_token 等同步 IO
  通过 ThreadPoolManager.run_async(TaskType.IO, ...) offload（R16 合规）
- token 不经 tushare SDK 全局状态（~/tk.csv）传递，全程显式传参 ts.pro_api(token=...)
"""

import asyncio
import logging
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, replace

from data.constants import TUSHARE_POINT_TIERS
from ui.viewmodels import Message
from ui.viewmodels.config_panel_status_mixin import ConfigPanelStatusMixin
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, get_error_message_key
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)

_TUSHARE_REGISTER_URL = "https://tushare.pro/register?reg=728426"


@dataclass(frozen=True)
class TushareConfigState:
    """TushareConfigPanel 的不可变 state snapshot。

    tier_options: 5 档积分档位常量元组 (由 VM 产出, View 经 use_viewmodel 消费,
        避免View 直接 import data.constants.TUSHARE_POINT_TIERS 违反 MVVM 契约).
    """

    # Config fields
    token: str = ""
    tier: str = "points_5000"  # 默认档位（ConfigHandler.get_tushare_point_tier 的默认值）
    tier_options: tuple[str, ...] = TUSHARE_POINT_TIERS  # VM 产出供 View 渲染下拉选项
    # Status fields
    is_verifying: bool = False
    status_message: Message | None = None
    status_type: str = "info"  # "success" / "error" / "warning" / "info"


class TushareConfigPanelViewModel(ConfigPanelStatusMixin, ObservableViewModelMixin[TushareConfigState]):
    """ViewModel for TushareConfigPanel.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (TushareConfigState) via subscribe/_notify
    - Commands as instance methods (stable references)
    - VM 不感知 locale，status_message 用 Message 产出 (key, params)

    消费方直接实例化 VM 以调用 commands（verify_token/update_tier），
    View 通过 use_state + use_effect 订阅 VM state 变化触发重渲染。
    """

    def __init__(
        self,
        on_verify_success: Callable[[str], None] | None = None,
        on_save: Callable[[dict], None] | None = None,
        on_change: Callable[[], None] | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        show_internal_loading: bool = True,
    ):
        self._on_verify_success = on_verify_success
        self._on_save = on_save
        self._on_change = on_change
        self._on_loading_change = on_loading_change
        self._show_internal_loading = show_internal_loading
        self._state = TushareConfigState()
        self._subscribers: list[Callable[[TushareConfigState], None]] = []
        # 同步初始化 state（从 ConfigHandler 加载配置）
        self._load_config_to_state()

    # --- Config loading ---

    def _load_config_to_state(self) -> None:
        """从 ConfigHandler 加载配置到 state（同步）。"""
        token = ConfigHandler.get_token() or ""
        tier = ConfigHandler.get_tushare_point_tier()
        self._state = replace(self._state, token=token, tier=tier)

    def reload_config(self) -> None:
        """重新从 ConfigHandler 加载配置到 state。"""
        self._load_config_to_state()
        self._notify()

    # --- Update commands ---

    def update_token(self, value: str) -> None:
        self._set_state(token=value)
        self._notify_on_change()

    def _notify_on_change(self) -> None:
        if self._on_change:
            self._on_change()

    # --- get_config / set_config ---

    def get_current_config(self) -> dict:
        """返回配置字典。"""
        return {"token": self._state.token.strip()}

    def set_config(self, config: dict) -> None:
        """批量更新配置字段。"""
        if "token" in config:
            self._set_state(token=config["token"])

    def save(self) -> None:
        """触发 on_save 回调（同步，由消费方处理实际异步保存）。"""
        if self._on_save:
            self._on_save(self.get_current_config())

    async def open_registration_url(self) -> None:
        """在系统默认浏览器打开 Tushare 注册页（R16: webbrowser 同步 IO offload）。

        Raises:
            asyncio.CancelledError: 取消时显式传播 (R2).
        """
        await ThreadPoolManager().run_async(TaskType.IO, webbrowser.open_new_tab, _TUSHARE_REGISTER_URL)

    # --- Status helpers ---

    def _show_success(self, message: Message) -> None:
        self._set_state(status_message=message, status_type="success")

    def _set_loading_state(self, loading: bool) -> None:
        """调 on_loading_change + 如果 show_internal_loading 则设置 is_verifying。"""
        if self._on_loading_change:
            self._on_loading_change(loading)
        if self._show_internal_loading:
            self._set_state(is_verifying=loading)

    # --- async commands ---

    @log_async_operation(
        operation_name="tushare_panel_update_tier",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def update_tier(self, value: str) -> bool:
        """档位变更：持久化 + 重建限速器 + 清除旧 probe 缓存。

        R16：set_tier/reload_limiters 是同步 IO，必须 offload 到 io_pool。
        """
        if value == self._state.tier:
            return True

        try:
            success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_tushare_point_tier, value)
            if not success:
                self._set_state(tier=ConfigHandler.get_tushare_point_tier())
                self._show_error(Message("sys_tier_save_failed"))
                return False

            from data.external.tushare_client import TushareClient

            client = TushareClient()
            await ThreadPoolManager().run_async(TaskType.IO, client.reload_rate_limiters)
            client.clear_capability_cache()

            self._set_state(tier=value)
            self._show_success(Message("sys_tier_saved_success"))
            return True
        except Exception as exc:
            logger.warning(
                "[TushareConfigVM] Failed to save tier: %s",
                DataSanitizer.sanitize_error(exc),
                exc_info=True,
            )
            self._set_state(tier=ConfigHandler.get_tushare_point_tier())
            self._show_error(Message("sys_tier_save_failed"))
            return False

    @log_async_operation(
        operation_name="tushare_panel_verify_token",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def verify_token(self) -> bool:
        """验证 Tushare Token：pro_api 探活 + save_token + probe capabilities。

        R16：ts.pro_api/trade_cal/ConfigHandler.save_token/
        TushareClient 构造等同步 IO 必须通过 ThreadPoolManager offload。

        token 全程显式传参（ts.pro_api(token=...)），不写 tushare SDK 全局 ~/tk.csv。
        """
        token = self._state.token.strip()

        if not token:
            self._show_error(Message("tushare_token_required"))
            return False

        if self._state.is_verifying:
            self._show_warning(Message("tushare_verifying_in_progress"))
            return False

        self._set_state(is_verifying=True)
        self._set_loading_state(True)

        try:
            import tushare as ts

            from data.external.tushare_client import _ASYNC_TIMEOUT_MULTIPLIER, TushareClient

            # 显式传 token，避免依赖 tushare SDK 全局状态（~/tk.csv 或环境变量）
            timeout_val = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.get_tushare_timeout)
            # get_tushare_timeout 无最小值钳制，配置异常（<=0）时兜底为 1s，
            # 避免 wait_for(timeout<=0) 未真正网络调用前即失败。
            timeout_val = max(timeout_val, 1)
            temp_pro = ts.pro_api(token=token, timeout=timeout_val)
            # asyncio.wait_for 兜底：requests.post 的 timeout 不覆盖 DNS 解析挂起等场景，
            # 与 _handle_api_call 的 timeout=self.timeout*_ASYNC_TIMEOUT_MULTIPLIER 同模式。
            await asyncio.wait_for(
                ThreadPoolManager().run_async(
                    TaskType.IO,
                    temp_pro.trade_cal,
                    exchange="SSE",
                    start_date="20250101",
                    end_date="20250101",
                ),
                timeout=timeout_val * _ASYNC_TIMEOUT_MULTIPLIER,
            )

            from strategies.all_strategies import StrategyManager

            # keyring.set_password + save_config 均为同步 IO，15s 兜底防 keyring 后端卡死
            await asyncio.wait_for(
                ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.save_token, token),
                timeout=15,
            )

            # TushareClient 必须在 async 上下文（事件循环线程）实例化，经 _capture_loop 捕获
            # 事件循环引用；set_token 使用异步 API set_token_async。同步包装器 set_token 依赖
            # _loop 已被捕获，若在工作线程构造（旧 _init_client_sync 反模式）则 _loop=None，
            # set_token 会抛 RuntimeError("set_token() requires a running event loop")。
            # set_token_async 内部仅重建 pro_api 引用与限流器（无网络 IO，不违反 R16），
            # 与 bootstrap 的 _warmup_tushare_capabilities 同模式。
            client = TushareClient()
            needs_probe = await client.set_token_async(token)

            if needs_probe:
                try:
                    logger.info("[TushareConfigVM] Probing API capabilities...")
                    probe_results = await client.probe_api_capabilities()

                    StrategyManager().invalidate_dependency_cache()

                    available_apis = [api for api, status in probe_results.items() if status is True]
                    unavailable_apis = [api for api, status in probe_results.items() if status is False]

                    if unavailable_apis:
                        self._show_warning(Message("tushare_verify_restricted", {"apis": ", ".join(unavailable_apis)}))
                        logger.warning("[TushareConfigVM] Restricted APIs: %s", unavailable_apis)
                    elif available_apis:
                        self._show_success(Message("tushare_verify_success"))
                        logger.info("[TushareConfigVM] All probed APIs available: %s", len(available_apis))
                    else:
                        self._show_warning(Message("tushare_verify_unknown_status"))
                except Exception as probe_exc:
                    logger.warning(
                        "[TushareConfigVM] Capability probe failed (non-critical): %s",
                        DataSanitizer.sanitize_error(probe_exc),
                        exc_info=True,
                    )
                    self._show_success(Message("tushare_verify_unknown_status"))
            else:
                self._show_success(Message("tushare_verify_success"))

            if self._on_verify_success:
                self._on_verify_success(token)

            return True
        except Exception as e:
            logger.error(
                "[TushareConfigVM] Token verification failed: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
            error_info = classify_error(e, context="token")
            self._show_error(get_error_message_key(error_info))
            return False
        finally:
            self._set_state(is_verifying=False)
            self._set_loading_state(False)
