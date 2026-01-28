import flet as ft
from data.data_processor import DataProcessor
from ui.theme import AppColors, AppStyles
import logging
import asyncio

logger = logging.getLogger(__name__)

class HomeView(ft.Container):
    def __init__(self):
        super().__init__()
        self.expand = True
        self.processor = DataProcessor()
        
        # UI State Controls
        self.date_label = ft.Text("数据日期: --", size=12, color=ft.Colors.GREY)
        
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
        
        self.content = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                ft.Row([
                    ft.Text("市场概览", size=24, weight=ft.FontWeight.BOLD),
                    ft.Container(expand=True),
                    self.date_label,
                    ft.IconButton(ft.Icons.REFRESH, on_click=self._refresh_data, tooltip="刷新数据")
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(),
                # Market Indices
                ft.ResponsiveRow(
                    [
                        self._build_market_card("上证指数", self.sh_value, self.sh_change),
                        self._build_market_card("深证成指", self.sz_value, self.sz_change),
                        self._build_market_card("创业板指", self.cyb_value, self.cyb_change),
                        self._build_stat_card("北向资金", self.hsgt_value, self.hsgt_sub),
                    ],
                ),
                ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                
                ft.Text("热门策略推荐", size=20, weight=ft.FontWeight.BOLD),
                ft.ResponsiveRow(
                    [
                        self._build_strategy_card(ft.Icons.DIAMOND, "价值投资", "寻找低估值优质蓝筹"),
                        self._build_strategy_card(ft.Icons.ROCKET_LAUNCH, "高成长", "捕捉业绩爆发股"),
                        self._build_strategy_card(ft.Icons.ACCOUNT_BALANCE, "机构龙虎榜", "跟踪主力资金动向"),
                        self._build_strategy_card(ft.Icons.ATTACH_MONEY, "高股息", "稳健收息精选"),
                    ],
                )
            ]
        )

    def did_mount(self):
        # Auto load data when view is attached
        self._refresh_data(None)

    def _refresh_data(self, e):
        # Run async task
        import asyncio
        # We need to run this in the page's async loop if possible, or create a task
        # Since did_mount is sync in standard Control, usually we use page.run_task or create_task
        # But here we are in Flet. 
        # Best practice: use event handler which supports async, or create_task
        if self.page:
             self.page.run_task(self._load_data)

    async def _load_data(self):
        try:
             data = await self.processor.get_market_overview()
             if not data:
                 return
             
             # Update Date
             self.date_label.value = f"数据日期: {data.get('date', '--')}"
             
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
             self.hsgt_value.color = ft.Colors.RED if '流入' in hsgt.get('sub', '') else ft.Colors.GREEN
             self.hsgt_sub.value = hsgt.get('sub', '--')
             
             if self.page:
                 self.update()
             
        except Exception as e:
            logger.error(f"Error loading home data: {e}")

    def _build_market_card(self, name, value_control, change_control):
        return ft.Container(
            content=ft.Column([
                ft.Text(name, size=14, color=AppColors.TEXT_SECONDARY, no_wrap=True),
                value_control,
                change_control,
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
    
    def _build_stat_card(self, title, value_control, sub_control):
        return ft.Container(
            content=ft.Column([
                ft.Text(title, size=14, color=AppColors.TEXT_SECONDARY, no_wrap=True),
                value_control,
                sub_control,
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

    def _build_strategy_card(self, icon, title, desc):
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icon, color=AppColors.PRIMARY, size=24),
                    ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text(desc, size=12, color=AppColors.TEXT_SECONDARY),
                ft.ElevatedButton(
                    text="运行", 
                    on_click=lambda _: None,
                    style=AppStyles.primary_button(),
                )
            ], spacing=10),
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
