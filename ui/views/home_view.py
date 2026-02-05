import asyncio
import logging

import flet as ft

from data.data_processor import DataProcessor
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
        self.has_more_news = True

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
            if self.page:
                self.update()
        except Exception as e:
            logger.error(f"Error refreshing locale: {e}")

    def did_mount(self):
        import time as _time
        _t0 = _time.perf_counter()
        logger.info("[PERF] >>> HomeView.did_mount START")

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
            self._start_auto_refresh()
            logger.debug("[HomeView] Skipping re-init - data already loaded")

        logger.info(f"[PERF] <<< HomeView.did_mount END (sync part) took {(_time.perf_counter()-_t0)*1000:.1f}ms")

    async def _init_and_load(self):
        """Initial load with DB init"""
        import time as _time
        _t_start = _time.perf_counter()
        logger.info("[PERF] >>> HomeView._init_and_load START")
        try:
            # Check if still mounted before each long operation
            if not self._is_mounted:
                logger.debug("[HomeView] _init_and_load cancelled - view unmounted")
                return

            _t0 = _time.perf_counter()
            await self.processor.init_data()
            logger.info(f"[PERF] HomeView: processor.init_data() took {(_time.perf_counter()-_t0)*1000:.1f}ms")

            if not self._is_mounted:
                logger.debug("[HomeView] _init_and_load cancelled after init - view unmounted")
                return

            _t0 = _time.perf_counter()
            await self._load_data()
            logger.info(f"[PERF] HomeView: _load_data() took {(_time.perf_counter()-_t0)*1000:.1f}ms")

            if self._is_mounted:
                self._data_loaded = True  # Mark data as loaded
                self._start_auto_refresh()

            logger.info(f"[PERF] <<< HomeView._init_and_load END, TOTAL={(_time.perf_counter()-_t_start)*1000:.1f}ms")
        except asyncio.CancelledError:
            logger.debug("[HomeView] _init_and_load was cancelled")
        except Exception as e:
            logger.error(f"[HomeView] Init failed: {e}")

    def will_unmount(self):
        """Cleanup when view is detached"""
        self._is_mounted = False
        self._stop_auto_refresh()
        # Cancel any pending init task
        if self._init_task:
            self._init_task.cancel()
            self._init_task = None
        # Note: pubsub subscription persists - we use _pubsub_subscribed flag to prevent duplicates
        # Flet handles cleanup automatically when page closes
        I18n.unsubscribe(self.refresh_locale)

    def _start_auto_refresh(self):
        """Start the periodic auto-refresh task"""
        if self._refresh_task is None and self.page and self._is_mounted:
            self._refresh_task = self.page.run_task(self._auto_refresh_loop)
            logger.info(f"[HomeView] Auto-refresh started (interval: {self.REFRESH_INTERVAL_SECONDS}s)")

    def _stop_auto_refresh(self):
        """Stop the auto-refresh task"""
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
            logger.info("[HomeView] Auto-refresh stopped")

    async def _auto_refresh_loop(self):
        """Background loop for periodic data refresh"""
        while self._is_mounted:
            try:
                await asyncio.sleep(self.REFRESH_INTERVAL_SECONDS)
                if self._is_mounted and self._is_visible:
                    logger.debug("[HomeView] Auto-refreshing data...")
                    await self._load_data()
                elif self._is_mounted:
                    logger.debug("[HomeView] Skipped auto-refresh - view not visible")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[HomeView] Auto-refresh error: {e}")

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
            if self.page:
                self.update()

    def _refresh_data(self, e):
        # Run async task
        if self.page:
            self.page.run_task(self._load_data)

    async def _load_data(self):
        import time as _time
        _t_start = _time.perf_counter()
        logger.info("[PERF] >>> HomeView._load_data START")
        try:
            # Reset pagination on full refresh
            self.news_page = 0
            self.has_more_news = True

            _t0 = _time.perf_counter()
            data = await self.processor.get_market_overview()
            logger.info(f"[PERF] HomeView: get_market_overview() took {(_time.perf_counter()-_t0)*1000:.1f}ms")
            
            if not data:
                logger.info("[PERF] <<< HomeView._load_data END (no data)")
                return

            # Update Cache
            self.last_data = data

            # Sync News immediately (Deep Sync Strategy)
            _t0 = _time.perf_counter()
            await self.processor.sync_market_news()
            logger.info(f"[PERF] HomeView: sync_market_news() took {(_time.perf_counter()-_t0)*1000:.1f}ms")

            # Load News Data
            _t0 = _time.perf_counter()
            await self._load_news_data()
            logger.info(f"[PERF] HomeView: _load_news_data() took {(_time.perf_counter()-_t0)*1000:.1f}ms")

            # Rebuild UI with fresh data
            _t0 = _time.perf_counter()
            self.content = self._build_ui(self.last_data)
            logger.info(f"[PERF] HomeView: _build_ui() took {(_time.perf_counter()-_t0)*1000:.1f}ms")
            
            _t0 = _time.perf_counter()
            if self.page:
                self.update()
            logger.info(f"[PERF] HomeView: self.update() took {(_time.perf_counter()-_t0)*1000:.1f}ms")
            
            logger.info(f"[PERF] <<< HomeView._load_data END, TOTAL={(_time.perf_counter()-_t_start)*1000:.1f}ms")

        except Exception as e:
            logger.error(f"Error loading home data: {e}")

    async def _on_load_more_click(self, e):
        """Handle Load More button click"""
        if self.has_more_news:
            self.news_page += 1
            await self._load_news_data(load_more=True)

            # Rebuild UI to append news (or in full rebuild case, show all)
            # Optimization: could just update news list, but full rebuild is safer for I18n consistency
            self.content = self._build_ui(self.last_data)
            if self.page:
                self.update()

    async def _load_news_data(self, load_more=False):
        try:
            offset = self.news_page * self.PAGE_SIZE

            # Fetch batch
            new_batch = await self.processor.cache.get_market_news(
                limit=self.PAGE_SIZE,
                offset=offset
            )

            if new_batch.empty:
                self.has_more_news = False
                if not load_more:
                    self.news_data = None
            else:
                if not load_more or self.news_data is None:
                    self.news_data = new_batch
                else:
                    # Append new data
                    import pandas as pd
                    self.news_data = pd.concat([self.news_data, new_batch], ignore_index=True)

                # Check if we likely have more
                if len(new_batch) < self.PAGE_SIZE:
                    self.has_more_news = False

        except Exception as e:
            logger.error(f"Error loading news: {e}")

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
            val_color = getattr(ft.Colors, info.get('color', 'GREY').upper())
            return self._build_dashboard_card(
                title,
                ft.Text(info.get('value', '--'), size=20, weight=ft.FontWeight.BOLD),
                ft.Text(info.get('change', '--'), size=14, weight=ft.FontWeight.BOLD, color=val_color)
            )

        # HSGT
        hsgt = data.get('hsgt', {})
        hsgt_val = hsgt.get('value', '--')
        hsgt_sub = hsgt.get('sub', '--')
        is_inflow = I18n.get("home_inflow") in hsgt_sub
        hsgt_color = ft.Colors.RED if is_inflow else ft.Colors.GREEN if 'out' in hsgt_sub or '流出' in hsgt_sub else ft.Colors.GREY

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
        news_items = []
        if self.news_data is not None and not self.news_data.empty:
            for _, row in self.news_data.iterrows():
                news_items.append(self._build_news_item(row))
        else:
            news_items.append(ft.Text(I18n.get("home_news_empty"), color=ft.Colors.GREY))

        # Load More Button
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
            padding=10,
            visible=self.has_more_news and len(news_items) > 0
        )

        news_list = ft.ListView(
            controls=news_items + [load_more_btn],
            spacing=10,
            padding=10,
            auto_scroll=False,
            expand=True  # Important: Expand within the column
        )

        news_section = ft.Container(
            content=news_list,
            # Removed fixed height=400 to allow expansion
            expand=True,
            bgcolor=AppColors.SURFACE,
            border_radius=12,
            border=ft.border.all(1, AppColors.BORDER),
            padding=10
        )

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

        content = row.get('content', '')
        time_str = row.get('publish_time', '')

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
