import logging
import math
from decimal import Decimal

import flet as ft

from ui.components._markdown_safe import safe_open_url
from ui.components.chart_utils import generate_kline_png
from ui.i18n import I18n
from ui.theme import AppColors

logger = logging.getLogger(__name__)

# Tushare unit conversion constants
TUSHARE_MV_UNIT = 10000  # Tushare returns market value in 万元, convert to 亿
TUSHARE_AMOUNT_UNIT = 100000  # Tushare returns amount in 千元, convert to 亿


def is_valid_number(val) -> bool:
    """Check if val is a valid (non-NaN) number."""
    if val is None:
        return False
    if isinstance(val, (float, Decimal)):
        return not math.isnan(val)
    if isinstance(val, int):
        return True
    try:
        float_val = float(val)
        return not math.isnan(float_val)
    except (TypeError, ValueError):
        return False


def format_mv(val) -> str:
    """Format market value in 亿 (pure function)."""
    if not is_valid_number(val):
        return "-"
    try:
        return f"{float(val) / TUSHARE_MV_UNIT:.1f}{I18n.get('unit_yi')}"
    except (ValueError, TypeError):
        return "-"


def format_vol(val) -> str:
    """Format volume (pure function)."""
    if not is_valid_number(val):
        return "-"
    try:
        v = float(val)
        if v >= 10000:
            return f"{v / 10000:.1f}{I18n.get('unit_wanshou')}"
        return f"{v:.0f}{I18n.get('unit_shou')}"
    except (ValueError, TypeError):
        return "-"


def format_amount(val) -> str:
    """Format amount in 亿 (pure function)."""
    if not is_valid_number(val):
        return "-"
    try:
        return f"{float(val) / TUSHARE_AMOUNT_UNIT:.2f}{I18n.get('unit_yi')}"
    except (ValueError, TypeError):
        return "-"


class StockDetailDialog(ft.AlertDialog):
    """
    Stock detail popup dialog showing comprehensive stock information.
    """

    def __init__(self, stock_data: dict | None = None, data_processor=None, page: ft.Page | None = None):  # type: ignore[untyped]
        self.stock_data = stock_data or {}
        self.data_processor = data_processor
        self._page_ref = page
        self._locale_subscription_id: object | None = None

        # 缓存对话框尺寸（打开时计算一次，不随 resize 变化）
        self._cached_width, self._cached_height = self._dialog_size()
        # K 线图尺寸基于对话框尺寸推算
        self._chart_width = max(self._cached_width - 40, 600)  # 减 padding
        self._chart_height = 340

        super().__init__(
            modal=True,
            title=self._build_title(),
            content=self._build_content(),
            actions=[
                ft.TextButton(I18n.get("common_close"), on_click=self._close),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

    def _dialog_size(self) -> tuple[int, int]:
        """基于窗口尺寸计算对话框宽高，加上限约束。"""
        if not self._page_ref:
            return 900, 700  # 回退默认值
        win_w = int(self._page_ref.window.width or 1280)
        win_h = int(self._page_ref.window.height or 800)
        w = min(max(win_w - 80, 600), 900)
        h = min(max(win_h - 80, 500), 700)
        return w, h

    def _build_title(self):
        code = self.stock_data.get("ts_code", "")
        name = self.stock_data.get("name", "")
        return ft.Row(
            [
                ft.Text(
                    f"{name}",
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Text(f"({code})", size=14, color=AppColors.TEXT_SECONDARY),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_content(self):
        """Build detail content with sections"""

        # Chart placeholder
        self.chart_container = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.ProgressRing(),  # pragma: no cover
                    ft.Text(  # pragma: no cover
                        I18n.get("detail_loading_chart"),  # pragma: no cover
                        size=12,  # pragma: no cover
                        color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
            ),  # pragma: no cover
            height=350,  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
            bgcolor=AppColors.BACKGROUND,  # pragma: no cover
            border=ft.border.all(1, AppColors.BORDER),  # pragma: no cover
            border_radius=8,  # pragma: no cover
        )  # pragma: no cover

        # Price section
        close = self._format_val("close", I18n.get("unit_yuan"))
        pct = self.stock_data.get("pct_chg", 0)
        # Guard against NaN from raw DataFrame data
        pct = float(pct) if is_valid_number(pct) else 0
        pct_color = AppColors.UP if pct > 0 else AppColors.DOWN
        pct_str = f"+{pct:.2f}%" if pct > 0 else f"{pct:.2f}%"

        price_section = ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("detail_sec_price"),  # pragma: no cover
                    size=14,  # pragma: no cover
                    weight=ft.FontWeight.BOLD,  # pragma: no cover
                    color=AppColors.PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Divider(height=5, color=AppColors.DIVIDER),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        self._info_chip(I18n.get("detail_price"), close),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_pct_chg"),  # pragma: no cover
                            pct_str,  # pragma: no cover
                            color=pct_color,  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_turnover"),  # pragma: no cover
                            self._format_val("turnover_rate", "%"),  # pragma: no cover
                        ),  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_vol"),  # pragma: no cover
                            self._format_vol("vol"),  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_amount"),  # pragma: no cover
                            self._format_amount("amount"),  # pragma: no cover
                        ),  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
        )  # pragma: no cover

        # Valuation section
        valuation_section = ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("detail_sec_valuation"),  # pragma: no cover
                    size=14,  # pragma: no cover
                    weight=ft.FontWeight.BOLD,  # pragma: no cover
                    color=AppColors.PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Divider(height=5, color=AppColors.DIVIDER),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_pe"),  # pragma: no cover
                            self._format_val("pe_ttm"),  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(I18n.get("detail_pb"), self._format_val("pb")),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_ps"),  # pragma: no cover
                            self._format_val("ps_ttm"),  # pragma: no cover
                        ),  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_dividend"),  # pragma: no cover
                            self._format_val("dv_ttm", "%"),  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_total_mv"),  # pragma: no cover
                            self._format_mv("total_mv"),  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_circ_mv"),  # pragma: no cover
                            self._format_mv("circ_mv"),  # pragma: no cover
                        ),  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
        )  # pragma: no cover

        # Financial section
        financial_section = ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("detail_sec_financial"),  # pragma: no cover
                    size=14,  # pragma: no cover
                    weight=ft.FontWeight.BOLD,  # pragma: no cover
                    color=AppColors.PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Divider(height=5, color=AppColors.DIVIDER),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_roe"),  # pragma: no cover
                            self._format_val("roe", "%"),  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_gpm"),  # pragma: no cover
                            self._format_val("grossprofit_margin", "%"),  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_debt_ratio"),  # pragma: no cover
                            self._format_val("debt_to_assets", "%"),  # pragma: no cover
                        ),  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_rev_yoy"),  # pragma: no cover
                            self._format_val("or_yoy", "%"),  # pragma: no cover
                        ),  # pragma: no cover
                        self._info_chip(  # pragma: no cover
                            I18n.get("detail_profit_yoy"),  # pragma: no cover
                            self._format_val("netprofit_yoy", "%"),  # pragma: no cover
                        ),  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
        )  # pragma: no cover

        # Basic info section
        basic_section = ft.Column(
            [
                ft.Container(height=10),
                ft.Text(
                    I18n.get("detail_sec_basic"),
                    size=14,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.PRIMARY,
                ),
                ft.Divider(height=5, color=AppColors.DIVIDER),
                ft.Row(
                    [
                        self._info_chip(
                            I18n.get("detail_industry"),
                            str(self.stock_data.get("industry", "-")),
                        ),
                        self._info_chip(
                            I18n.get("detail_list_date"),
                            str(self.stock_data.get("list_date", "-")),
                        ),
                    ],
                ),
            ],
        )

        # AI Analysis Section
        ai_section = ft.Container()
        ai_reason = self.stock_data.get("ai_reason")
        ai_score = self.stock_data.get("ai_score")

        if ai_reason or ai_score:
            try:
                score_val = float(ai_score) if ai_score is not None else 0
            except (ValueError, TypeError):
                score_val = 0

            score_color = (
                AppColors.SUCCESS if score_val >= 80 else (AppColors.WARNING if score_val >= 60 else AppColors.ERROR)
            )

            ai_section = ft.Column(
                [
                    ft.Container(height=10),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.AUTO_AWESOME, color=AppColors.ACCENT),
                            ft.Text(
                                I18n.get("detail_ai_analysis"),
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.ACCENT,
                            ),
                            ft.Container(expand=True),
                            ft.Container(
                                content=ft.Text(
                                    f"{I18n.get('detail_ai_score_prefix')}{score_val}",
                                    color=AppColors.TEXT_ON_PRIMARY,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                bgcolor=score_color,
                                padding=ft.padding.symmetric(horizontal=10, vertical=5),
                                border_radius=12,
                            )
                            if ai_score is not None
                            else ft.Container(),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=10, color=AppColors.DIVIDER),
                    ft.Container(
                        content=ft.Markdown(
                            str(ai_reason) if ai_reason else I18n.get("detail_ai_no_analysis"),
                            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                            selectable=True,
                            on_tap_link=safe_open_url,
                            # Markdown styles need care, but default usually adapts or we can set code_theme?
                            # Flet Markdown inherits default theme colors.
                        ),
                        padding=10,
                        bgcolor=AppColors.SURFACE_VARIANT,
                        border_radius=8,
                        border=ft.border.all(1, AppColors.BORDER),
                    ),
                    # --- AI Thinking Chain ---
                    ft.ExpansionTile(
                        title=ft.Text(
                            I18n.get("detail_ai_thinking"),
                            size=12,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        controls=[
                            ft.Container(
                                content=ft.Markdown(
                                    self.stock_data.get(
                                        "thinking",
                                        I18n.get("detail_ai_no_thinking"),
                                    ),
                                    extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                                    selectable=True,
                                    on_tap_link=safe_open_url,
                                ),
                                padding=10,
                                bgcolor=AppColors.SURFACE_VARIANT,
                                border_radius=8,
                                border=ft.border.all(1, AppColors.BORDER),
                            ),
                        ],
                    )
                    if self.stock_data.get("thinking")
                    else ft.Container(),
                ],
            )

        return ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    self.chart_container,  # pragma: no cover
                    ai_section,  # pragma: no cover
                    price_section,  # pragma: no cover
                    valuation_section,  # pragma: no cover
                    financial_section,  # pragma: no cover
                    basic_section,  # pragma: no cover
                ],  # pragma: no cover
                scroll=ft.ScrollMode.AUTO,  # pragma: no cover
            ),  # pragma: no cover
            width=self._cached_width,  # pragma: no cover
            height=self._cached_height,  # pragma: no cover
        )  # pragma: no cover

    def _info_chip(self, label, value, color=None):  # pragma: no cover
        """Create an info chip with label and value"""  # pragma: no cover
        return ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Text(label, size=11, color=AppColors.TEXT_SECONDARY),  # pragma: no cover
                    ft.Text(  # pragma: no cover
                        str(value),  # pragma: no cover
                        size=14,  # pragma: no cover
                        weight=ft.FontWeight.W_500,  # pragma: no cover
                        color=color or AppColors.TEXT_PRIMARY,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                spacing=2,  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
            ),  # pragma: no cover
            padding=ft.padding.all(8),  # pragma: no cover
            bgcolor=AppColors.SURFACE_VARIANT,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            width=120,  # pragma: no cover
        )  # pragma: no cover

    def _format_val(self, key, suffix=""):
        """Format a value with handling for NaN"""
        val = self.stock_data.get(key)
        if not is_valid_number(val):
            return "-"
        try:
            return f"{float(val):.2f}{suffix}"
        except (ValueError, TypeError):
            return "-"

    def _format_mv(self, key):
        """Format market value in 亿"""
        return format_mv(self.stock_data.get(key))

    def _format_vol(self, key):
        """Format volume"""
        return format_vol(self.stock_data.get(key))

    def _format_amount(self, key):
        """Format amount in 亿"""
        return format_amount(self.stock_data.get(key))

    def _close(self, e):
        self.open = False
        if self.page:
            self.page.update()

    def update_data(self, stock_data: dict):
        """Update the dialog with new stock data"""
        self.stock_data = stock_data
        self.title = self._build_title()
        self.content = self._build_content()

    def did_mount(self):
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def will_unmount(self):
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def refresh_locale(self):
        """Refresh i18n text on locale change (pure UI, preserves loaded chart)."""
        try:
            # 保存已加载的 K 线图（避免重建 content 时丢失）
            old_chart_content = None
            if hasattr(self, "chart_container") and isinstance(self.chart_container.content, ft.Image):
                old_chart_content = self.chart_container.content

            self.title = self._build_title()
            self.content = self._build_content()
            self.actions = [
                ft.TextButton(I18n.get("common_close"), on_click=self._close),
            ]

            # 恢复已加载的 K 线图
            if old_chart_content is not None:
                self.chart_container.content = old_chart_content

            if self.page:
                self.update()
        except Exception as e:
            logger.warning(f"[StockDetailDialog] refresh_locale failed: {e}")

    async def load_chart(self, ts_code: str):
        """Asynchronously load history data and render an inline K-line chart."""
        if not self.data_processor:
            self.chart_container.content = ft.Text(
                I18n.get("detail_err_no_processor"),
                color=AppColors.ERROR,
            )
            self.chart_container.update()
            return

        try:
            # Show loading spinner
            self.chart_container.content = ft.Column(
                [
                    ft.ProgressRing(),
                    ft.Text(
                        I18n.get("detail_loading_history"),
                        size=12,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
            self.chart_container.update()

            # Fetch data (History 365 days)
            df = await self.data_processor.get_stock_history(ts_code, days=365)

            if df.empty:
                self.chart_container.content = ft.Text(
                    I18n.get("detail_no_history"),
                    color=AppColors.TEXT_HINT,
                )
                self.chart_container.update()
                return

            # Ensure volume column exists
            if "vol" not in df.columns:
                df["vol"] = 0

            # Generate inline PNG via mplfinance
            chart_title = f"{self.stock_data.get('name', '')} ({ts_code})"
            from utils.thread_pool import ThreadPoolManager, TaskType

            b64_png = await ThreadPoolManager().run_async(
                TaskType.CPU,
                generate_kline_png,
                df,
                title=chart_title,
                width=self._chart_width,
                height=self._chart_height,
            )

            self.chart_container.content = ft.Image(
                src_base64=b64_png,
                fit=ft.ImageFit.CONTAIN,
                expand=True,
            )
            self.chart_container.update()

        except Exception as e:
            import traceback

            from utils.error_classifier import classify_error, get_error_message

            logger.error(f"Error loading chart: {e}\n{traceback.format_exc()}")
            error_info = classify_error(e, context="chart")
            self.chart_container.content = ft.Text(
                get_error_message(error_info),
                color=AppColors.ERROR,
            )
            self.chart_container.update()
