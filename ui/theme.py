"""
A股量化选股系统 - 统一主题配置

提供专业金融应用的配色方案和组件样式。
"""
from typing import TypedDict

import flet as ft

from ui.i18n import I18n


# ============================================================================
# 类型定义
# ============================================================================

class CardStyle(TypedDict, total=False):
    """卡片容器样式类型定义"""
    bgcolor: str
    border_radius: int
    padding: int
    border: ft.Border
    shadow: ft.BoxShadow


class DashboardCardStyle(TypedDict):
    """仪表盘卡片样式类型定义"""
    bgcolor: str
    border_radius: int
    padding: int
    shadow: ft.BoxShadow
    border: ft.Border


class AppColors:
    """
    应用配色常量。

    本类定义了 A股量化选股系统 的统一色彩规范，确保 UI 的视觉一致性。
    所有颜色值均为 HEX 格式。

    颜色分组：
        - 主色调 (PRIMARY_*): 用于导航栏、按钮、重要标题
        - 强调色 (ACCENT_*): 用于高亮、链接、交互元素
        - 涨跌色 (UP/DOWN): 用于股价、涨跌幅显示（遵循 A股红涨绿跌习惯）
        - 中性色 (BACKGROUND/SURFACE/BORDER): 用于背景、卡片、边框
        - 文字色 (TEXT_*): 用于不同层级的文字内容
        - 状态色 (SUCCESS/WARNING/ERROR/INFO): 用于操作反馈、提示信息
        - 表格专用 (TABLE_*): 用于数据表格的各个组成部分
        - 输入组件 (INPUT_*): 用于表单输入框
    """

    # =========================================================================
    # 主色调 - 用于导航栏、主按钮、标题等核心 UI 元素
    # =========================================================================
    PRIMARY = "#1E3A5F"  # 深海蓝 - 专业、信任
    PRIMARY_LIGHT = "#2E5A8F"  # 浅蓝 - 悬停态、次要按钮
    PRIMARY_DARK = "#0F2A4F"  # 深蓝 - 侧边栏背景、按下态

    # =========================================================================
    # 强调色 - 用于强调元素、链接、图标高亮
    # =========================================================================
    ACCENT = "#00D4AA"  # 科技绿 - 现代、活力
    ACCENT_LIGHT = "#33DDBB"  # 浅绿 - 悬停态

    # =========================================================================
    # 涨跌色 - 遵循 A股市场习惯：红涨绿跌
    # 用于：股价变动、涨跌幅百分比、K线图
    # =========================================================================
    UP = "#E53935"  # 涨幅红
    DOWN = "#4CAF50"  # 跌幅绿
    RISE = UP  # 别名 - K线图上涨
    FALL = DOWN  # 别名 - K线图下跌

    # =========================================================================
    # 中性色 - 用于页面背景、卡片容器、分割线
    # =========================================================================
    BACKGROUND = "#F5F7FA"  # 页面背景 - 柔和灰蓝
    SURFACE = "#FFFFFF"  # 卡片/对话框背景
    BORDER = "#E0E4E8"  # 通用边框色

    # =========================================================================
    # 文字色 - 用于内容文本的不同层级
    # =========================================================================
    TEXT_PRIMARY = "#1A1A1A"  # 主文字 - 标题、重要内容
    TEXT_SECONDARY = "#666666"  # 次要文字 - 描述、说明
    TEXT_HINT = "#999999"  # 提示文字 - 占位符、禁用态
    TEXT_ON_PRIMARY = "#FFFFFF"  # 深色背景上的文字

    # =========================================================================
    # 状态色 - 用于操作反馈、系统提示
    # =========================================================================
    SUCCESS = "#4CAF50"  # 成功 - 完成提示、正向操作
    WARNING = "#FF9800"  # 警告 - 注意事项、风险提示
    ERROR = "#F44336"  # 错误 - 失败提示、阻断性问题
    INFO = "#2196F3"  # 信息 - 中性提示、帮助信息

    # =========================================================================
    # 表格专用色 - 用于 DataTable、股票列表等数据展示组件
    # 使用场景：
    #   - TABLE_HEADER_*: 表头行样式
    #   - TABLE_ROW_*: 数据行斑马纹背景
    #   - TABLE_CELL_*: 单元格文字
    #   - TABLE_GRID_*: 网格线（区分水平/垂直密度）
    # =========================================================================
    TABLE_HEADER_BG = "#1E3A5F"  # 表头背景 - 与 PRIMARY 一致
    TABLE_HEADER_TEXT = "#FFFFFF"  # 表头文字
    TABLE_ROW_ODD = "#FFFFFF"  # 奇数行背景（索引 0, 2, 4...）
    TABLE_ROW_EVEN = "#F7FAFC"  # 偶数行背景（索引 1, 3, 5...）
    TABLE_CELL_TEXT = "#2D3748"  # 普通单元格文字
    TABLE_CELL_NUMERIC = "#1A365D"  # 数值单元格文字 - 深蓝更专业
    TABLE_BORDER = "#E0E4E8"  # 表格外边框
    TABLE_GRID_V = "#E8ECF0"  # 垂直网格线 - 列分隔
    TABLE_GRID_H = "#F0F3F5"  # 水平网格线 - 行分隔（更淡）

    # =========================================================================
    # 输入组件色 - 用于表单输入框、下拉框等
    # =========================================================================
    GRID_LINE = "#F5F5F5"  # 通用网格线 (Grey 100)
    INPUT_BORDER = "#E0E0E0"  # 输入框边框 (Grey 300)


class AppStyles:
    """应用组件样式工厂"""

    @staticmethod
    def card(
            padding: int = 20,
            border_radius: int = 12,
            with_shadow: bool = True,
    ) -> CardStyle:
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
    def dashboard_card(padding: int = 20) -> DashboardCardStyle:
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
            icon_color=AppColors.TEXT_ON_PRIMARY,
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
            icon_color=AppColors.TEXT_ON_PRIMARY,
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
        return AppColors.TABLE_ROW_ODD if index % 2 == 0 else AppColors.TABLE_ROW_EVEN

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
        font_family=I18n.get("font_family"),
        scrollbar_theme=ft.ScrollbarTheme(
            track_visibility=True,
            thumb_visibility=True,
            track_color={
                ft.ControlState.HOVERED: ft.Colors.with_opacity(0.1, AppColors.PRIMARY),
                ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
            },
            thumb_color={
                ft.ControlState.HOVERED: ft.Colors.with_opacity(0.5, AppColors.PRIMARY),
                ft.ControlState.DEFAULT: ft.Colors.with_opacity(0.2, AppColors.PRIMARY),
            },
            thickness=10,
            radius=5,
            interactive=True,
        )
    )
