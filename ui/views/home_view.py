import asyncio
import logging

import flet as ft

import pandas as pd
from data.data_processor import DataProcessor
from data.market_data_service import MarketDataService
from ui.i18n import I18n
from ui.theme import AppColors

logger = logging.getLogger(__name__)


class HomeView(ft.Container):
    def __init__(self, on_run_strategy=None):
        super().__init__()
        self.expand = True
        self.processor = DataProcessor()
        self.on_run_strategy = on_run_strategy

        # Pagination State
        self.news_page = 0
        self.PAGE_SIZE = 20
        self.has_more_news = False
        self._is_loading_more = False  # Lock for load more action

        # Auto-refresh Configuration
        self.REFRESH_INTERVAL_SECONDS = 30
        self._refresh_task = None
        self._init_task = None  # Track init task for cancellation
        self._is_mounted = False
        self._is_visible = True  # Track if HomeView is the active visible view
        self._data_loaded = False  # Skip re-init if data already loaded
        self._pubsub_subscribed = False  # Prevent duplicate pubsub subscriptions

        # Data Cache (for UI Rebuild)
        self.last_data = {}
        self.news_data = None  # DataFrame

        # Build initial UI
        self.content = self._build_ui()

        # Subscribe to locale changes
        I18n.subscribe(self.refresh_locale)

    def refresh_locale(self):
        """Rebuild UI on locale change"""
        try:
            # Rebuild UI using cached data
            self.content = self._build_ui(self.last_data)
            if self.page and self._is_mounted:
                self.update()
        except Exception as e:
            logger.error(f"Error refreshing locale: {e}")
    
    def _run_if_visible(self, task_func, log_msg="Refreshing"):
        """辅助方法：仅在可见且已挂载时运行任务"""
        if not self._is_visible or not self._is_mounted:
            logger.debug(f"[HomeView] Skipping {log_msg} - not visible or not mounted")
            return
        
        logger.debug(f"[HomeView] {log_msg}")
        if self.page:
            self.page.run_task(task_func)

    def refresh_news_if_visible(self):
        """刷新新闻列表（供 NewsSubscriptionService 回调使用）"""
        self._run_if_visible(self._refresh_news_only, "Refreshing news list")
    
    def refresh_market_if_visible(self):
        """刷新市场数据（供 MarketDataService 回调使用）"""
        self._run_if_visible(self._refresh_from_cache, "Refreshing market data from cache")

    def did_mount(self):
        import time as _time
        _t0 = _time.perf_counter()
        logger.debug("[PERF] >>> HomeView.did_mount START")

        # Subscribe to broadcast messages (only once)
        if self.page and not self._pubsub_subscribed:
            self.page.pubsub.subscribe(self._on_broadcast_message)
            self._pubsub_subscribed = True

        self._is_mounted = True

        # Only initialize data on first mount or if data was cleared
        if not self._data_loaded:
            if self.page:
                self._init_task = self.page.run_task(self._init_and_load)
        else:
            # Data already loaded, just restart auto-refresh
            # Force UI update to ensure data is visible after tab switch
            if self.last_data:
                self.content = self._build_ui(self.last_data)
                self.update()
            
            # self._start_auto_refresh()
            logger.debug("[HomeView] Skipping re-init - data restored")

        logger.debug(f"[PERF] <<< HomeView.did_mount END (sync part) took {(_time.perf_counter()-_t0)*1000:.1f}ms")

    async def _init_and_load(self):
        """Initial load with DB init"""
        import time as _time
        _t_start = _time.perf_counter()
        logger.debug("[PERF] >>> HomeView._init_and_load START")
        try:
            # Check if still mounted before each long operation
            if not self._is_mounted:
                logger.debug("[HomeView] _init_and_load cancelled - view unmounted")
                return

            _t0 = _time.perf_counter()
            await self.processor.init_data()
            logger.debug(f"[PERF] HomeView: processor.init_data() took {(_time.perf_counter()-_t0)*1000:.1f}ms")

            if not self._is_mounted:
                logger.debug("[HomeView] _init_and_load cancelled after init - view unmounted")
                return

            _t0 = _time.perf_counter()
            await self._load_data()
            logger.debug(f"[PERF] HomeView: _load_data() took {(_time.perf_counter()-_t0)*1000:.1f}ms")

            if self._is_mounted:
                self._data_loaded = True  # Mark data as loaded
                # self._start_auto_refresh()

            logger.debug(f"[PERF] <<< HomeView._init_and_load END, TOTAL={(_time.perf_counter()-_t_start)*1000:.1f}ms")
        except asyncio.CancelledError:
            logger.debug("[HomeView] _init_and_load was cancelled")
        except Exception as e:
            logger.error(f"[HomeView] Init failed: {e}")

    def will_unmount(self):
        """Cleanup when view is detached"""
        self._is_mounted = False
        # self._stop_auto_refresh()
        # Cancel any pending init task
        if self._init_task:
            self._init_task.cancel()
            self._init_task = None
        # Subscriptions persist for singleton views, no need to unsubscribe (it clears all)
        # I18n.unsubscribe(self.refresh_locale) # Singleton: keep listening

    # Auto-refresh logic moved to MarketDataService (event-driven)
    # def _start_auto_refresh(self): ...
    # def _stop_auto_refresh(self): ...
    # async def _auto_refresh_loop(self): ...

    def set_visible(self, visible: bool):
        """Set the visibility state of HomeView (called by main.py on tab change)"""
        self._is_visible = visible
        logger.debug(f"[HomeView] Visibility set to: {visible}")

    def _on_broadcast_message(self, message):
        """Handle broadcast messages"""
        if message == "cache_cleared":
            # Clear in-memory news list immediately
            self.last_data = {}
            self.news_data = None
            self.has_more_news = False
            self.news_page = 0
            self._data_loaded = False  # Force re-init on next mount

            # Rebuild UI to show empty state
            self.content = self._build_ui({})
            
            # Only update UI if mounted
            if self.page and self._is_mounted:
                self.update()

    def _refresh_data(self, e):
        # Run async task
        if self.page:
            self.page.run_task(self._load_data)

    async def _load_data(self):
        import time as _time
        _t_start = _time.perf_counter()
        logger.debug("[PERF] >>> HomeView._load_data START")
        try:
            # Reset pagination on full refresh
            self.news_page = 0
            self.has_more_news = True

            # 从 MarketDataService 缓存读取市场数据
            # 首次加载时服务可能还没准备好，等待重试
            _t0 = _time.perf_counter()
            data = None
            for attempt in range(5):  # 最多等待 2.5 秒
                data = MarketDataService().get_cached_data()
                if data:
                    break
                await asyncio.sleep(0.5)
                logger.debug(f"[HomeView] Waiting for market data cache... (attempt {attempt + 1})")
            
            logger.debug(f"[PERF] HomeView: get_cached_data() took {(_time.perf_counter()-_t0)*1000:.1f}ms")
            
            if not data:
                logger.debug("[PERF] <<< HomeView._load_data END (no cached data)")
                return

            # Update Cache
            self.last_data = data

            # 读取新闻数据（由 NewsSubscriptionService 后台服务负责同步和保存）
            _t0 = _time.perf_counter()
            await self._load_news_data()
            logger.debug(f"[PERF] HomeView: _load_news_data() took {(_time.perf_counter()-_t0)*1000:.1f}ms")

            # Rebuild UI with fresh data
            _t0 = _time.perf_counter()
            self.content = self._build_ui(self.last_data)
            logger.debug(f"[PERF] HomeView: _build_ui() took {(_time.perf_counter()-_t0)*1000:.1f}ms")
            
            _t0 = _time.perf_counter()
            if self.page:
                self.update()
            logger.debug(f"[PERF] HomeView: self.update() took {(_time.perf_counter()-_t0)*1000:.1f}ms")
            
            logger.debug(f"[PERF] <<< HomeView._load_data END, TOTAL={(_time.perf_counter()-_t_start)*1000:.1f}ms")

        except Exception as e:
            logger.error(f"Error loading home data: {e}")
    
    async def _refresh_news_only(self):
        """只刷新新闻部分，不重新加载其他数据（供事件驱动刷新使用）"""
        try:
            await self._load_news_data()
            # 只重建 UI（使用缓存的 last_data）
            self.content = self._build_ui(self.last_data)
            if self.page:
                self.update()
            logger.debug("[HomeView] News section refreshed")
        except Exception as e:
            logger.error(f"[HomeView] Failed to refresh news: {e}")
    

    
    async def _refresh_from_cache(self):
        """从 MarketDataService 缓存读取市场数据并刷新 UI"""
        try:
            data = MarketDataService().get_cached_data()
            if not data:
                logger.debug("[HomeView] No cached market data available")
                return
            
            # 更新行情缓存
            self.last_data = data
            
            # 重建 UI（使用缓存的 news_data）
            self.content = self._build_ui(self.last_data)
            if self.page:
                self.update()
            logger.debug("[HomeView] UI refreshed from cache")
        except Exception as e:
            logger.error(f"[HomeView] Failed to refresh from cache: {e}")

    async def _on_load_more_click(self, e):
        """Handle Load More button click"""
        if self._is_loading_more or not self.has_more_news:
            return

        self._is_loading_more = True
        try:
            # Try to load next page
            next_page = self.news_page + 1
            success = await self._load_news_data(target_page=next_page)

            if success:
                # Rebuild UI
                self.content = self._build_ui(self.last_data)
                if self.page:
                    self.update()
        finally:
            self._is_loading_more = False

    async def _load_news_data(self, target_page=0):
        """
        Load news data for a specific page.
        Args:
            target_page: The page index to load.
        """
        try:
            offset = target_page * self.PAGE_SIZE

            # Fetch batch
            new_batch = await self.processor.cache.get_market_news(
                limit=self.PAGE_SIZE,
                offset=offset
            )

            if new_batch.empty:
                self.has_more_news = False
                # If we were trying to load page 0 and it's empty, clear data
                if target_page == 0:
                    self.news_data = None
            else:
                # If loading page 0 (refresh), replace data. Else append.
                if target_page == 0:
                    self.news_data = new_batch
                    self.news_page = 0
                else:
                    self.news_data = pd.concat([self.news_data, new_batch], ignore_index=True)
                    self.news_page = target_page

                # Check if we likely have more
                if len(new_batch) < self.PAGE_SIZE:
                    self.has_more_news = False
                else:
                    self.has_more_news = True
                    
            return True

        except Exception as e:
            logger.error(f"Error loading news: {e}")
            return False

    def _build_ui(self, data=None):
        """
        Rebuilds the entire UI structure. 
        Args:
            data: dict of market data (indices, hsgt, etc.)
        """
        if data is None:
            data = {}

        # --- UI Components Construction ---

        # 1. Header Section
        date_str = data.get('date', '--')
        header = ft.Row([
            ft.Text(I18n.get("home_title"), size=24, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.Text(I18n.get("home_data_date").format(date=date_str), size=12, color=ft.Colors.GREY),
            ft.IconButton(ft.Icons.REFRESH, on_click=self._refresh_data, tooltip=I18n.get("home_refresh"))
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        # 2. Market Indices Section
        indices = data.get('indices', [])
        # Default placeholders
        sh = {'value': '--', 'change': '--', 'color': 'grey'}
        sz = {'value': '--', 'change': '--', 'color': 'grey'}
        cyb = {'value': '--', 'change': '--', 'color': 'grey'}

        if len(indices) >= 3:
            sh, sz, cyb = indices[0], indices[1], indices[2]

        def _mk_idx_card(title, info):
            if not isinstance(info, dict): info = {}
            val_color = getattr(ft.Colors, info.get('color', 'GREY').upper(), ft.Colors.GREY)
            return self._build_dashboard_card(
                title,
                ft.Text(str(info.get('value', '--')), size=20, weight=ft.FontWeight.BOLD),
                ft.Text(str(info.get('change', '--')), size=14, weight=ft.FontWeight.BOLD, color=val_color)
            )

        # HSGT
        hsgt = data.get('hsgt') or {}
        hsgt_val = hsgt.get('value', '--') or '--'
        hsgt_sub = hsgt.get('sub', '--') or '--'
        hsgt_sub = hsgt.get('sub', '--') or '--'
        # 使用 Service 返回的 color，如果不存在则回退到 GREY
        color_str = hsgt.get('color', 'GREY').upper()
        hsgt_color = getattr(ft.Colors, color_str, ft.Colors.GREY)

        indices_row = ft.ResponsiveRow([
            _mk_idx_card(I18n.get("home_index_sh"), sh),
            _mk_idx_card(I18n.get("home_index_sz"), sz),
            _mk_idx_card(I18n.get("home_index_cyb"), cyb),
            self._build_dashboard_card(
                I18n.get("home_northbound"),
                ft.Text(hsgt_val, size=20, weight=ft.FontWeight.BOLD, color=hsgt_color),
                ft.Text(hsgt_sub, size=12, color=ft.Colors.GREY_500)
            ),
        ])

        # 3. Hot Concepts Section
        hot_concepts = data.get('hot_concepts', [])
        concept_cards = []
        if hot_concepts:
            concept_cards = [
                ft.ResponsiveRow(
                    controls=[self._build_concept_card(c) for c in hot_concepts if c.get('name')],
                    run_spacing=10,
                )
            ]
        else:
            concept_cards = [ft.Text(I18n.get("home_hot_concepts_empty"), size=12, color=ft.Colors.GREY)]

        concepts_section = ft.Column([
            ft.Text(I18n.get("home_hot_concepts"), size=16, weight=ft.FontWeight.BOLD),
            *concept_cards
        ], spacing=10)

        # 4. News Feed Section
        news_section = self._build_news_section()

        # Assemble Full Layout
        return ft.Column(
            scroll=None,  # Disable outer scroll so inner listview handles scrolling
            expand=True,
            controls=[
                header,
                ft.Divider(),
                indices_row,
                ft.Container(height=10),
                concepts_section,
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                ft.Text(I18n.get("home_live_news"), size=20, weight=ft.FontWeight.BOLD),
                news_section
            ]
        )

    def _build_news_section(self):
        """Builder for the news list section with empty state handling"""
        # Case 1: Empty Data (No News)
        if self.news_data is None or self.news_data.empty:
            return ft.Container(
                content=ft.Column([
                    ft.Icon(ft.Icons.ARTICLE_OUTLINED, size=48, color=ft.Colors.GREY_300),
                    ft.Text(I18n.get("home_news_empty"), color=ft.Colors.GREY)
                ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                alignment=ft.alignment.center,
                expand=True,
                bgcolor=AppColors.SURFACE,
                border_radius=12,
                border=ft.border.all(1, AppColors.BORDER),
                padding=20
            )
            
        # Case 2: News List Present
        news_items = []
        for _, row in self.news_data.iterrows():
            news_items.append(self._build_news_item(row))

        # Add "Load More" button only if we have more pages
        if self.has_more_news:
            load_more_btn = ft.Container(
                content=ft.ElevatedButton(
                    text=I18n.get("news_load_more"),
                    on_click=self._on_load_more_click,
                    style=ft.ButtonStyle(
                        color=AppColors.TEXT_SECONDARY,
                        bgcolor=ft.Colors.TRANSPARENT,
                        shape=ft.RoundedRectangleBorder(radius=8),
                        side=ft.BorderSide(1, AppColors.BORDER)
                    )
                ),
                alignment=ft.alignment.center,
                padding=10
            )
            news_items.append(load_more_btn)

        news_list = ft.ListView(
            controls=news_items,
            spacing=10,
            padding=10,
            auto_scroll=False,
            expand=True
        )

        return ft.Container(
            content=news_list,
            expand=True,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
            border=ft.border.all(1, AppColors.BORDER),
            padding=10
        )

    def _build_news_item(self, row):
        raw_tag = row.get('tags', '') or ''  # Ensure not None
        # Localize tags using new keys
        # Mapping: 'Stock' -> 'tag_stock', etc.
        tag_key = f"tag_{raw_tag.lower()}"
        # Fallback to raw tag if no translation found, but try I18n first
        translated_tag = I18n.get(tag_key)
        if translated_tag == tag_key:  # Key missing/returned itself
            # Fallback logic for unknown tags or composite tags "Stock,Global"
            # Try splitting by comma
            tags = [t.strip() for t in raw_tag.split(',') if t.strip()]
            translated_parts = []
            for t in tags:
                tk = f"tag_{t.lower()}"
                tv = I18n.get(tk)
                translated_parts.append(tv if tv != tk else t)

            if translated_parts:
                translated_tag = ",".join(translated_parts)
            else:
                translated_tag = raw_tag

        content = str(row.get('content', '') or '')
        time_str = str(row.get('publish_time', '') or '')

        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(translated_tag, color=ft.Colors.BLUE, weight=ft.FontWeight.BOLD, size=12),
                    ft.Text(time_str[-8:], color=ft.Colors.GREY, size=12)  # HH:MM:SS
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Text(content, size=14, color=AppColors.TEXT_PRIMARY)
            ]),
            padding=10,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.BLUE) if "利好" in content else ft.Colors.TRANSPARENT,
            border=ft.border.only(bottom=ft.BorderSide(1, ft.Colors.GREY_200))
        )

    def _build_dashboard_card(self, title, control1, control2):
        """Unified card builder for dashboard stats"""
        return ft.Container(
            content=ft.Column([
                ft.Text(title, size=14, color=AppColors.TEXT_SECONDARY, no_wrap=True),
                control1,
                control2,
            ], spacing=5),
            padding=20,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
            border=ft.border.all(1, AppColors.BORDER),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            ),
            col={"xs": 6, "sm": 6, "md": 3, "lg": 3},
        )

    def _build_concept_card(self, item):
        name = item.get('name', '--')
        change = item.get('change', '0.00%')
        color_str = str(item.get('color', ''))
        is_up = 'red' in color_str
        color = ft.Colors.RED if is_up else ft.Colors.GREEN

        return ft.Container(
            content=ft.Column([
                ft.Text(name, size=14, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY, no_wrap=True),
                ft.Row([
                    ft.Icon(ft.Icons.TRENDING_UP if is_up else ft.Icons.TRENDING_DOWN,
                            color=color, size=16),
                    ft.Text(change, size=16, weight=ft.FontWeight.BOLD, color=color)
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ], spacing=5),
            padding=15,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
            border=ft.border.all(1, AppColors.BORDER),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=5,
                color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK),
                offset=ft.Offset(0, 2)
            ),
            col={"xs": 6, "sm": 4, "md": 3, "lg": 2}
        )
