"""
A股量化选股系统 - 统一主题配置

提供专业金融应用的配色方案和组件样式。
"""
import flet as ft


class AppColors:
    """应用配色常量"""
    
    # 主色调
    PRIMARY = "#1E3A5F"          # 深海蓝 - 专业、信任
    PRIMARY_LIGHT = "#2E5A8F"    # 浅蓝
    PRIMARY_DARK = "#0F2A4F"     # 深蓝
    
    # 强调色
    ACCENT = "#00D4AA"           # 科技绿 - 现代、活力
    ACCENT_LIGHT = "#33DDBB"
    
    # 涨跌色 (A股习惯)
    UP = "#E53935"               # 涨幅红
    DOWN = "#4CAF50"             # 跌幅绿
    RISE = UP                    # 别名 (for K-line chart)
    FALL = DOWN                  # 别名 (for K-line chart)
    
    # 中性色
    BACKGROUND = "#F5F7FA"       # 页面背景
    SURFACE = "#FFFFFF"          # 卡片背景
    BORDER = "#E0E4E8"           # 边框色
    
    # 文字色
    TEXT_PRIMARY = "#1A1A1A"     # 主文字
    TEXT_SECONDARY = "#666666"   # 次要文字
    TEXT_HINT = "#999999"        # 提示文字
    TEXT_ON_PRIMARY = "#FFFFFF"  # 深色背景上的文字
    
    # 状态色
    SUCCESS = "#4CAF50"
    WARNING = "#FF9800"
    ERROR = "#F44336"
    INFO = "#2196F3"


class AppStyles:
    """应用组件样式工厂"""
    
    @staticmethod
    def card(
        padding: int = 20,
        border_radius: int = 12,
        with_shadow: bool = True,
    ) -> dict:
        """卡片容器样式"""
        style = {
            "bgcolor": AppColors.SURFACE,
            "border_radius": border_radius,
            "padding": padding,
            "border": ft.border.all(1, AppColors.BORDER),
        }
        if with_shadow:
            style["shadow"] = ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.08, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            )
        return style

    @staticmethod
    def dashboard_card(padding: int = 20) -> dict:
        """Data dashboard card style"""
        return {
            "bgcolor": AppColors.SURFACE,
            "border_radius": 16,
            "padding": padding,
            "shadow": ft.BoxShadow(
                blur_radius=10,
                color=ft.Colors.with_opacity(0.05, ft.Colors.BLACK),
                offset=ft.Offset(0, 4),
            ),
            "border": ft.border.all(1, ft.Colors.with_opacity(0.5, AppColors.BORDER))
        }
    
    @staticmethod
    def primary_button() -> ft.ButtonStyle:
        """主按钮样式"""
        return ft.ButtonStyle(
            color=AppColors.TEXT_ON_PRIMARY,
            bgcolor=AppColors.PRIMARY,
            padding=ft.padding.symmetric(horizontal=24, vertical=12),
            shape=ft.RoundedRectangleBorder(radius=8),
            alignment=ft.alignment.center,
        )
    
    @staticmethod
    def accent_button() -> ft.ButtonStyle:
        """强调按钮样式"""
        return ft.ButtonStyle(
            color=AppColors.TEXT_ON_PRIMARY,
            bgcolor=AppColors.ACCENT,
            padding=ft.padding.symmetric(horizontal=24, vertical=12),
            shape=ft.RoundedRectangleBorder(radius=8),
            alignment=ft.alignment.center,
        )
    
    @staticmethod
    def outline_button() -> ft.ButtonStyle:
        """描边按钮样式"""
        return ft.ButtonStyle(
            color=AppColors.PRIMARY,
            bgcolor=ft.Colors.TRANSPARENT,
            padding=ft.padding.symmetric(horizontal=24, vertical=12),
            shape=ft.RoundedRectangleBorder(radius=8),
            side=ft.BorderSide(1, AppColors.PRIMARY),
            alignment=ft.alignment.center,
        )
    
    @staticmethod
    def nav_rail_style() -> dict:
        """侧边导航栏样式"""
        return {
            "bgcolor": AppColors.PRIMARY_DARK,
            "indicator_color": AppColors.ACCENT,
            "selected_label_content_color": AppColors.TEXT_ON_PRIMARY,
            "unselected_label_content_color": ft.Colors.with_opacity(0.7, AppColors.TEXT_ON_PRIMARY),
        }
    
    @staticmethod
    def data_table_row(index: int, is_hovered: bool = False) -> str:
        """数据表格行背景色 (斑马纹)"""
        if is_hovered:
            return ft.Colors.with_opacity(0.1, AppColors.PRIMARY)
        return AppColors.SURFACE if index % 2 == 0 else "#F8FAFC"
    
    @staticmethod
    def price_change_color(value: float) -> str:
        """涨跌颜色"""
        if value > 0:
            return AppColors.UP
        elif value < 0:
            return AppColors.DOWN
        return AppColors.TEXT_SECONDARY


def apply_page_theme(page: ft.Page):
    """应用全局页面主题"""
    page.bgcolor = AppColors.BACKGROUND
    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=AppColors.PRIMARY,
            secondary=AppColors.ACCENT,
            surface=AppColors.SURFACE,
            background=AppColors.BACKGROUND,
            error=AppColors.ERROR,
            on_primary=AppColors.TEXT_ON_PRIMARY,
            on_secondary=AppColors.TEXT_ON_PRIMARY,
            on_surface=AppColors.TEXT_PRIMARY,
            on_background=AppColors.TEXT_PRIMARY,
            on_error=AppColors.TEXT_ON_PRIMARY,
        ),
        font_family="Microsoft YaHei",
    )
