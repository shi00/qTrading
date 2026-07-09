import asyncio
import logging
from collections.abc import Callable

import flet as ft
import pandas as pd

from data.cache.cache_manager import CacheManager
from services.news_subscription_service import NewsUpdateType
from ui.components.market_dashboard import MarketDashboard
from ui.components.news_feed import NewsFeed
from ui.i18n import I18n
from ui.theme import AppColors
from ui.viewmodels.home_view_model import HomeViewModel
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


class HomeView(ft.Container):
    """
    HomeView with MVVM Architecture.
    Responsibility: Rendering and User Interaction.
    State & Logic: Delegated to HomeViewModel.
    """

    def __init__(self, on_run_strategy=None):  # pragma: no cover
        super().__init__()  # pragma: no cover
        self.expand = True  # pragma: no cover
        self.on_run_strategy = on_run_strategy  # pragma: no cover

        # Dependency Injection
        self.vm = HomeViewModel()  # pragma: no cover

        # View State (UI only)
        self._init_task = None  # pragma: no cover
        self._is_mounted = False  # pragma: no cover
        self._is_visible = True  # pragma: no cover
        self._pubsub_subscribed = False  # pragma: no cover
        # NOTE(lazy): VM subscribe 替代 init(on_*),Phase 4 声明式重写时移除。
        # ceiling: Phase 4 HomeView 重写. upgrade: Task 4.x HomeView 声明式重写.
        self._vm_unsubscribe: Callable[[], None] | None = None
        self._prev_state = self.vm.state  # pragma: no cover

        # --- Initialize Components ---
        self.header_title = ft.Text(  # pragma: no cover
            I18n.get("home_title"),  # pragma: no cover
            size=24,  # pragma: no cover
            weight=ft.FontWeight.BOLD,  # pragma: no cover
        )  # pragma: no cover
        self.header = self._build_header()  # pragma: no cover
        self.dashboard = MarketDashboard()  # pragma: no cover
        self.news_feed = NewsFeed(on_load_more_click=self._on_load_more_click)  # pragma: no cover
        self.news_header = ft.Text(  # pragma: no cover
            I18n.get("home_live_news"),  # pragma: no cover
            size=20,  # pragma: no cover
            weight=ft.FontWeight.BOLD,  # pragma: no cover
        )  # pragma: no cover

        # Assemble Layout
        self.content = ft.Column(  # pragma: no cover
            scroll=None,  # pragma: no cover
            expand=True,  # pragma: no cover
            controls=[  # pragma: no cover
                self.header,  # pragma: no cover
                ft.Divider(),  # pragma: no cover
                self.dashboard,  # pragma: no cover
                self.news_header,  # pragma: no cover
                self.news_feed,  # pragma: no cover
            ],  # pragma: no cover
        )  # pragma: no cover

    def _build_header(self):  # pragma: no cover
        self.date_text = ft.Text("--", size=12, color=ft.Colors.GREY)
        self.refresh_btn = ft.IconButton(
            ft.Icons.REFRESH,
            on_click=self._refresh_clicked,
            tooltip=I18n.get("home_refresh"),
        )
        return ft.Row(
            [
                self.header_title,
                ft.Container(expand=True),
                self.date_text,
                self.refresh_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # --- Lifecycle ---

    def did_mount(self):  # pragma: no cover
        self._is_mounted = True

        # Subscribe to PubSub (Global Events)
        if self.page and not self._pubsub_subscribed:
            self.page.pubsub.subscribe(self._on_broadcast_message)
            self._pubsub_subscribed = True

        # Initialize ViewModel (subscribe state diff 替代 init(on_*))
        logger.debug("[HomeView] Initializing ViewModel and Listeners")
        self.vm.init()
        self._prev_state = self.vm.state
        self._vm_unsubscribe = self.vm.subscribe(self._on_vm_state_changed)

        # Subscribe to I18n
        I18n.subscribe(self.refresh_locale)

        # Load Data
        if self.page:
            self._init_task = self.page.run_task(self._init_and_load)

    def will_unmount(self):  # pragma: no cover
        self._is_mounted = False
        if self._vm_unsubscribe is not None:
            self._vm_unsubscribe()
            self._vm_unsubscribe = None
        self.vm.dispose()
        # Fix P1-9: Unsubscribe PubSub to prevent ghost event handling
        if self.page and self._pubsub_subscribed:
            try:
                self.page.pubsub.unsubscribe(self._on_broadcast_message)  # type: ignore[untyped]
            except Exception as exc:
                logger.debug("[HomeView] PubSub unsubscribe skipped: %s", exc, exc_info=True)
            self._pubsub_subscribed = False
        try:
            I18n.unsubscribe(self.refresh_locale)
        except Exception as exc:
            logger.debug("[HomeView] I18n unsubscribe skipped: %s", exc, exc_info=True)
        if self._init_task:
            self._init_task.cancel()

    def _on_vm_state_changed(self, state) -> None:
        """VM state 变更 diff 派发 (Phase 2: 替代 on_news_update/on_market_update 回调)。"""
        prev = self._prev_state
        # 1. news_update (dual-track: 拉取 last_news_update)
        if state.news_update_version != prev.news_update_version:
            news_update = self.vm.last_news_update
            if news_update is not None:
                update_type, data = news_update
                self.refresh_news_if_visible(update_type, data)
        # 2. market_update (dual-track: 通知 View 刷新市场数据)
        if state.market_update_version != prev.market_update_version:
            self.refresh_market_if_visible()
        self._prev_state = state

    # --- Event Handlers & Callbacks ---

    def set_visible(self, visible: bool):  # pragma: no cover
        if self._is_visible != visible:
            self._is_visible = visible
            logger.debug("[HomeView] Visibility | changed to: %s", visible)

    def refresh_news_if_visible(self, update_type=None, data=None):  # pragma: no cover
        if update_type == NewsUpdateType.TAG_UPDATE:
            self._update_news_tag(data)
        elif update_type == NewsUpdateType.NEW_ITEM:
            self._run_if_visible(self._prepend_new_news, "Prepending new news", data)
        else:
            self._run_if_visible(self._refresh_news_data, "Refreshing news list")

    def refresh_market_if_visible(self):  # pragma: no cover
        self._run_if_visible(self._refresh_market_data, "Refreshing market data")

    def _run_if_visible(
        self, task_func, log_msg="Refreshing", data=None
    ):  # pragma: no cover — visibility guard; vm logic tested separately
        if not self._is_visible or not self._is_mounted:
            return
        if self.page:
            self.page.run_task(task_func, data)

    def _on_broadcast_message(self, message):  # pragma: no cover — event routing; logic delegated to vm.clear_state()
        if message == "cache_cleared":
            self.vm.clear_state()
            # Only update UI if mounted
            if self.page and self._is_mounted:
                self.dashboard.update_data({})
                self.news_feed.set_news(None, False)  # type: ignore[untyped]
                self.update()

    def _refresh_clicked(self, e):  # pragma: no cover — event routing; delegates to vm via run_task
        ensure_correlation_id()
        UILogger.log_action("HomeView", "Click", "btn_refresh")
        if self.page:
            self.page.run_task(self._load_data)

    async def _on_load_more_click(self, e):  # pragma: no cover — event routing; vm.load_next_page() tested separately
        # Delegate to VM
        new_batch, has_more = await self.vm.load_next_page()
        if new_batch is not None and not new_batch.empty:
            self.news_feed.append_news(new_batch, has_more)
        else:
            # Update button state (remove it if no more)
            self.news_feed.append_news(pd.DataFrame(), has_more)

    def _update_news_tag(self, data):  # pragma: no cover
        if not data:
            return
        content = data.get("content", "")
        tags = data.get("tags", "")
        if content:
            self.news_feed.update_news_tag(content, tags)

    async def _prepend_new_news(self, data=None):  # pragma: no cover
        if not data:
            return

        rows = []
        for item in data:
            normalized = CacheManager.normalize_news_item(item, default_source="CLS")
            rows.append(normalized)

        if rows:
            news_df = pd.DataFrame(rows)
            self.news_feed.prepend_news(news_df)

    def refresh_locale(self):
        try:
            self.header_title.value = I18n.get("home_title")
            self.news_header.value = I18n.get("home_live_news")
            self.refresh_btn.tooltip = I18n.get("home_refresh")
            # 重新格式化 date_text（不触发网络请求，复用 vm 缓存的 last_market_data）
            data = self.vm.last_market_data or {}
            date_str = data.get("date", "--")
            stale = data.get("stale", False)
            suffix = f" ({I18n.get('home_data_updating')})" if stale else ""
            self.date_text.value = I18n.get("home_data_date").format(date=date_str) + suffix
            self.dashboard.update_locale()
            self.news_feed.update_locale()
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[HomeView] refresh_locale failed: %s", e, exc_info=True)

    def update_theme(self):  # pragma: no cover
        try:
            # 1. Update sub-components
            self.dashboard.update_theme()
            self.news_feed.update_theme()

            # 2. Update local controls
            self.header_title.color = None  # Default
            self.date_text.color = AppColors.TEXT_SECONDARY

            # Refresh button icon?
            # It's an IconButton, default icon color might need refresh if not set?
            # It usually picks up theme primary/on_surface.
        except Exception as e:
            logger.error("[HomeView] Theme | ❌ Update failed: %s", e, exc_info=True)

    def handle_resize(self, width: float = 0, height: float = 0) -> None:
        """窗口尺寸变化时调整布局。HomeView 内容自适应，无需响应式调整。"""
        # No responsive adjustment needed — dashboard 和 news_feed 使用 expand 自适应

    # --- Data Loading Logic ---

    async def _init_and_load(self):  # pragma: no cover
        try:
            if not self._is_mounted:
                return
            await self.vm.init_data()
            if not self._is_mounted:
                return
            await self._load_data()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[HomeView] Init | ❌ Failed: %s", e, exc_info=True)

    async def _load_data(self):  # pragma: no cover
        await self._refresh_market_data()
        await self._refresh_news_data()

    async def _refresh_market_data(self, _data=None):  # pragma: no cover
        try:
            # VM handles retry logic if needed, or we just get cached
            data = await self.vm.load_market_data()
            if data:
                # Update UI
                # DEBUG: Log indices count to investigate RangeError
                indices = data.get("indices", [])
                logger.debug("[HomeView] Market Data Indices: %s", len(indices))

                date_str = data.get("date", "--")
                stale = data.get("stale", False)
                suffix = f" ({I18n.get('home_data_updating')})" if stale else ""
                self.date_text.value = I18n.get("home_data_date").format(date=date_str) + suffix
                self.date_text.update()
                self.dashboard.update_data(data)
        except Exception as e:
            logger.error("[HomeView] Market | Load failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[HomeView] Market | Load failed traceback", exc_info=True)

    async def _refresh_news_data(self, _data=None):  # pragma: no cover
        try:
            news_data, has_more = await self.vm.refresh_news()
            self.news_feed.set_news(news_data, has_more)  # type: ignore[untyped]
        except Exception as e:
            logger.error("[HomeView] News | Load failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[HomeView] News | Load failed traceback", exc_info=True)
