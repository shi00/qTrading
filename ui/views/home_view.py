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
import datetime
import hashlib
import logging
import typing
from collections.abc import Callable

import flet as ft

from ui.components.flet_type_helpers import safe_controls, safe_on_click
from ui.components.market_dashboard import MarketDashboard
from ui.components.news_feed import NewsFeed
from ui.components.state_views import ErrorState, LoadingState
from ui.cache_cleared_state import get_cache_cleared_state
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.business_research_view_model import (
    OBJECT_KIND_NEWS,
    BusinessResearchRequest,
)
from ui.viewmodels.home_view_model import HomeViewModel, NewsRow
from utils.app_env import is_e2e_mode
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@ft.component
def HomeView(
    on_run_strategy: Callable[[], None] | None = None,
    active: bool = True,
    on_start_research: Callable[..., None] | None = None,
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

    # --- VM (内部模式: hook 实例化 + 卸载时 dispose) ---
    state, vm = use_viewmodel(HomeViewModel)

    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Handlers ---

    def _refresh_clicked(e: ft.ControlEvent) -> None:
        ensure_correlation_id()
        UILogger.log_action("HomeView", "Click", "btn_refresh")
        try:
            page = ft.context.page
            if page is not None:
                # D17-001: 刷新也走统一入口, 初始化阶段失败后点刷新可恢复
                # (vm.init_data() 成功后幂等, 不重复 DB/stock_basic 初始化)。
                page.run_task(_init_and_load)
        except RuntimeError:
            logger.debug("[HomeView] page not available for refresh")

    def _on_retry_load() -> None:
        """ErrorState on_retry: 触发 _refresh_clicked 逻辑 (P1-3 批次 2).

        D17-001: 重试必须覆盖失败的初始化阶段 (init/init_data), 不能只重跑
        数据加载——否则 processor 未就绪时错误态永久不可恢复。

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
        """新闻「查看个股」跳转 (Task 8.1 + UX-04): 深链携带股票代码.

        消息 "screener:<code>" (UX-01 深链协议, screener 段语义=股票代码):
        app_layout 切 screener tab 并将 code 透传 ScreenerView 填充代码过滤;
        code 为空时降级纯 tab 导航 (空段消息会被协议解析判非法, 不发送).
        """
        try:
            page = ft.context.page
            if page is not None:
                message = f"screener:{stock_code}" if stock_code else "screener"
                page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, message)
        except RuntimeError:
            logger.debug("[HomeView] page not available for view_stock navigation")

    def _on_start_news_research(row: NewsRow) -> None:
        """所选资讯 → 研究草稿准备（只准备不发送；不导航、不重跑任何业务）。"""
        if on_start_research is None or row.news_id is None:
            return
        available_at: str | None = None
        if row.publish_time:
            try:
                published = datetime.datetime.fromisoformat(row.publish_time)
            except (TypeError, ValueError):
                published = None
            if published is not None:
                if published.tzinfo is None:
                    # market_news.publish_time 为 UTC tz-naive（入库前 to_utc_for_db）
                    published = published.replace(tzinfo=datetime.UTC)
                available_at = published.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        excerpt = row.content if len(row.content) <= 500 else f"{row.content[:500]}…"
        content_digest = hashlib.sha256(row.content.encode("utf-8")).hexdigest()
        on_start_research(
            BusinessResearchRequest(
                source_module="news",
                object_kind=OBJECT_KIND_NEWS,
                object_id=str(row.news_id),
                display_name=row.content[:40] or f"news {row.news_id}",
                summary=(
                    ("news_id", str(row.news_id)),
                    ("publish_time", row.publish_time),
                    ("source", row.source),
                    ("content_excerpt", excerpt),
                    ("content_sha256", content_digest),
                    ("content_truncated", "true" if len(row.content) > 500 else "false"),
                ),
                available_at=available_at,
            )
        )

    async def _on_load_more_click() -> None:
        """「加载更多」业务命令 (M12-028: 无参, 不接收 Flet ControlEvent).

        Flet 按 handler 参数个数分发: 0 参 handler 不被注入事件 (锁定版本行为)。
        """
        try:
            await vm.load_next_page()  # state 自动更新, 无需 View 持有快照
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error("[HomeView] Load more failed: %s", DataSanitizer.sanitize_error(exc))

    # --- Data loading ---

    async def _load_data(news_generation: int) -> Message | None:
        """执行行情 + 新闻加载, 返回本代次应写回的可见错误 (None = 成功).

        D17-001: loading 所有权 (begin_load/end_load) 统一由 ``_init_and_load``
        持有, 本函数不再自行分配代次, 避免初始化与加载各持一次所有权。
        """
        load_error: Message | None = None
        try:
            await vm.load_market_data()
            await vm.refresh_news(news_generation)
            # VMD-F02: refresh_news 取数失败时已在 state 发布可见错误（保留旧行）。
            # 必须带入 end_load，否则 finally 以 None 收尾会把错误清掉（原缺陷）。
            load_error = vm.state.load_error
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error("[HomeView] Load failed: %s", DataSanitizer.sanitize_error(exc))
            load_error = Message("home_load_failed_title", {})
        return load_error

    async def _init_and_load() -> None:
        if not active:
            return
        # E2E 模式下跳过外部依赖初始化（bootstrap.py 已跳过 SchedulerService/
        # NewsSubscriptionService/MarketDataService.start）。vm.init() 会实例化
        # NewsSubscriptionService → AIService → MarketDataService 等（同步阻塞
        # MainThread；litellm 已惰性化不再阻塞），
        # vm.init_data() 调用 Tushare API（E2E 环境 token 无效必失败），_load_data()
        # 等待 MarketDataService 缓存（E2E 未启动，5×0.5s=2.5s 空等）。这些操作
        # 阻塞 Flet patch 下发，导致 E2E 浏览器在 600s 内未检测到 NavigationRail
        # "智能选股"文本。
        # E2E 测试不依赖 HomeView 异步数据（test_home_view_loads 仅断言静态标签），
        # 跳过是安全的。bootstrap.py:131 / main.py:209 / quality_gate.py 等已有
        # E2E_TESTING 检查先例，此处与之一致。
        if is_e2e_mode():
            vm.set_loading(False)
            return
        # D17-001: 初始化与数据加载共用一次加载所有权。初始化异常经 VM 公开命令
        # end_load 提交错误态并退出 busy (View 不触碰 VM 私有状态), 首帧不再把
        # 初始化失败渲染成"空内容"; 取消仍经 except CancelledError 传播 (R2)。
        view_generation, news_generation = vm.begin_load()
        load_error: Message | None = None
        try:
            vm.init()  # 添加 service listener (active 失活时经下方 effect cleanup 调 vm.stop() 退订)
            await vm.init_data()
            load_error = await _load_data(news_generation)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error(
                "[HomeView] Init failed: %s",
                DataSanitizer.sanitize_error(exc),
                exc_info=True,
            )
            load_error = Message("home_load_failed_title", {})
        finally:
            # 代次失效 (新加载/clear_state/dispose) 时 end_load 为 no-op,
            # 旧初始化结果不会清掉新一代的错误或 loading。
            vm.end_load(view_generation, load_error)

    # --- CacheCleared 订阅 (UIX-01: pubsub → Observable 转发, 保留 active 门控) ---

    # 信号当前快照 (经 use_state(get_cache_cleared_state) 订阅 Observable 自动重渲染)
    cache_state, _ = ft.use_state(get_cache_cleared_state)
    # 已消费的序号上界 (仅对 seq 变化生效; mount/active 翻转 seq 未变则早返)
    last_seq = ft.use_ref(cache_state.seq)

    def _on_cache_seq_change() -> None:
        if cache_state.seq == last_seq.current:
            return
        last_seq.current = cache_state.seq
        if active:
            vm.clear_state()

    ft.use_effect(_on_cache_seq_change, dependencies=[cache_state.seq, active])

    # --- 初始加载 (mount 时执行一次; active 失活经 cleanup 调 vm.stop() 退订) ---

    def _cleanup_service_listeners() -> None:
        """active 失活时退订 service listener (与 _init_and_load 的 vm.init() 配对).

        HomeView 常驻 app_layout 的 ft.Stack (永不卸载), listener 仅随 dispose()
        移除会导致失活后仍被新闻/行情 push 反复重渲染。本 cleanup 在 deps
        (active) 变化 (True→False 失活 / False→True 重激活) 与卸载时执行,
        调 vm.stop() 退订 (幂等; 重激活时 cleanup 先于 setup 执行, stop 为
        空操作, 随后 setup 中 init() 重新注册 listener)。
        E2E 早返: 对称 _init_and_load 顶部守卫。E2E 下未早返时将实例化
        NewsSubscriptionService → AIService → MarketDataService 等 (同步阻塞
        MainThread; litellm 已惰性化不再阻塞),
        造成 E2E 回归。
        """
        if is_e2e_mode():
            return
        vm.stop()

    ft.use_effect(_init_and_load, dependencies=[active], cleanup=_cleanup_service_listeners)

    # --- 渲染 (直接从 state 读取, 无 dual-track) ---

    # Task 8.1: 数据缓存透明化 — stale 时显示「数据缓存于」明确告知用户当前为缓存快照
    # UIR-F03-002: 页头 i18n key 由 VM 依逐分量 as_of 派生 (混合日期 →
    # market_data_mixed_dates)，View 只按当前 locale 渲染 key。
    date_text_value = I18n.get(state.market_header_key).format(date=state.market_date)

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
                    disabled=state.is_loading,  # 加载中置灰, 防止连点触发并发全量加载
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
        content_controls.append(LoadingState())
    else:
        content_controls.extend(
            [
                MarketDashboard(
                    indices=state.market_indices,
                    hsgt=state.market_hsgt,
                    hot_concepts=state.market_hot_concepts,
                    data_date=state.market_date,
                ),
                ft.Text(I18n.get("home_live_news"), size=AppStyles.FONT_SIZE_HEADLINE, weight=ft.FontWeight.BOLD),
                NewsFeed(
                    news_rows=state.news_rows,
                    has_more=state.has_more_news,
                    on_load_more_click=_on_load_more_click,
                    on_view_stock=_on_view_stock,
                    valid_stock_codes=state.valid_stock_codes,
                    valid_stock_ts_codes=state.valid_stock_ts_codes,
                    is_loading_more=state.is_loading_more,
                    on_start_research=_on_start_news_research if on_start_research is not None else None,
                ),
            ]
        )

    return ft.Container(
        content=ft.Column(
            safe_controls(content_controls),
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
