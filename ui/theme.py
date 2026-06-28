"""
A股量化选股系统 - 统一主题配置
A-Share Quantitative Screener - Unified Theme Configuration

双层架构 (Dual-Layer Architecture):
  Layer 1: Flet 语义 Token (ft.Colors.SURFACE 等) - 自动随主题切换
  Layer 2: AppColors 自定义业务色 (涨跌色/表格色) - 需手动更新

支持三主题 (Dark / Light / Navy)
"""

import logging
import threading
from typing import TypedDict

import flet as ft

from ui.i18n import I18n
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


# ============================================================================
# 常量定义
# ============================================================================


class ThemeName:
    DARK = "dark"
    LIGHT = "light"
    NAVY = "navy"
    DRACULA = "dracula"


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


class ThemeColors(TypedDict):
    """主题配色定义 (必须包含所有业务色 key)"""

    UP_RED: str
    DOWN_GREEN: str
    UP: str
    DOWN: str
    SUCCESS: str
    WARNING: str
    INFO: str
    TABLE_HEADER_BG: str
    TABLE_HEADER_TEXT: str
    TABLE_ROW_ODD: str
    TABLE_ROW_EVEN: str
    TABLE_CELL_TEXT: str
    TABLE_CELL_NUMERIC: str
    TABLE_BORDER: str
    TABLE_GRID: str
    INPUT_BG: str
    INPUT_BORDER: str
    INPUT_TEXT: str
    LOG_BG: str
    LOG_TEXT: str


# ============================================================================
# 自定义业务色预设 (仅 Layer 2 的颜色)
# ============================================================================

CUSTOM_COLOR_PRESETS: dict[str, ThemeColors] = {
    ThemeName.DARK: {
        "UP_RED": "#F44336",
        "DOWN_GREEN": "#4CAF50",
        "UP": "#FF3333",
        "DOWN": "#00E676",
        "SUCCESS": "#00E676",
        "WARNING": "#FFAB00",
        "INFO": "#2979FF",
        "TABLE_HEADER_BG": "#252526",
        "TABLE_HEADER_TEXT": "#E0E0E0",
        "TABLE_ROW_ODD": "#1E1E1E",
        "TABLE_ROW_EVEN": "#181818",
        "TABLE_CELL_TEXT": "#CCCCCC",
        "TABLE_CELL_NUMERIC": "#FFFFFF",
        "TABLE_BORDER": "#333333",
        "TABLE_GRID": "#2C2C2C",
        "INPUT_BG": "#2D2D2D",
        "INPUT_BORDER": "#424242",
        "INPUT_TEXT": "#FFFFFF",
        "LOG_BG": "#000000",
        "LOG_TEXT": "#CCCCCC",
    },
    ThemeName.LIGHT: {
        "UP_RED": "#F44336",
        "DOWN_GREEN": "#4CAF50",
        "UP": "#D32F2F",
        "DOWN": "#388E3C",
        "SUCCESS": "#388E3C",
        "WARNING": "#FFA000",
        "INFO": "#1976D2",
        "TABLE_HEADER_BG": "#FAFAFA",
        "TABLE_HEADER_TEXT": "#424242",
        "TABLE_ROW_ODD": "#FFFFFF",
        "TABLE_ROW_EVEN": "#F5F5F5",
        "TABLE_CELL_TEXT": "#424242",
        "TABLE_CELL_NUMERIC": "#212121",
        "TABLE_BORDER": "#EEEEEE",
        "TABLE_GRID": "#EEEEEE",
        "INPUT_BG": "#FFFFFF",
        "INPUT_BORDER": "#BDBDBD",
        "INPUT_TEXT": "#212121",
        "LOG_BG": "#FFFFFF",
        "LOG_TEXT": "#212121",
    },
    ThemeName.NAVY: {
        "UP_RED": "#EF4444",
        "DOWN_GREEN": "#22C55E",
        "UP": "#F87171",
        "DOWN": "#4ADE80",
        "SUCCESS": "#4ADE80",
        "WARNING": "#FBBF24",
        "INFO": "#38BDF8",
        "TABLE_HEADER_BG": "#1E293B",
        "TABLE_HEADER_TEXT": "#E2E8F0",
        "TABLE_ROW_ODD": "#1E293B",
        "TABLE_ROW_EVEN": "#11192E",
        "TABLE_CELL_TEXT": "#CBD5E1",
        "TABLE_CELL_NUMERIC": "#F8FAFC",
        "TABLE_BORDER": "#334155",
        "TABLE_GRID": "#1E293B",
        "INPUT_BG": "#334155",
        "INPUT_BORDER": "#475569",
        "INPUT_TEXT": "#F8FAFC",
        "LOG_BG": "#11192E",
        "LOG_TEXT": "#CBD5E1",
    },
    ThemeName.DRACULA: {
        "UP_RED": "#F44336",
        "DOWN_GREEN": "#4CAF50",
        "UP": "#FF5555",  # Red
        "DOWN": "#50FA7B",  # Green
        "SUCCESS": "#50FA7B",
        "WARNING": "#FFB86C",  # Orange
        "INFO": "#8BE9FD",  # Cyan
        "TABLE_HEADER_BG": "#44475A",  # Current Line
        "TABLE_HEADER_TEXT": "#F8F8F2",
        "TABLE_ROW_ODD": "#282A36",  # Background
        "TABLE_ROW_EVEN": "#44475A",  # Current Line (Alternating)
        "TABLE_CELL_TEXT": "#F8F8F2",
        "TABLE_CELL_NUMERIC": "#BD93F9",  # Purple
        "TABLE_BORDER": "#6272A4",  # Comment (Grey-ish)
        "TABLE_GRID": "#6272A4",
        "INPUT_BG": "#44475A",
        "INPUT_BORDER": "#6272A4",
        "INPUT_TEXT": "#F8F8F2",
        "LOG_BG": "#282A36",
        "LOG_TEXT": "#F8F8F2",
    },
}

# ============================================================================
# ColorScheme 预设 (Layer 1 — Flet 原生主题)
# ============================================================================

THEME_COLOR_SCHEMES = {
    ThemeName.DARK: ft.ColorScheme(
        primary="#2196F3",
        primary_container="#0D47A1",
        secondary="#00BFA5",
        secondary_container="#004D40",
        tertiary="#64B5F6",
        surface="#1E1E1E",
        surface_variant="#2D2D2D",
        background="#121212",
        error="#FF1744",
        error_container="#93000A",
        on_primary="#FFFFFF",
        on_primary_container="#BBDEFB",
        on_secondary="#000000",
        on_secondary_container="#A7FFEB",
        on_surface="#FFFFFF",
        on_surface_variant="#B0B0B0",
        on_background="#FFFFFF",
        on_error="#FFFFFF",
        outline="#333333",
        outline_variant="#2C2C2C",
        inverse_primary="#0D47A1",
        inverse_surface="#FFFFFF",
        on_inverse_surface="#121212",
        shadow="#000000",
        scrim="#000000",
    ),
    ThemeName.LIGHT: ft.ColorScheme(
        primary="#2196F3",
        primary_container="#BBDEFB",
        secondary="#009688",
        secondary_container="#B2DFDB",
        tertiary="#1976D2",
        surface="#FFFFFF",
        surface_variant="#ECEFF1",
        background="#F5F7FA",
        error="#D32F2F",
        error_container="#FFDAD6",
        on_primary="#FFFFFF",
        on_primary_container="#0D47A1",
        on_secondary="#FFFFFF",
        on_secondary_container="#004D40",
        on_surface="#212121",
        on_surface_variant="#757575",
        on_background="#212121",
        on_error="#FFFFFF",
        outline="#E0E0E0",
        outline_variant="#EEEEEE",
        inverse_primary="#BBDEFB",
        inverse_surface="#212121",
        on_inverse_surface="#F5F7FA",
        shadow="#000000",
        scrim="#000000",
    ),
    ThemeName.NAVY: ft.ColorScheme(
        primary="#38BDF8",
        primary_container="#0369A1",
        secondary="#2DD4BF",
        secondary_container="#134E4A",
        tertiary="#7DD3FC",
        surface="#1E293B",
        surface_variant="#334155",
        background="#16213E",
        error="#F87171",
        error_container="#7F1D1D",
        on_primary="#0F172A",
        on_primary_container="#E0F2FE",
        on_secondary="#0F172A",
        on_secondary_container="#CCFBF1",
        on_surface="#F1F5F9",
        on_surface_variant="#94A3B8",
        on_background="#F1F5F9",
        on_error="#0F172A",
        outline="#334155",
        outline_variant="#1E293B",
        inverse_primary="#0369A1",
        inverse_surface="#F1F5F9",
        on_inverse_surface="#0F172A",
        shadow="#000000",
        scrim="#000000",
    ),
    ThemeName.DRACULA: ft.ColorScheme(
        primary="#BD93F9",  # Purple
        primary_container="#44475A",  # Current Line
        secondary="#FF79C6",  # Pink
        secondary_container="#44475A",
        tertiary="#8BE9FD",  # Cyan
        surface="#282A36",  # Background
        surface_variant="#44475A",  # Current Line
        background="#282A36",  # Background
        error="#FF5555",  # Red
        error_container="#44475A",
        on_primary="#282A36",
        on_primary_container="#BD93F9",
        on_secondary="#282A36",
        on_secondary_container="#FF79C6",
        on_surface="#F8F8F2",  # Foreground
        on_surface_variant="#6272A4",  # Comment
        on_background="#F8F8F2",
        on_error="#282A36",
        outline="#6272A4",
        outline_variant="#44475A",
        inverse_primary="#BD93F9",
        inverse_surface="#F8F8F2",
        on_inverse_surface="#282A36",
        shadow="#000000",
        scrim="#000000",
    ),
}

# 主题 → ThemeMode 映射
THEME_MODE_MAP = {
    ThemeName.DARK: ft.ThemeMode.DARK,
    ThemeName.LIGHT: ft.ThemeMode.LIGHT,
    ThemeName.NAVY: ft.ThemeMode.DARK,  # Navy 使用 Dark 模式 + 自定义 ColorScheme
    ThemeName.DRACULA: ft.ThemeMode.DARK,
}


class AppColors:
    """
    双层主题色管理器 (Dual-Layer Theme Color Manager)

    第一层 (Layer 1) - 语义 Token (自动更新):
        使用 ft.Colors.SURFACE 等 Flet 原生 Token。
        组件使用这些 Token 后，切换主题时 Flet 自动重绘，无需手动 update。

    第二层 (Layer 2) - 业务自定义色 (手动更新):
        涨跌色、表格色等 Material Design 没有定义的颜色。
        这些颜色仍然是 Hex 值，切换主题时需要手动更新相关组件。
    """

    # ====================================================================
    # Layer 1: Flet 语义 Token (自动更新 — 无需 update_theme)
    # ====================================================================
    BACKGROUND = "background"  # Use string token to access scheme background
    SURFACE = ft.Colors.SURFACE
    SURFACE_VARIANT = ft.Colors.SURFACE_CONTAINER_HIGHEST  # ft.Colors.SURFACE_VARIANT doesn't exist
    PRIMARY = ft.Colors.PRIMARY
    PRIMARY_DARK = ft.Colors.PRIMARY_CONTAINER
    PRIMARY_LIGHT = ft.Colors.INVERSE_PRIMARY
    ACCENT = ft.Colors.SECONDARY
    ACCENT_HOVER = ft.Colors.SECONDARY_CONTAINER
    TEXT_PRIMARY = ft.Colors.ON_SURFACE
    TEXT_SECONDARY = ft.Colors.ON_SURFACE_VARIANT
    TEXT_HINT = ft.Colors.ON_SURFACE_VARIANT  # 复用次要文本色
    TEXT_ON_PRIMARY = ft.Colors.ON_PRIMARY
    BORDER = ft.Colors.OUTLINE
    DIVIDER = ft.Colors.OUTLINE_VARIANT
    ERROR = ft.Colors.ERROR

    # ====================================================================
    # Layer 2: 业务自定义色 (Hex 值 — 需手动更新)
    # ====================================================================
    UP_RED = "#F44336"
    DOWN_GREEN = "#4CAF50"
    UP = "#FF3333"
    DOWN = "#00E676"
    RISE = UP
    FALL = DOWN
    SUCCESS = "#00E676"
    WARNING = "#FFAB00"
    INFO = "#2979FF"
    TABLE_HEADER_BG = "#252526"
    TABLE_HEADER_TEXT = "#E0E0E0"
    TABLE_ROW_ODD = "#1E1E1E"
    TABLE_ROW_EVEN = "#181818"
    TABLE_CELL_TEXT = "#CCCCCC"
    TABLE_CELL_NUMERIC = "#FFFFFF"
    TABLE_BORDER = "#333333"
    TABLE_GRID = "#2C2C2C"
    TABLE_GRID_V = "#2C2C2C"
    TABLE_GRID_H = "#2C2C2C"
    TABLE_ROW_HOVER = "#333333"
    CARD_BG = "#1E1E1E"
    INPUT_BG = "#2D2D2D"
    INPUT_BORDER = "#424242"
    INPUT_TEXT = "#FFFFFF"
    LOG_BG = "#000000"
    LOG_TEXT = "#CCCCCC"

    # 内部状态
    _CURRENT_THEME_MODE = ft.ThemeMode.DARK
    _CURRENT_THEME_NAME = ThemeName.DARK
    _listeners = []
    _listeners_lock = threading.Lock()  # Thread-safe lock for _listeners list

    @classmethod
    def subscribe(cls, listener):
        """订阅主题变更事件"""
        with cls._listeners_lock:
            if listener not in cls._listeners:
                cls._listeners.append(listener)

    @classmethod
    def unsubscribe(cls, listener):
        """取消订阅"""
        with cls._listeners_lock:
            if listener in cls._listeners:
                cls._listeners.remove(listener)

    @classmethod
    def load_theme(cls, theme_name: str = ThemeName.DARK):
        """
        加载指定主题的自定义色 (Layer 2)。
        Layer 1 的语义 Token 无需加载——它们在 apply_page_theme 中通过 ColorScheme 生效。
        """
        logger.info("Loading theme: %s", theme_name)
        cls._CURRENT_THEME_NAME = theme_name
        cls._CURRENT_THEME_MODE = THEME_MODE_MAP.get(theme_name, ft.ThemeMode.DARK)

        # 加载自定义色
        # 使用 TypedDict key 确保完整性 (防止主题缺键导致旧值残留)
        preset = CUSTOM_COLOR_PRESETS.get(
            theme_name,
            CUSTOM_COLOR_PRESETS[ThemeName.DARK],
        )

        # 强制遍历 ThemeColors 的所有字段，而不是 preset 的 keys
        # 这样如果 preset 缺字段， IDE 会首先报错 (mypy)，运行时 getattr 会报错或我们应该提供默认值
        # 但由于 preset 是 ThemeColors 类型，我们假设它是完整的。
        # 为了运行时安全，我们遍历 preset.items() 并确保覆盖所有 AppColors 对应属性

        for key, value in preset.items():
            if hasattr(cls, key):
                setattr(cls, key, value)

        # 别名同步
        cls.RISE = cls.UP
        cls.FALL = cls.DOWN
        cls.TABLE_GRID_V = cls.TABLE_GRID
        cls.TABLE_GRID_H = cls.TABLE_GRID

        # 通知监听器 (复制列表避免迭代期间修改)
        logger.debug("Notifying %s theme listeners", len(cls._listeners))
        with cls._listeners_lock:
            listeners_snapshot = list(cls._listeners)
        for listener in listeners_snapshot:
            try:
                listener()
            except Exception as e:
                logger.error("Error notifying theme listener: %s", e, exc_info=True)


class AppStyles:
    """应用组件样式工厂 — 全部使用语义 Token"""

    # --- Size Tokens (统一控件宽度，消除魔术数字) ---
    CONTROL_WIDTH_XS = 80  # 超小型控件：短标签、小按钮
    CONTROL_WIDTH_SM = 120  # 小型控件：数字输入、线程数、连接池
    CONTROL_WIDTH_MD = 200  # 中型控件：Dropdown 下拉框、单行选择
    CONTROL_WIDTH_LG = 400  # 大型控件：Token/URL 输入框

    # --- Spacing Tokens (统一间距，消除魔术数字) ---
    SPACING_XS = 4
    SPACING_SM = 8
    SPACING_MD = 12
    SPACING_LG = 16
    SPACING_XL = 20

    # --- Responsive Column Configs (标准栅格配置，消除各视图重复硬编码 col={...}) ---
    COL_FULL = {"xs": 12}
    COL_HALF = {"xs": 12, "sm": 6}
    COL_THIRD = {"xs": 12, "sm": 6, "md": 4}
    COL_QUARTER = {"xs": 6, "sm": 4, "md": 3, "lg": 2}
    COL_TWO_THIRDS = {"xs": 12, "sm": 6, "md": 8}

    @staticmethod
    def card(
        padding: int = 15,
        border_radius: int = 4,
        with_shadow: bool = False,
        with_border: bool = True,
    ) -> CardStyle:
        """标准卡片容器"""
        style: CardStyle = {
            "bgcolor": ft.Colors.SURFACE,
            "border_radius": border_radius,
            "padding": padding,
        }
        if with_border:
            style["border"] = ft.border.all(1, ft.Colors.OUTLINE)

        if with_shadow:
            style["shadow"] = ft.BoxShadow(
                spread_radius=0,
                blur_radius=4,
                color=ft.Colors.with_opacity(0.15, ft.Colors.SHADOW),
                offset=ft.Offset(0, 2),
            )
        return style

    @staticmethod
    def dashboard_card(padding: int = 15) -> DashboardCardStyle:
        """仪表盘卡片"""
        return {
            "bgcolor": ft.Colors.SURFACE,
            "border_radius": 4,
            "padding": padding,
            "border": ft.border.all(1, ft.Colors.OUTLINE),
            "shadow": ft.BoxShadow(
                spread_radius=0,
                blur_radius=10,
                color=ft.Colors.with_opacity(0.1, ft.Colors.SHADOW),
                offset=ft.Offset(0, 4),
            ),
        }

    @staticmethod
    def primary_button() -> ft.ButtonStyle:
        return ft.ButtonStyle(
            color=ft.Colors.ON_PRIMARY,
            icon_color=ft.Colors.ON_PRIMARY,
            bgcolor=ft.Colors.PRIMARY,
            padding=ft.padding.symmetric(horizontal=20, vertical=16),
            shape=ft.RoundedRectangleBorder(radius=2),
            elevation=0,
            text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
        )

    @staticmethod
    def secondary_button() -> ft.ButtonStyle:
        return ft.ButtonStyle(
            color=ft.Colors.PRIMARY,
            bgcolor=ft.Colors.TRANSPARENT,
            padding=ft.padding.symmetric(horizontal=20, vertical=16),
            shape=ft.RoundedRectangleBorder(radius=2),
            side=ft.BorderSide(1, ft.Colors.PRIMARY),
            elevation=0,
        )

    @staticmethod
    def accent_button() -> ft.ButtonStyle:
        return ft.ButtonStyle(
            color=ft.Colors.ON_SECONDARY,
            bgcolor=ft.Colors.SECONDARY,
            padding=ft.padding.symmetric(horizontal=20, vertical=16),
            shape=ft.RoundedRectangleBorder(radius=2),
            elevation=0,
            text_style=ft.TextStyle(weight=ft.FontWeight.BOLD),
        )

    @staticmethod
    def outline_button() -> ft.ButtonStyle:
        return ft.ButtonStyle(
            color=ft.Colors.PRIMARY,
            bgcolor=ft.Colors.TRANSPARENT,
            padding=ft.padding.symmetric(horizontal=20, vertical=16),
            shape=ft.RoundedRectangleBorder(radius=2),
            side=ft.BorderSide(1, ft.Colors.PRIMARY),
            overlay_color=ft.Colors.with_opacity(0.1, ft.Colors.PRIMARY),
        )

    @staticmethod
    def data_table_row(index: int, is_hovered: bool = False) -> str:
        """表格行颜色 (Layer 2 — 自定义色)"""
        if is_hovered:
            return AppColors.TABLE_ROW_ODD  # Hover uses slightly different shade
        return AppColors.TABLE_ROW_ODD if index % 2 == 0 else AppColors.TABLE_ROW_EVEN

    @staticmethod
    def price_change_color(value: float) -> str:
        """涨跌颜色 (Layer 2 — 自定义色)"""
        if value > 0:
            return AppColors.UP
        if value < 0:
            return AppColors.DOWN
        return ft.Colors.ON_SURFACE_VARIANT


def _build_theme(theme_name: str) -> ft.Theme:
    """
    根据主题名构建 ft.Theme 对象。
    包含 ColorScheme + 全局组件样式（滚动条、分割线、数据表）。
    """
    color_scheme = THEME_COLOR_SCHEMES.get(
        theme_name,
        THEME_COLOR_SCHEMES[ThemeName.DARK],
    )
    custom = CUSTOM_COLOR_PRESETS.get(theme_name, CUSTOM_COLOR_PRESETS[ThemeName.DARK])

    return ft.Theme(
        color_scheme=color_scheme,
        font_family=I18n.get("font_family"),
        scrollbar_theme=ft.ScrollbarTheme(
            track_visibility=False,
            thumb_visibility=True,
            thumb_color={
                ft.ControlState.HOVERED: ft.Colors.with_opacity(
                    0.4,
                    ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.ControlState.DEFAULT: ft.Colors.with_opacity(
                    0.2,
                    ft.Colors.ON_SURFACE_VARIANT,
                ),
            },
            thickness=6,
            radius=3,
            interactive=True,
        ),
        divider_theme=ft.DividerTheme(color=ft.Colors.OUTLINE_VARIANT, thickness=1),
        data_table_theme=ft.DataTableTheme(
            heading_row_color=custom["TABLE_HEADER_BG"],
            data_row_color={
                ft.ControlState.HOVERED: AppColors.SURFACE_VARIANT,
            },
            heading_text_style=ft.TextStyle(
                weight=ft.FontWeight.BOLD,
                color=custom["TABLE_HEADER_TEXT"],
            ),
            data_text_style=ft.TextStyle(
                color=custom["TABLE_CELL_TEXT"],
                font_family="Roboto Mono, Consolas, monospace",
            ),
            horizontal_margin=10,
            column_spacing=20,
            divider_thickness=0,
        ),
    )


def apply_page_theme(page: ft.Page, theme_name: str | None = None):  # type: ignore[untyped]
    """
    应用全局页面主题。

    此方法是主题切换的唯一入口点。它会：
    1. 加载自定义色 (Layer 2)
    2. 构建并设置 page.theme / page.dark_theme (Layer 1)
    3. 设置 page.theme_mode
    """
    if theme_name is None:
        theme_name = ConfigHandler.get_theme_name()

    # Step 1: 加载自定义色
    AppColors.load_theme(theme_name)

    # Step 2: 构建主题
    target_mode = THEME_MODE_MAP.get(theme_name, ft.ThemeMode.DARK)

    if target_mode == ft.ThemeMode.LIGHT:
        # 如果目标是亮色模式 (light)，设置 page.theme
        page.theme = _build_theme(theme_name)
        page.dark_theme = _build_theme(ThemeName.DARK)
    else:
        # 如果目标是暗色模式 (dark, navy, dracula)，设置 page.dark_theme
        # page.theme 保持默认 light，以便切换
        page.theme = _build_theme(ThemeName.LIGHT)
        page.dark_theme = _build_theme(theme_name)

    # Step 3: 设置模式
    page.theme_mode = target_mode
    page.bgcolor = None  # 让 Flet 根据 ColorScheme.background 自动设置


# ============================================================================
# 策略参数分组常量 (Strategy Parameter Grouping Constants)
# ============================================================================

PARAM_GROUP_ORDER = [
    "core_signal",
    "volume_confirm",
    "fundamental",
    "risk_control",
    "default",
    "advanced",
]

DEFAULT_GROUP_LABELS = {
    "core_signal": "🎯 核心触发信号",
    "volume_confirm": "📊 量价资金确认",
    "fundamental": "🏢 基本面滤网",
    "risk_control": "🛑 严格风控红线",
    "default": "🎛️ 基础设置",
    "advanced": "⚙️ 高级调优",
}
