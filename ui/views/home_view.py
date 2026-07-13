"""home_view — 声明式组件 (Phase C.1).

从命令式容器子类重写为 ``@ft.component`` + ``use_viewmodel`` 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式容器子类 → ``@ft.component def HomeView()``
- 生命周期回调 / 手动刷新 / 可见性命令式 API / 手动刷新调用 / locale 命令式刷新 / 响应式回调 全部移除
- VM 通过 ``use_viewmodel(HomeViewModel)`` 消费 (内部 VM 模式, hook 实例化 + dispose)
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 大体积数据 (market_data / news_items) 用 ``use_state`` 快照管理, service 事件通过
  VM state version 变化触发 ``use_effect`` 重新拉取 (dual-track 落地)
- PubSub 订阅/退订用 ``use_effect(setup, [], cleanup=cleanup)`` (Phase 3.0.3 模式)
- 消费声明式 ``MarketDashboard(data=...)`` / ``NewsFeed(news_items=..., has_more=..., on_load_more_click=...)``
- page 访问用 ``ft.context.page`` (try/except 守卫 RuntimeError)
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft
import pandas as pd

from data.cache.cache_manager import CacheManager
from services.news_subscription_service import NewsUpdateType
from ui.components.market_dashboard import MarketDashboard
from ui.components.news_feed import NewsFeed
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import CACHE_CLEARED_TOPIC
from ui.theme import AppColors
from ui.viewmodels.home_view_model import HomeViewModel
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


@ft.component
def HomeView(on_run_strategy: Callable[[], None] | None = None) -> ft.Container:
    """Home dashboard view (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - state + commands via ``use_viewmodel(HomeViewModel)`` (内部 VM 模式)
    - i18n/theme via ``ft.use_state(*.get_observable_state)`` for auto-rerender
    - PubSub via ``use_effect(setup, [], cleanup=cleanup)`` (Phase 3.0.3)
    - 大体积数据用 ``use_state`` 快照, VM state version 变化触发 ``use_effect`` 拉取

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

    # --- 大体积数据快照 (dual-track: VM 持有源数据, View 用 use_state 快照驱动渲染) ---
    market_data, set_market_data = ft.use_state(lambda: {})
    news_items, set_news_items = ft.use_state(lambda: pd.DataFrame())
    has_more, set_has_more = ft.use_state(False)

    # --- Handlers ---

    def _on_broadcast_message(topic: str, message: str) -> None:
        """PubSub topic 事件处理 (cache_cleared → 清空状态)."""
        if topic == CACHE_CLEARED_TOPIC and message == "cache_cleared":
            vm.clear_state()
            set_market_data({})
            set_news_items(pd.DataFrame())
            set_has_more(False)

    def _refresh_clicked(e: ft.ControlEvent) -> None:
        ensure_correlation_id()
        UILogger.log_action("HomeView", "Click", "btn_refresh")
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(_load_data)
        except RuntimeError:
            logger.debug("[HomeView] page not available for refresh")

    async def _on_load_more_click(e: ft.ControlEvent) -> None:
        try:
            new_batch, more = await vm.load_next_page()
            if new_batch is not None and not new_batch.empty:
                set_news_items(pd.concat([news_items, new_batch], ignore_index=True))
                set_has_more(more)
            else:
                set_has_more(False)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error("[HomeView] Load more failed: %s", DataSanitizer.sanitize_error(exc))

    # --- Data loading ---

    async def _load_data() -> None:
        try:
            mdata = await vm.load_market_data()
            set_market_data(mdata or {})
            ndata, more = await vm.refresh_news()
            set_news_items(ndata if ndata is not None else pd.DataFrame())
            set_has_more(more)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error("[HomeView] Load failed: %s", DataSanitizer.sanitize_error(exc))

    async def _init_and_load() -> None:
        try:
            vm.init()  # 添加 service listener (use_viewmodel cleanup 时 vm.dispose() 移除)
            await vm.init_data()
            await _load_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.error("[HomeView] Init failed: %s", exc, exc_info=True)

    # --- PubSub 订阅/退订 (Phase 3.0.3 模式) ---

    async def _setup_pubsub() -> None:
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

    ft.use_effect(_setup_pubsub, dependencies=[], cleanup=_cleanup_pubsub)

    # --- 初始加载 (mount 时执行一次) ---
    ft.use_effect(_init_and_load, dependencies=[])

    # --- market_update 响应 (VM state.market_update_version 变化触发) ---

    async def _on_market_update() -> None:
        try:
            data = await vm.get_cached_market_data()
            set_market_data(data or {})
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.debug("[HomeView] Market update failed: %s", exc, exc_info=True)

    ft.use_effect(_on_market_update, dependencies=[state.market_update_version])

    # --- news_update 响应 (VM state.news_update_version 变化触发) ---

    async def _on_news_update() -> None:
        update = vm.last_news_update
        if update is None:
            return
        try:
            update_type, data = update
            if update_type == NewsUpdateType.NEW_ITEM and data:
                rows = [CacheManager.normalize_news_item(item, default_source="CLS") for item in data]
                if rows:
                    new_df = pd.DataFrame(rows)
                    set_news_items(pd.concat([new_df, news_items], ignore_index=True))
            elif update_type == NewsUpdateType.TAG_UPDATE and data:
                content = data.get("content", "")
                tags = data.get("tags", "")
                if content and not news_items.empty:
                    mask = news_items["content"] == content
                    if mask.any():
                        updated = news_items.copy()
                        updated.loc[mask, "tags"] = tags
                        set_news_items(updated)
            else:
                ndata, more = await vm.refresh_news()
                set_news_items(ndata if ndata is not None else pd.DataFrame())
                set_has_more(more)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            logger.debug("[HomeView] News update failed: %s", exc, exc_info=True)

    ft.use_effect(_on_news_update, dependencies=[state.news_update_version])

    # --- 渲染 ---

    data = market_data or {}
    date_str = data.get("date", "--")
    stale = data.get("stale", False)
    suffix = f" ({I18n.get('home_data_updating')})" if stale else ""
    date_text_value = I18n.get("home_data_date").format(date=date_str) + suffix

    header = ft.Row(
        [
            ft.Text(I18n.get("home_title"), size=24, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.Text(date_text_value, size=12, color=AppColors.TEXT_SECONDARY),
            ft.IconButton(
                ft.Icons.REFRESH,
                on_click=_refresh_clicked,
                tooltip=I18n.get("home_refresh"),
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    return ft.Container(
        content=ft.Column(
            [
                header,
                ft.Divider(),
                MarketDashboard(data=market_data),
                ft.Text(I18n.get("home_live_news"), size=20, weight=ft.FontWeight.BOLD),
                NewsFeed(
                    news_items=news_items,
                    has_more=has_more,
                    on_load_more_click=_on_load_more_click,
                ),
            ],
            scroll=None,
            expand=True,
        ),
        expand=True,
    )
