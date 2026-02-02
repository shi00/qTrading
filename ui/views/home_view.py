import flet as ft
from data.data_processor import DataProcessor
from ui.theme import AppColors, AppStyles
from ui.i18n import I18n
import logging
import asyncio

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
        self._is_mounted = False
        
        # UI State Controls
        self.date_label = ft.Text(I18n.get("home_data_date").format(date="--"), size=12, color=ft.Colors.GREY)
        
        # Indices Controls
        self.sh_value = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
        self.sh_change = ft.Text("--", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        
        self.sz_value = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
        self.sz_change = ft.Text("--", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        
        self.cyb_value = ft.Text("--", size=20, weight=ft.FontWeight.BOLD)
        self.cyb_change = ft.Text("--", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        
        # HSGT Controls
        self.hsgt_value = ft.Text("--", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY)
        self.hsgt_sub = ft.Text("--", size=12, color=ft.Colors.GREY_500)
        
        # Hot Concepts
        self.hot_concepts_container = ft.Container()
        
        # News Control
        self.news_list = ft.ListView(spacing=10, padding=10, auto_scroll=False, expand=True)
        self.load_more_btn = ft.ElevatedButton(
            text=I18n.get("news_load_more") if I18n.get("news_load_more") != "news_load_more" else "加载更多",
            on_click=self._on_load_more_click,
            visible=False,
            style=ft.ButtonStyle(
                color=AppColors.TEXT_SECONDARY,
                bgcolor=ft.Colors.TRANSPARENT,
                shape=ft.RoundedRectangleBorder(radius=8),
                side=ft.BorderSide(1, AppColors.BORDER)
            )
        )
        
        self.content = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                ft.Row([
                    ft.Text(I18n.get("home_title"), size=24, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    self.date_label,
                    ft.IconButton(ft.Icons.REFRESH, on_click=self._refresh_data, tooltip=I18n.get("home_refresh"))
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(),
                # Market Indices
                ft.ResponsiveRow(
                    [
                        self._build_dashboard_card(I18n.get("home_index_sh"), self.sh_value, self.sh_change),
                        self._build_dashboard_card(I18n.get("home_index_sz"), self.sz_value, self.sz_change),
                        self._build_dashboard_card(I18n.get("home_index_cyb"), self.cyb_value, self.cyb_change),
                        self._build_dashboard_card(I18n.get("home_northbound"), self.hsgt_value, self.hsgt_sub),
                    ],
                ),
                ft.Container(height=10),
                
                # Hot Concepts Section
                self.hot_concepts_container,
                
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),

                
                ft.Text(I18n.get("home_live_news"), size=20, weight=ft.FontWeight.BOLD),
                ft.Container(
                    content=self._build_news_feed(),
                    height=400, # Fixed height scrollable area
                    bgcolor=AppColors.SURFACE,
                    border_radius=12,
                    border=ft.border.all(1, AppColors.BORDER),
                    padding=10
                )
            ]
        )
        
        # Subscribe to locale changes
        I18n.subscribe(self.refresh_locale)

    def refresh_locale(self):
        """Rebuild UI text on locale change - requires page reload for full effect"""
        # Note: Since content structure is built in __init__, a full rebuild would require
        # re-creating controls. For simplicity, we just log. A page reload is recommended.
        try:
            if self.page:
                self.page.update()
        except Exception:
            pass

    def did_mount(self):
        # Subscribe to broadcast messages
        if self.page:
            self.page.pubsub.subscribe(self._on_broadcast_message)
        
        self._is_mounted = True
        
        # Initialize Data & Auto load
        # Use task to avoid blocking did_mount UI thread
        if self.page:
            self.page.run_task(self._init_and_load)

    async def _init_and_load(self):
        """Initial load with DB init"""
        try:
            await self.processor.init_data()
            await self._load_data()
            # Start timer only INITED
            self._start_auto_refresh()
        except Exception as e:
            logger.error(f"[HomeView] Init failed: {e}")
            
    def will_unmount(self):
        """Cleanup when view is detached"""
        self._is_mounted = False
        self._stop_auto_refresh()

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
                if self._is_mounted:
                    logger.debug("[HomeView] Auto-refreshing data...")
                    await self._load_data()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[HomeView] Auto-refresh error: {e}")

    def _on_broadcast_message(self, message):
        """Handle broadcast messages"""
        if message == "cache_cleared":
            # Clear in-memory news list immediately
            self.news_list.controls.clear()
            self.news_list.controls.append(ft.Text("暂无新闻", color=ft.Colors.GREY))
            self.has_more_news = False
            self.news_page = 0
            
            # If validated page, update UI
            if self.page:
                self.news_list.update()
                # self.update() # Optional: if we want to ensure full refresh, but news list is enough

    def _refresh_data(self, e):
        # Run async task
        if self.page:
             self.page.run_task(self._load_data)

    async def _load_data(self):
        try:
             # Reset pagination on full refresh
             self.news_page = 0
             self.has_more_news = True
             
             data = await self.processor.get_market_overview()
             if not data:
                 return
             
             
             
             # Sync News immediately
             # Deep Sync Strategy: handled by DataProcessor internally (first run = 200 items)
             await self.processor.sync_market_news()
             
             # Update Date
             self.date_label.value = I18n.get("home_data_date").format(date=data.get('date', '--'))
             
             # Update Indices
             indices = data.get('indices', [])
             if len(indices) >= 3:
                 sh, sz, cyb = indices[0], indices[1], indices[2]
                 
                 self.sh_value.value = sh['value']
                 self.sh_change.value = sh['change']
                 self.sh_change.color = getattr(ft.Colors, sh['color'].upper())
                 
                 self.sz_value.value = sz['value']
                 self.sz_change.value = sz['change']
                 self.sz_change.color = getattr(ft.Colors, sz['color'].upper())
                 
                 self.cyb_value.value = cyb['value']
                 self.cyb_change.value = cyb['change']
                 self.cyb_change.color = getattr(ft.Colors, cyb['color'].upper())

             # Update HSGT
             hsgt = data.get('hsgt', {})
             self.hsgt_value.value = hsgt.get('value', '--')
             self.hsgt_value.color = ft.Colors.RED if I18n.get("home_inflow") in hsgt.get('sub', '') else ft.Colors.GREEN
             self.hsgt_sub.value = hsgt.get('sub', '--')
             
             # Update Hot Concepts
             hot_concepts = data.get('hot_concepts', [])
             
             # Always show the section title so user knows it exists
             controls_content = []
             if hot_concepts:
                 controls_content = [
                    ft.ResponsiveRow(
                        controls=[
                            self._build_concept_card(c) for c in hot_concepts if c.get('name')
                        ],
                        run_spacing=10,
                    )
                 ]
             else:
                 # Show empty/error state
                 controls_content = [
                     ft.Text(I18n.get("home_hot_concepts_empty") if I18n.get("home_hot_concepts_empty") != "home_hot_concepts_empty" else "暂无热点数据 (请检查网络)", 
                             size=12, color=ft.Colors.GREY)
                 ]

             self.hot_concepts_container.content = ft.Column([
                ft.Text(I18n.get("home_hot_concepts"), size=16, weight=ft.FontWeight.BOLD),
                *controls_content
             ], spacing=10)
             
             if self.page:
                 self.update()
                 
             # Load News
             await self._load_news()

        except Exception as e:
            logger.error(f"Error loading home data: {e}")

    async def _on_load_more_click(self, e):
        """Handle Load More button click"""
        if self.has_more_news:
            self.news_page += 1
            await self._load_news(load_more=True)

    async def _load_news(self, load_more=False):
        try:
            offset = self.news_page * self.PAGE_SIZE
            
            # Load all recent news without strict date filter
            # The sync process already limits to recent items
            news_df = await self.processor.cache.get_market_news(
                limit=self.PAGE_SIZE, 
                offset=offset
            )
            
            if not load_more:
                self.news_list.controls.clear()
            
            if news_df.empty:
               self.has_more_news = False
               if not load_more:
                   self.news_list.controls.append(ft.Text("暂无新闻", color=ft.Colors.GREY))
            else:
               for _, row in news_df.iterrows():
                   self.news_list.controls.append(self._build_news_item(row))
               
               # Check if we likely have more
               if len(news_df) < self.PAGE_SIZE:
                   self.has_more_news = False
            
            # Update button visibility
            self.load_more_btn.visible = self.has_more_news and len(self.news_list.controls) > 0
            if self.load_more_btn.page:
                self.load_more_btn.update()
            
            if self.page:
                self.news_list.update()
                
        except Exception as e:
            logger.error(f"Error loading news: {e}")

    def _build_news_feed(self):
        return ft.Column([
            self.news_list,
            ft.Container(
                content=self.load_more_btn,
                alignment=ft.alignment.center,
                padding=10
            )
        ], scroll=ft.ScrollMode.AUTO, expand=True)

    def _build_news_item(self, row):
        tag = row.get('tags', '')
        # Localize tags
        tag = tag.replace('Stock', '个股').replace('Policy', '政策').replace('Market', '市场').replace('Global', '全球').replace('International', '国际').replace('Macro', '宏观')
        
        content = row.get('content', '')
        time_str = row.get('publish_time', '')
        
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Text(tag, color=ft.Colors.BLUE, weight=ft.FontWeight.BOLD, size=12),
                    ft.Text(time_str[-8:], color=ft.Colors.GREY, size=12) # HH:MM:SS
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


