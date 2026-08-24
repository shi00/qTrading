"""home_view — 声明式组件 (Phase C.1).

从命令式容器子类重写为 ``@ft.component`` + ``use_viewmodel`` 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式容器子类 → ``@ft.component def HomeView()``
- 生命周期回调 / 手动刷新 / 可见性命令式 API / 手动刷新调用 / locale 命令式刷新 / 响应式回调 全部移除
- VM 通过 ``use_viewmodel(HomeViewModel)`` 消费 (内部 VM 模式, hook 实例化 + dispose)
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- L771 合规: 业务数据直接从 state 读取 (tuple[Row, ...]),
  移除 dual-track 的 use_state 快照 + use_effect 拉取模式
- PubSub 订阅/退订用 ``use_effect(setup, [], cleanup=cleanup)`` (Phase 3.0.3 模式)
- 消费声明式 ``MarketDashboard(indices=..., hsgt=..., hot_concepts=...)``
  / ``NewsFeed(news_rows=..., has_more=..., on_load_more_click=...)``
- page 访问用 ``ft.context.page`` (try/except 守卫 RuntimeError)
"""

import asyncio
import logging
import os
import typing
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_controls, safe_on_click
from ui.components.market_dashboard import MarketDashboard
from ui.components.news_feed import NewsFeed
from ui.components.state_views import ErrorState
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import CACHE_CLEARED_TOPIC, TOPIC_NAVIGATE
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.home_view_model import HomeViewModel
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@ft.component
def HomeView(
    on_run_strategy: Callable[[], None] | None = None,
    active: bool = True,
) -> ft.Container:
    """Home dashboard view (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - state + commands via ``use_viewmodel(HomeViewModel)`` (内部 VM 模式)
    - i18n/theme via ``ft.use_state(*.get_observable_state)`` for auto-rerender
    - PubSub via ``use_effect(setup, [], cleanup=cleanup)`` (Phase 3.0.3)
    - L771 合规: 业务数据直接从 state 读取, 无 dual-track use_state 快照

    Args:
        on_run_strategy: 保留参数兼容 app_layout 命令式调用 (Phase F.4 重写后移除);
            当前 HomeView 不使用此回调 (与原命令式实现一致 —— 参数被存储但从未调用)
    """
    # 兼容 app_layout 命令式调用, 当前不使用 (原命令式实现亦未调用)
    _ = on_run_strategy

    logger.info("[HomeView] construction start, active=%s", active)

    # --- VM (内部模式: hook 实例化 + 卸载时 dispose) ---
    logger.info("[HomeView] calling use_viewmodel(HomeViewModel)")
    state, vm = use_viewmodel(HomeViewModel)
    logger.info("[HomeView] use_viewmodel returned, state.loading=%s", getattr(state, "loading", "unknown"))

    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Handlers ---

    def _on_broadcast_message(topic: str, message: str) -> None:
        """PubSub topic 事件处理 (cache_cleared → 清空状态)."""
        if topic == CACHE_CLEARED_TOPIC and message == "cache_cleared":
            vm.clear_state()

    def _refresh_clicked(e: ft.ControlEvent) -> None:
        ensure_correlation_id()
        UILogger.log_action("HomeView", "Click", "btn_refresh")
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(_load_data)
        except RuntimeError:
            logger.debug("[HomeView] page not available for refresh")

    def _on_retry_load() -> None:
        """ErrorState on_retry: 触发 _refresh_clicked 逻辑 (P1-3 批次 2).

        _refresh_clicked 内部 e 参数未使用, 可安全传 None。
        """
        _refresh_clicked(typing.cast(ft.ControlEvent, None))

    def _navigate_to_data_source() -> None:
        """ErrorState CTA: PubSub 广播深链导航到 data_source 设置子页 (UX-01).

        home_view 无 page.go() 路由, 通过 PubSub TOPIC_NAVIGATE 广播
        "<tab>:<subtab>" 深链消息 (UX-01 协议), app_layout 订阅后切换
        NavigationRail selected_index 并激活 settings 的 data 子页,
        一次点击直达目标子页 (非设置壳)。
        """
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, "settings:data")
        except RuntimeError:
            logger.debug("[HomeView] page not available for navigation")

    def _on_view_stock(stock_code: str) -> None:
        """新闻「查看个股」跳转 (Task 8.1): PubSub 广播导航到选股页.

        最小实现: 跳转到选股 tab 供用户进一步研究该股票代码。
        """
        _ = stock_code  # 预留: 后续可扩展为跨视图股票代码预填
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, "screener")
        except RuntimeError:
            logger.debug("[HomeView] page not available for view_stock navigation")

    async def _on_load_more_click(e: ft.ControlEvent) -> None:
        try:
            await vm.load_next_page()  # state 自动更新, 无需 View 持有快照
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error("[HomeView] Load more failed: %s", DataSanitizer.sanitize_error(exc))

    # --- Data loading ---

    async def _load_data() -> None:
        # P1-3 批次 2: 包装 set_loading/set_load_error 实现加载态/错误态
        vm.set_loading(True)
        vm.set_load_error(None)
        try:
            await vm.load_market_data()
            await vm.refresh_news()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error("[HomeView] Load failed: %s", DataSanitizer.sanitize_error(exc))
            vm.set_load_error(Message("home_load_failed_title", {}))
        finally:
            vm.set_loading(False)

    async def _init_and_load() -> None:
        if not active:
            return
        # E2E 模式下跳过外部依赖初始化（bootstrap.py 已跳过 SchedulerService/
        # NewsSubscriptionService/MarketDataService.start）。vm.init() 会实例化
        # NewsSubscriptionService → AIService → litellm import（同步阻塞 MainThread
        # 18s+，触发 Matplotlib 字体缓存构建），vm.init_data() 调用 Tushare API
        # （E2E 环境 token 无效必失败），_load_data() 等待 MarketDataService 缓存
        # （E2E 未启动，5×0.5s=2.5s 空等）。这些操作阻塞 Flet patch 下发，
        # 导致 E2E 浏览器在 600s 内未检测到 NavigationRail "智能选股"文本。
        # E2E 测试不依赖 HomeView 异步数据（test_home_view_loads 仅断言静态标签），
        # 跳过是安全的。bootstrap.py:131 / main.py:209 / quality_gate.py 等已有
        # E2E_TESTING 检查先例，此处与之一致。
        if os.environ.get("E2E_TESTING") == "true":
            vm.set_loading(False)
            return
        try:
            vm.init()  # 添加 service listener (active 失活时经下方 effect cleanup 调 vm.stop() 退订)
            await vm.init_data()
            await _load_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error(
                "[HomeView] Init failed: %s",
                DataSanitizer.sanitize_error(exc),
                exc_info=True,
            )

    # --- PubSub 订阅/退订 (Phase 3.0.3 模式) ---

    async def _setup_pubsub() -> None:
        if not active:
            return
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.subscribe_topic(CACHE_CLEARED_TOPIC, _on_broadcast_message)
        except RuntimeError:
            pass

    async def _cleanup_pubsub() -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.unsubscribe_topic(CACHE_CLEARED_TOPIC)
        except RuntimeError:
            pass

    ft.use_effect(_setup_pubsub, dependencies=[active], cleanup=_cleanup_pubsub)

    # --- 初始加载 (mount 时执行一次; active 失活经 cleanup 调 vm.stop() 退订) ---

    def _cleanup_service_listeners() -> None:
        """active 失活时退订 service listener (与 _init_and_load 的 vm.init() 配对).

        HomeView 常驻 app_layout 的 ft.Stack (永不卸载), listener 仅随 dispose()
        移除会导致失活后仍被新闻/行情 push 反复重渲染。本 cleanup 在 deps
        (active) 变化 (True→False 失活 / False→True 重激活) 与卸载时执行,
        调 vm.stop() 退订 (幂等; 重激活时 cleanup 先于 setup 执行, stop 为
        空操作, 随后 setup 中 init() 重新注册 listener)。
        E2E 早返: 对称 _init_and_load 顶部守卫。E2E 下未早返时将实例化
        NewsSubscriptionService → AIService → litellm import (同步阻塞 MainThread),
        造成 E2E 回归。
        """
        if os.environ.get("E2E_TESTING") == "true":
            return
        vm.stop()

    ft.use_effect(_init_and_load, dependencies=[active], cleanup=_cleanup_service_listeners)

    # --- 渲染 (直接从 state 读取, 无 dual-track) ---

    # Task 8.1: 数据缓存透明化 — stale 时显示「数据缓存于」明确告知用户当前为缓存快照
    if state.market_stale:
        date_text_value = I18n.get("market_data_cached_at").format(date=state.market_date)
    else:
        date_text_value = I18n.get("home_data_date").format(date=state.market_date)

    header = ft.Row(
        safe_controls(
            [
                ft.Text(I18n.get("home_title"), size=AppStyles.FONT_SIZE_XL, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                ft.Text(date_text_value, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY),
                ft.IconButton(
                    ft.Icons.REFRESH,
                    on_click=safe_on_click(_refresh_clicked),
                    tooltip=I18n.get("home_refresh"),
                ),
            ]
        ),
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # P1-3 批次 2: 三态渲染 (load_error → ErrorState; is_loading → ProgressRing; 否则正常)
    content_controls: list[ft.Control] = [header, ft.Divider()]
    if state.load_error is not None:
        content_controls.append(
            ErrorState(
                icon=ft.Icons.ERROR_OUTLINE,
                title=I18n.get("home_load_failed_title"),
                message=I18n.get("home_load_failed_message"),
                on_retry=_on_retry_load,
                retry_text=I18n.get("common_retry"),
                on_cta=_navigate_to_data_source,
                cta_text=I18n.get("home_goto_data_source"),
                cta_icon=ft.Icons.SETTINGS,  # UX-03 (P2-09): 前往设置页数据源, 显式传入保持语义
            )
        )
    elif state.is_loading:
        content_controls.append(
            ft.Container(
                content=ft.ProgressRing(width=48, height=48, stroke_width=4),
                alignment=ft.Alignment.CENTER,
                expand=True,
                padding=AppStyles.EMPTY_STATE_PADDING,
            )
        )
    else:
        content_controls.extend(
            [
                MarketDashboard(
                    indices=state.market_indices,
                    hsgt=state.market_hsgt,
                    hot_concepts=state.market_hot_concepts,
                ),
                ft.Text(I18n.get("home_live_news"), size=AppStyles.FONT_SIZE_HEADLINE, weight=ft.FontWeight.BOLD),
                NewsFeed(
                    news_rows=state.news_rows,
                    has_more=state.has_more_news,
                    on_load_more_click=typing.cast("Callable[[ft.ControlEvent], None]", _on_load_more_click),
                    on_view_stock=_on_view_stock,
                ),
            ]
        )

    logger.info("[HomeView] construction complete, returning Container")
    return ft.Container(
        content=ft.Column(
            safe_controls(content_controls),
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
