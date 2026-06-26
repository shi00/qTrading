from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import flet as ft

from data.constants import (
    HEALTH_CHECK_TABLES,
    HEALTH_DEPTH_WARNING_RATIO,
    HEALTH_REPORT_ORDER,
    HEALTH_THRESHOLD_BREADTH,
    HEALTH_THRESHOLD_FINANCIAL_COVERAGE,
    HEALTH_THRESHOLD_FINANCIAL_EXCELLENT,
)
from ui.i18n import I18n
from ui.theme import AppColors

if TYPE_CHECKING:
    from data.data_processor import DataProcessor

logger = logging.getLogger(__name__)

# ==============================================================================
# Sub-Components
# ==============================================================================


class HealthScoreCard(ft.Container):
    """
    Shows the overall health score and status banner.
    L1 Visual Hierarchy: Big visual impact.
    """

    # Status -> (color, icon, i18n_key) mapping
    _STATUS_MAP = {
        "green": (AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE, "health_status_excellent"),
        "yellow": (
            AppColors.WARNING,
            ft.Icons.WARNING_ROUNDED,
            "health_status_warning",
        ),
    }
    _DEFAULT_STATUS = (
        AppColors.ERROR,
        ft.Icons.ERROR_OUTLINE,
        "health_status_critical",
    )

    @staticmethod
    def _make_gradient(color: str) -> ft.LinearGradient:  # pragma: no cover
        return ft.LinearGradient(
            begin=ft.alignment.top_left,
            end=ft.alignment.bottom_right,
            colors=[
                ft.Colors.with_opacity(0.2, color),
                ft.Colors.with_opacity(0.05, color),
            ],
        )

    def __init__(self, status: str, tables_count: int):  # pragma: no cover
        super().__init__()

        self.color, self.icon, i18n_key = self._STATUS_MAP.get(
            status,
            self._DEFAULT_STATUS,
        )
        self.text = I18n.get(i18n_key)
        self.bg_gradient = self._make_gradient(self.color)

        self.content = ft.Row(
            controls=[
                ft.Icon(self.icon, color=self.color, size=48),
                ft.Column(
                    controls=[
                        ft.Text(
                            I18n.get("health_report_title"),
                            size=14,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        ft.Text(
                            self.text,
                            size=24,
                            weight=ft.FontWeight.BOLD,
                            color=self.color,
                        ),
                    ],
                    spacing=2,
                ),
                ft.Container(expand=True),
                ft.Column(
                    controls=[
                        ft.Text(
                            I18n.get("health_checked_count").format(count=tables_count),
                            size=12,
                            color=AppColors.TEXT_HINT,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )
        self.padding = 20
        self.border_radius = 8
        self.gradient = self.bg_gradient
        self.border = ft.border.all(1, ft.Colors.with_opacity(0.3, self.color))


class MetricTile(ft.Container):
    """
    A single metric tile for the grid.
    """

    def __init__(  # pragma: no cover
        self,
        label: str,
        value: str,
        trend_color: str = AppColors.TEXT_PRIMARY,
        sub_text: str | None = None,  # type: ignore[untyped]
    ):
        super().__init__()
        self.content = ft.Column(
            controls=[
                ft.Text(label, size=12, color=AppColors.TEXT_SECONDARY),
                ft.Text(
                    str(value),
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=trend_color,
                ),
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
        )
        if sub_text:
            self.content.controls.append(
                ft.Text(sub_text, size=10, color=AppColors.TEXT_HINT),
            )

        self.padding = 15
        self.bgcolor = AppColors.SURFACE_VARIANT
        self.border_radius = 6
        self.expand = True


class KeyMetricsGrid(ft.Column):
    """
    L2 Visual Hierarchy: Key indicators (Lag, Gaps).
    """

    def __init__(self, market: dict, fundamentals: dict):
        super().__init__()

        # Parse Data
        lag_days = market.get("lag_days", 0)
        gap_count = fundamentals.get("gap_count", 0)
        sanity_errors = fundamentals.get("sanity_errors", 0)
        latest_date = market.get("latest_local", "N/A")

        # Colors
        lag_color = AppColors.ERROR if lag_days > 0 else AppColors.SUCCESS
        gap_color = AppColors.ERROR if gap_count > 0 else AppColors.SUCCESS
        sanity_color = AppColors.ERROR if sanity_errors > 0 else AppColors.SUCCESS

        self.spacing = 10
        self.controls = [
            ft.Text(
                I18n.get("health_market_ts"),
                weight=ft.FontWeight.BOLD,
                size=14,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.Row(
                [
                    MetricTile(
                        I18n.get("health_lag_days"),
                        f"{lag_days} {I18n.get('common_suffix_day')}",
                        lag_color,
                    ),
                    MetricTile(I18n.get("health_gap_count"), str(gap_count), gap_color),
                    MetricTile(
                        I18n.get("health_sanity_err"),
                        str(sanity_errors),
                        sanity_color,
                    ),
                ],
            ),
            ft.Row(
                [
                    MetricTile(
                        I18n.get("health_sync_latest"),
                        str(latest_date),
                        AppColors.TEXT_PRIMARY,
                    ),
                ],
            ),
        ]


class CoverageDetailTable(ft.Column):
    """
    L3 Visual Hierarchy: Detailed list, grouped by type (Global vs Stock).
    """

    def __init__(self, tables: dict):
        controls = []

        # Split tables into groups based on 'type' field
        global_tables = []
        stock_tables = []

        # Strict ordering based on constants
        sorted_keys = [k for k in HEALTH_REPORT_ORDER if k in tables]
        # Append any extras
        sorted_keys += [k for k in tables if k not in HEALTH_REPORT_ORDER]

        for k in sorted_keys:
            t_data = tables[k]
            t_type = t_data.get("type", "stock")
            if t_type == "global":
                global_tables.append(k)
            else:
                stock_tables.append(k)

        # 1. Global Section
        if global_tables:
            controls.append(self._build_section_header("health_section_global"))
            for k in global_tables:
                controls.append(self._create_row(k, tables[k]))

        # 2. Stock Section
        if stock_tables:
            if global_tables:
                controls.append(ft.Divider(height=20, color=ft.Colors.TRANSPARENT))
            controls.append(self._build_section_header("health_section_stock"))
            for k in stock_tables:
                controls.append(self._create_row(k, tables[k]))

        super().__init__(controls=controls, spacing=10)

    def _build_section_header(self, i18n_key):  # pragma: no cover
        return ft.Container(
            padding=ft.padding.symmetric(vertical=5),
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.SUBTITLES, size=16, color=AppColors.PRIMARY),
                    ft.Text(
                        I18n.get(i18n_key),
                        weight=ft.FontWeight.BOLD,
                        color=AppColors.PRIMARY,
                    ),
                ],
                spacing=5,
            ),
        )

    def _create_row(self, table_key, stats):
        # Get display name
        key = f"tab_{table_key}"
        name = I18n.get(key)
        if name == key:
            name = HEALTH_CHECK_TABLES.get(table_key, {}).get("desc", table_key)

        ratio = stats.get("ratio", 0)
        fresh_ratio = stats.get("fresh_ratio", 0)
        is_global = stats.get("type") == "global"

        # Color Logic
        if ratio >= HEALTH_THRESHOLD_FINANCIAL_EXCELLENT:
            bar_color = AppColors.SUCCESS
            status_icon = ft.Icons.CHECK_CIRCLE_OUTLINE
            icon_color = AppColors.SUCCESS
        elif ratio >= HEALTH_THRESHOLD_FINANCIAL_COVERAGE:
            bar_color = AppColors.WARNING
            status_icon = ft.Icons.INFO_OUTLINE
            icon_color = AppColors.WARNING
        else:
            bar_color = AppColors.ERROR
            status_icon = ft.Icons.HIGHLIGHT_OFF
            icon_color = AppColors.ERROR

        # Adaptive display for Global vs Stock
        if is_global:
            # Global: Show presence status badge instead of coverage bar
            cnt = stats.get("covered", 0)
            value_text = (
                I18n.get("health_global_count", count=f"{cnt:,}") if ratio > 0 else I18n.get("health_global_no_data")
            )
            return ft.Container(
                padding=ft.padding.symmetric(vertical=5),
                content=ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Icon(status_icon, size=14, color=icon_color),
                                ft.Text(
                                    name,
                                    width=120,
                                    size=12,
                                    weight=ft.FontWeight.BOLD,
                                    color=AppColors.TEXT_PRIMARY,
                                    no_wrap=True,
                                ),
                            ],
                            spacing=5,
                            width=140,
                        ),
                        ft.Container(
                            content=ft.Text(
                                value_text,
                                size=11,
                                color=icon_color,
                                weight=ft.FontWeight.BOLD,
                            ),
                            bgcolor=ft.Colors.with_opacity(0.1, icon_color),
                            padding=ft.padding.symmetric(horizontal=10, vertical=3),
                            border_radius=12,
                            expand=True,
                            alignment=ft.alignment.center,
                        ),
                        ft.Container(width=10),
                        ft.Text(
                            "✓" if ratio > 0 else "✗",
                            size=16,
                            weight=ft.FontWeight.BOLD,
                            color=icon_color,
                            width=60,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            )

        # Stock: Standard coverage bar
        return ft.Container(
            padding=ft.padding.symmetric(vertical=5),
            content=ft.Row(
                [
                    # Name & Icon
                    ft.Row(
                        [
                            ft.Icon(status_icon, size=14, color=icon_color),
                            ft.Text(
                                name,
                                width=120,
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.TEXT_PRIMARY,
                                no_wrap=True,
                            ),
                        ],
                        spacing=5,
                        width=140,
                    ),
                    # Progress Bar
                    ft.ProgressBar(
                        value=ratio,
                        color=bar_color,
                        bgcolor=AppColors.SURFACE_VARIANT,
                        height=6,
                        expand=True,
                    ),
                    # Values
                    ft.Container(width=10),
                    ft.Column(
                        [
                            ft.Text(
                                f"{ratio * 100:.1f}%",
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.TEXT_PRIMARY,
                            ),
                            ft.Text(
                                I18n.get(
                                    "health_freshness",
                                    ratio=f"{fresh_ratio * 100:.0f}%",
                                ),
                                size=10,
                                color=AppColors.TEXT_HINT,
                            ),
                        ]
                        + self._build_depth_breadth_items(stats),
                        spacing=0,
                        alignment=ft.MainAxisAlignment.CENTER,
                        width=70,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _build_depth_breadth_items(self, stats):  # pragma: no cover
        """Build optional Depth/Breadth indicator items (only shown when not None)."""
        items = []
        depth_ratio = stats.get("depth_ratio")
        breadth_ratio = stats.get("breadth_ratio")
        if depth_ratio is not None:
            items.append(
                ft.Text(
                    I18n.get("health_depth", ratio=f"{depth_ratio * 100:.0f}%"),
                    size=10,
                    color=AppColors.WARNING if depth_ratio < HEALTH_DEPTH_WARNING_RATIO else AppColors.TEXT_HINT,
                ),
            )
        if breadth_ratio is not None:
            items.append(
                ft.Text(
                    I18n.get("health_breadth", ratio=f"{breadth_ratio * 100:.0f}%"),
                    size=10,
                    color=AppColors.WARNING if breadth_ratio < HEALTH_THRESHOLD_BREADTH else AppColors.TEXT_HINT,
                ),
            )
        return items


# ==============================================================================
# Main Dialog
# ==============================================================================


class HealthReportDialog(ft.AlertDialog):
    def __init__(self, page, report, on_dismiss=None):
        self.page_ref = page
        self.report = report
        self._locale_subscription_id: object | None = None

        # 缓存对话框尺寸（打开时计算一次，不随 resize 变化）
        self._cached_width, self._cached_height = self._dialog_size()

        # LOG REPORT SUMMARY FOR DEBUGGING
        try:
            r_status = report.get("status", "unknown")
            r_tables = len(report.get("fundamentals", {}).get("tables", {}))
            r_lag = report.get("market", {}).get("lag_days", "?")
            logger.info(
                f"HealthReportDialog Opened: Status={r_status}, Tables={r_tables}, Lag={r_lag}",
            )
        except Exception as e:
            logger.error(f"Error logging report summary: {e}")

        self.on_dismiss_callback = on_dismiss

        super().__init__(
            content_padding=0,
            modal=True,
            title=ft.Container(),
            title_padding=0,
            content=self._build_content(),
            actions=[
                ft.TextButton(
                    I18n.get("health_btn_deep_scan"),
                    on_click=self.run_deep_scan,
                    style=ft.ButtonStyle(color=AppColors.ACCENT),
                ),
                ft.TextButton(
                    I18n.get("common_close"),
                    on_click=self.close_dialog,
                    style=ft.ButtonStyle(color=AppColors.PRIMARY),
                ),
            ],
            actions_padding=10,
            shape=ft.RoundedRectangleBorder(radius=8),
        )

    def _dialog_size(self) -> tuple[int, int]:
        """基于窗口尺寸计算对话框宽高，加上限约束。"""
        if not self.page_ref:
            return 600, 600
        win_w = int(self.page_ref.window.width or 1280)
        win_h = int(self.page_ref.window.height or 800)
        w = min(max(win_w - 80, 480), 600)
        h = min(max(win_h - 80, 400), 600)
        return w, h

    def close_dialog(self, e=None):  # pragma: no cover
        try:
            if hasattr(self.page_ref, "close"):
                self.page_ref.close(self)
            else:
                self.open = False
                if self.page_ref:
                    self.page_ref.update()
        except Exception as ex:
            logger.error(f"Error closing dialog: {ex}")

        if self.on_dismiss_callback:
            self.on_dismiss_callback()

    def did_mount(self):
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def will_unmount(self):
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def refresh_locale(self):
        """Refresh i18n text on locale change (pure UI rebuild)."""
        try:
            self.content = self._build_content()
            self.actions = [
                ft.TextButton(
                    I18n.get("health_btn_deep_scan"),
                    on_click=self.run_deep_scan,
                    style=ft.ButtonStyle(color=AppColors.ACCENT),
                ),
                ft.TextButton(
                    I18n.get("common_close"),
                    on_click=self.close_dialog,
                    style=ft.ButtonStyle(color=AppColors.PRIMARY),
                ),
            ]
            if self.page:
                self.update()
        except Exception as e:
            logger.warning(f"[HealthReportDialog] refresh_locale failed: {e}")

    def _build_content(self):
        # Extract Data
        status = self.report.get("status", "red")
        market = self.report.get("market", {})
        fundamentals = self.report.get("fundamentals", {})
        tables = fundamentals.get("tables", {})
        reasons = self.report.get("reasons", [])

        # Components
        header = HealthScoreCard(status, len(tables))
        metrics = KeyMetricsGrid(market, fundamentals)
        coverage = CoverageDetailTable(tables)

        # Issues Section (if any)
        issues_section = ft.Container()
        if reasons:
            issues_list = [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.WARNING_AMBER, color=AppColors.ERROR, size=14),
                        ft.Text(r, size=12, color=AppColors.ERROR),
                    ],
                )
                for r in reasons
            ]
            issues_section = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            I18n.get("common_reason"),
                            weight=ft.FontWeight.BOLD,
                            size=12,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                        *issues_list,
                    ],
                    spacing=5,
                ),
                padding=10,
                bgcolor=ft.Colors.with_opacity(0.1, AppColors.ERROR),
                border_radius=4,
                margin=ft.margin.only(bottom=10),
            )

        # Assemble
        return ft.Container(
            width=self._cached_width,
            height=self._cached_height,
            padding=20,
            content=ft.Column(
                controls=[
                    header,
                    ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                    issues_section,
                    metrics,
                    ft.Divider(height=20, color=AppColors.DIVIDER),
                    ft.Column(
                        [
                            ft.Container(
                                content=coverage,
                                padding=ft.padding.only(right=15),
                            ),
                        ],
                        scroll=ft.ScrollMode.AUTO,
                        expand=True,
                    ),  # Scrollable list
                ],
                spacing=0,
            ),
        )

    async def run_deep_scan(self, e):
        """Open Deep Scan Dialog"""
        from data.data_processor import DataProcessor

        self.close_dialog(e)

        dialog = HealthScanDialog(self.page_ref, DataProcessor())
        if hasattr(self.page_ref, "open"):
            self.page_ref.open(dialog)
        else:
            self.page_ref.dialog = dialog
            dialog.open = True
            self.page_ref.update()

        # Run backend scan without blocking UI thread indefinitely.
        await dialog.start_scan()


class HealthScanDialog(ft.AlertDialog):
    """
    Dialog for Deep Health Scan (Tier 2/3).
    Shows progress then results.
    """

    def __init__(self, page, data_processor: DataProcessor):  # pragma: no cover
        self.page_ref = page
        self._data_processor = data_processor
        self._locale_subscription_id: object | None = None
        self._last_result: dict | None = None
        # 缓存对话框尺寸
        self._cached_width, self._cached_height = self._dialog_size()
        self.progress_bar = ft.ProgressBar(
            width=400,
            color=AppColors.PRIMARY,
            bgcolor=AppColors.SURFACE_VARIANT,
        )
        self.status_text = ft.Text(
            I18n.get("scan_step_init"),
            size=12,
            color=AppColors.TEXT_SECONDARY,
        )
        self.result_content = ft.Column(visible=False)
        self._title_text = ft.Text(I18n.get("scan_title"), size=16, weight=ft.FontWeight.BOLD)
        self._close_btn = ft.TextButton(I18n.get("common_close"), on_click=self.close_dialog)

        super().__init__(
            modal=True,
            title=self._title_text,
            content=ft.Container(
                width=self._cached_width,
                height=self._cached_height,
                content=ft.Column(
                    [
                        ft.Container(height=20),
                        self.status_text,
                        self.progress_bar,
                        self.result_content,
                    ],
                ),
            ),
            actions=[self._close_btn],
            actions_padding=10,
        )

    def _dialog_size(self) -> tuple[int, int]:
        """基于窗口尺寸计算对话框宽高，加上限约束。"""
        if not self.page_ref:
            return 450, 300
        win_w = int(self.page_ref.window.width or 1280)
        win_h = int(self.page_ref.window.height or 800)
        w = min(max(win_w - 80, 360), 450)
        h = min(max(win_h - 80, 240), 300)
        return w, h

    def close_dialog(self, e=None):  # pragma: no cover
        if hasattr(self.page_ref, "close"):
            self.page_ref.close(self)
        else:
            self.open = False
            self.page_ref.update()

    def did_mount(self):  # pragma: no cover
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def will_unmount(self):  # pragma: no cover
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def refresh_locale(self):
        """Refresh i18n text on locale change (pure UI)."""
        try:
            self._title_text.value = I18n.get("scan_title")
            self._close_btn.text = I18n.get("common_close")
            if self.status_text.visible:
                self.status_text.value = I18n.get("scan_step_init")
            # 结果区域可见时，用缓存的扫描结果重建以刷新所有 i18n 文案
            if self._last_result is not None and self.result_content.visible:
                self.show_results(self._last_result)
            if self.page:
                self.update()
        except Exception as e:
            logger.warning(f"[HealthScanDialog] refresh_locale failed: {e}")

    async def start_scan(self):
        """Start async scan"""
        loop = asyncio.get_running_loop()

        try:

            def on_progress(current, total, msg):
                # Schedule UI update on main event loop instead of direct cross-thread call
                asyncio.run_coroutine_threadsafe(self._update_progress(current, total, msg), loop)

            result = await self._data_processor.run_quality_scan(
                sample_size=50,
                progress_callback=on_progress,
            )
            self.show_results(result)
        except Exception:
            self.status_text.value = I18n.get("db_err_format")
            self.page_ref.update()

    async def _update_progress(self, current, total, msg):
        """Update progress UI on the main event loop (thread-safe)."""
        self.progress_bar.value = current / total
        self.status_text.value = msg
        self.page_ref.update()

    def show_results(self, result):  # pragma: no cover
        """Display results."""
        self._last_result = result
        score = result.get("score", 0)
        tier = result.get("tier", 1)
        avg_lag = result.get("avg_lag", 99)
        avg_cont = result.get("avg_continuity", 0)

        color = AppColors.SUCCESS if score > 80 else (AppColors.WARNING if score > 50 else AppColors.ERROR)

        self.progress_bar.visible = False
        self.status_text.visible = False

        self.result_content.controls = [
            ft.Container(height=20),
            ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color=color, size=40),
                    ft.Column(
                        [
                            ft.Text(
                                f"{I18n.get('health_score_title')}: {score}",
                                size=20,
                                weight=ft.FontWeight.BOLD,
                                color=color,
                            ),
                            ft.Text(
                                f"{I18n.get('quality_tier_' + str(tier))}",
                                size=14,
                                color=AppColors.TEXT_PRIMARY,
                            ),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Divider(height=20),
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                I18n.get("health_continuity"),
                                size=12,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                f"{avg_cont * 100:.1f}%",
                                size=16,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ],
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                I18n.get("health_avg_recency"),
                                size=12,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                f"{avg_lag:.1f} {I18n.get('health_days')}",
                                size=16,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ],
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                I18n.get("health_sample_size"),
                                size=12,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                f"{result.get('sample_size', 0)}",
                                size=16,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_AROUND,
            ),
            ft.Divider(height=10),
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(
                                I18n.get("health_fundamental_completeness"),
                                size=12,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                f"{result.get('avg_fundamental', 0) * 100:.1f}%",
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.SUCCESS
                                if result.get("avg_fundamental", 0) > 0.7
                                else (AppColors.WARNING if result.get("avg_fundamental", 0) > 0.5 else AppColors.ERROR),
                            ),
                        ],
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                I18n.get("health_fin_recency"),
                                size=12,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                "✓" if result.get("fin_recency_ok", False) else "✗",
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.SUCCESS if result.get("fin_recency_ok", False) else AppColors.ERROR,
                            ),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_AROUND,
            ),
        ]
        self.result_content.visible = True
        self.page_ref.update()
