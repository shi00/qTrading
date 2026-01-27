import flet as ft

class HomeView(ft.Container):
    def __init__(self, page):
        super().__init__()
        # self.page = page # Avoid setting self.page as it is a read-only property on Controls
        self.expand = True
        
        self.content = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                ft.Text("市场概览", size=24, weight=ft.FontWeight.BOLD),
                ft.Divider(),
                # Market Indices - use ResponsiveRow for flexible layout
                ft.ResponsiveRow(
                    [
                        self._build_market_card("上证指数", "3000.00", "+0.5%"),
                        self._build_market_card("深证成指", "9000.00", "-0.2%"),
                        self._build_market_card("创业板指", "1800.00", "+1.2%"),
                        self._build_stat_card("北向资金", "+50亿", "流入"),
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

    def _build_market_card(self, name, value, change):
        color = ft.Colors.GREEN if "-" in change else ft.Colors.RED
        return ft.Container(
            content=ft.Column([
                ft.Text(name, size=14, color=ft.Colors.GREY_600, no_wrap=True),
                ft.Text(value, size=20, weight=ft.FontWeight.BOLD),
                ft.Text(change, size=14, color=color, weight=ft.FontWeight.BOLD),
            ], spacing=5),
            padding=15,
            bgcolor=ft.Colors.WHITE,
            border_radius=10,
            border=ft.border.all(1, ft.Colors.GREY_200),
            col={"xs": 6, "sm": 6, "md": 3, "lg": 3},  # Responsive columns
        )
    
    def _build_stat_card(self, title, value, sub):
        return ft.Container(
            content=ft.Column([
                ft.Text(title, size=14, color=ft.Colors.GREY_600, no_wrap=True),
                ft.Text(value, size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.RED),
                ft.Text(sub, size=12, color=ft.Colors.GREY_500),
            ], spacing=5),
            padding=15,
            bgcolor=ft.Colors.WHITE,
            border_radius=10,
            border=ft.border.all(1, ft.Colors.GREY_200),
            col={"xs": 6, "sm": 6, "md": 3, "lg": 3},  # Responsive columns
        )

    def _build_strategy_card(self, icon, title, desc):
        return ft.Container(
            content=ft.Column([
                ft.Row([
                    ft.Icon(icon, color=ft.Colors.PRIMARY, size=24),
                    ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
                ], spacing=8),
                ft.Text(desc, size=12, color=ft.Colors.GREY_500),
                ft.ElevatedButton("运行", on_click=lambda _: None)
            ], spacing=8),
            padding=20,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border_radius=10,
            col={"xs": 6, "sm": 6, "md": 3, "lg": 3},  # Responsive columns
        )
