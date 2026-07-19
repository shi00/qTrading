from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from data.constants import (
    HEALTH_CHECK_TABLES,
    HEALTH_DEPTH_WARNING_RATIO,
    HEALTH_REPORT_ORDER,
    HEALTH_THRESHOLD_BREADTH,
    HEALTH_THRESHOLD_FINANCIAL_COVERAGE,
    HEALTH_THRESHOLD_FINANCIAL_EXCELLENT,
)
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors
from ui.viewmodels.health_scan_view_model import HealthScanViewModel

logger = logging.getLogger(__name__)

# ==============================================================================
# Sub-Components (模块级纯函数，由旧命令式 class 转换)
# ==============================================================================

# HealthScoreCard 状态映射 (status -> (color, icon, i18n_key))
_HEALTH_STATUS_MAP = {
    "green": (AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE, "health_status_excellent"),
    "yellow": (
        AppColors.WARNING,
        ft.Icons.WARNING_ROUNDED,
        "health_status_warning",
    ),
}
_HEALTH_DEFAULT_STATUS = (
    AppColors.ERROR,
    ft.Icons.ERROR_OUTLINE,
    "health_status_critical",
)


def _make_gradient(color: str) -> ft.LinearGradient:
    """构建状态卡片渐变背景（纯函数）。"""
    return ft.LinearGradient(
        begin=ft.Alignment.TOP_LEFT,
        end=ft.Alignment.BOTTOM_RIGHT,
        colors=[
            ft.Colors.with_opacity(0.2, color),
            ft.Colors.with_opacity(0.05, color),
        ],
    )


def _build_health_score_card(status: str, tables_count: int) -> ft.Container:
    """构建健康状态卡片（L1 视觉层级，纯函数）。

    由旧 ``HealthScoreCard(ft.Container)`` class 转换。
    """
    color, icon, i18n_key = _HEALTH_STATUS_MAP.get(status, _HEALTH_DEFAULT_STATUS)
    text = I18n.get(i18n_key)

    return ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(icon, color=color, size=48),
                ft.Column(
                    controls=[
                        ft.Text(
                            I18n.get("health_report_title"),
                            size=14,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        ft.Text(
                            text,
                            size=24,
                            weight=ft.FontWeight.BOLD,
                            color=color,
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
        ),
        padding=20,
        border_radius=8,
        gradient=_make_gradient(color),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.3, color)),
    )


def _build_metric_tile(
    label: str,
    value: str,
    trend_color: str = AppColors.TEXT_PRIMARY,
    sub_text: str | None = None,
) -> ft.Container:
    """构建单个指标 tile（纯函数）。

    由旧 ``MetricTile(ft.Container)`` class 转换。
    """
    controls: list[ft.Control] = [
        ft.Text(label, size=12, color=AppColors.TEXT_SECONDARY),
        ft.Text(
            str(value),
            size=18,
            weight=ft.FontWeight.BOLD,
            color=trend_color,
        ),
    ]
    if sub_text:
        controls.append(ft.Text(sub_text, size=10, color=AppColors.TEXT_HINT))

    return ft.Container(
        content=ft.Column(
            controls=controls,
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
        padding=15,
        bgcolor=AppColors.SURFACE_VARIANT,
        border_radius=6,
        expand=True,
    )


def _build_key_metrics_grid(market: dict, fundamentals: dict) -> ft.Column:
    """构建关键指标网格（L2 视觉层级，纯函数）。

    由旧 ``KeyMetricsGrid(ft.Column)`` class 转换。
    """
    lag_days = market.get("lag_days", 0)
    gap_count = fundamentals.get("gap_count", 0)
    sanity_errors = fundamentals.get("sanity_errors", 0)
    latest_date = market.get("latest_local", "N/A")

    lag_color = AppColors.ERROR if lag_days > 0 else AppColors.SUCCESS
    gap_color = AppColors.ERROR if gap_count > 0 else AppColors.SUCCESS
    sanity_color = AppColors.ERROR if sanity_errors > 0 else AppColors.SUCCESS

    return ft.Column(
        spacing=10,
        controls=[
            ft.Text(
                I18n.get("health_market_ts"),
                weight=ft.FontWeight.BOLD,
                size=14,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.Row(
                [
                    _build_metric_tile(
                        I18n.get("health_lag_days"),
                        f"{lag_days} {I18n.get('common_suffix_day')}",
                        lag_color,
                    ),
                    _build_metric_tile(I18n.get("health_gap_count"), str(gap_count), gap_color),
                    _build_metric_tile(
                        I18n.get("health_sanity_err"),
                        str(sanity_errors),
                        sanity_color,
                    ),
                ],
            ),
            ft.Row(
                [
                    _build_metric_tile(
                        I18n.get("health_sync_latest"),
                        str(latest_date),
                        AppColors.TEXT_PRIMARY,
                    ),
                ],
            ),
        ],
    )


def _build_section_header(i18n_key: str) -> ft.Container:
    """构建分节标题（纯函数）。"""
    return ft.Container(
        padding=ft.Padding.symmetric(vertical=5),
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


def _build_depth_breadth_items(stats: dict) -> list[ft.Text]:
    """构建可选 Depth/Breadth 指标项（纯函数，仅在非 None 时显示）。"""
    items: list[ft.Text] = []
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


def _create_coverage_row(table_key: str, stats: dict) -> ft.Container:
    """构建单行覆盖详情（纯函数）。

    由旧 ``CoverageDetailTable._create_row`` 实例方法转换。
    """
    key = f"tab_{table_key}"
    name = I18n.get(key)
    if name == key:
        name = HEALTH_CHECK_TABLES.get(table_key, {}).get("desc", table_key)

    ratio = stats.get("ratio", 0)
    fresh_ratio = stats.get("fresh_ratio", 0)
    is_global = stats.get("type") == "global"

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

    name_row = ft.Row(
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
    )

    if is_global:
        # Global: 显示存在数量徽标，不显示覆盖进度条
        cnt = stats.get("covered", 0)
        value_text = (
            I18n.get("health_global_count", count=f"{cnt:,}") if ratio > 0 else I18n.get("health_global_no_data")
        )
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=5),
            content=ft.Row(
                [
                    name_row,
                    ft.Container(
                        content=ft.Text(
                            value_text,
                            size=11,
                            color=icon_color,
                            weight=ft.FontWeight.BOLD,
                        ),
                        bgcolor=ft.Colors.with_opacity(0.1, icon_color),
                        padding=ft.Padding.symmetric(horizontal=10, vertical=3),
                        border_radius=12,
                        expand=True,
                        alignment=ft.Alignment.CENTER,
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

    # Stock: 标准覆盖进度条
    return ft.Container(
        padding=ft.Padding.symmetric(vertical=5),
        content=ft.Row(
            [
                name_row,
                ft.ProgressBar(
                    value=ratio,
                    color=bar_color,
                    bgcolor=AppColors.SURFACE_VARIANT,
                    height=6,
                    expand=True,
                ),
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
                    + _build_depth_breadth_items(stats),
                    spacing=0,
                    alignment=ft.MainAxisAlignment.CENTER,
                    width=70,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
    )


def _build_coverage_detail_table(tables: dict) -> ft.Column:
    """构建覆盖详情表格（L3 视觉层级，纯函数）。

    由旧 ``CoverageDetailTable(ft.Column)`` class 转换。
    按 type 分组（Global / Stock），严格按 HEALTH_REPORT_ORDER 排序。
    """
    controls: list[ft.Control] = []

    global_tables: list[str] = []
    stock_tables: list[str] = []

    sorted_keys = [k for k in HEALTH_REPORT_ORDER if k in tables]
    sorted_keys += [k for k in tables if k not in HEALTH_REPORT_ORDER]

    for k in sorted_keys:
        t_data = tables[k]
        t_type = t_data.get("type", "stock")
        if t_type == "global":
            global_tables.append(k)
        else:
            stock_tables.append(k)

    if global_tables:
        controls.append(_build_section_header("health_section_global"))
        for k in global_tables:
            controls.append(_create_coverage_row(k, tables[k]))

    if stock_tables:
        if global_tables:
            controls.append(ft.Divider(height=20, color=ft.Colors.TRANSPARENT))
        controls.append(_build_section_header("health_section_stock"))
        for k in stock_tables:
            controls.append(_create_coverage_row(k, tables[k]))

    return ft.Column(controls=controls, spacing=10)


# ==============================================================================
# HealthReportDialog (已声明式 V1，Phase 3.2.7 完成，保留)
# ==============================================================================


def _health_dialog_size(page: ft.Page | None) -> tuple[int, int]:
    """基于窗口尺寸计算对话框宽高，加上限约束（纯函数）。"""
    if not page:
        return 600, 600
    win_w = int(page.window.width or 1280)
    win_h = int(page.window.height or 800)
    w = min(max(win_w - 80, 480), 600)
    h = min(max(win_h - 80, 400), 600)
    return w, h


def _log_report_summary(report: dict) -> None:
    """记录报告摘要日志（纯函数，异常不抛出）。"""
    try:
        r_status = report.get("status", "unknown")
        r_tables = len(report.get("fundamentals", {}).get("tables", {}))
        r_lag = report.get("market", {}).get("lag_days", "?")
        logger.info(
            "HealthReportDialog Opened: Status=%s, Tables=%s, Lag=%s",
            r_status,
            r_tables,
            r_lag,
        )
    except Exception as e:
        logger.error("Error logging report summary: %s", e, exc_info=True)


def _build_health_content(report: dict, width: int, height: int) -> ft.Container:
    """构建健康报告详情内容（纯函数）。

    由旧 ``HealthReportDialog._build_content`` 实例方法转换。
    """
    status = report.get("status", "red")
    market = report.get("market", {})
    fundamentals = report.get("fundamentals", {})
    tables = fundamentals.get("tables", {})
    reasons = report.get("reasons", [])

    header = _build_health_score_card(status, len(tables))
    metrics = _build_key_metrics_grid(market, fundamentals)
    coverage = _build_coverage_detail_table(tables)

    issues_section: ft.Control = ft.Container()
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
            margin=ft.Margin.only(bottom=10),
        )

    return ft.Container(
        width=width,
        height=height,
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
                            padding=ft.Padding.only(right=15),
                        ),
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
            ],
            spacing=0,
        ),
    )


@ft.component
def HealthReportDialog(
    report: dict,
    page: ft.Page | None = None,
    open_state: bool = False,
    on_close: Callable[[], None] | None = None,
    on_deep_scan: Callable[[], None] | None = None,
) -> ft.Container:
    """健康报告弹窗（声明式 V1）。

    CLAUDE.md §3.2 MVVM + §3.3 声明式范式 + Phase 3.0.2 spike 模式：
    - ``use_state(open)`` 控制 dialog 显隐，``ft.use_dialog`` 自动挂载/卸载到 page overlay
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 深度扫描通过 ``on_deep_scan`` 回调通知消费方（HealthScanDialog 声明式，Task E.3 重写）
    - 无命令式生命周期回调/手动刷新/``show_dialog``/``pop_dialog``

    Args:
        report: 健康报告字典
        page: ft.Page 引用（用于计算对话框尺寸）
        open_state: 初始打开状态（消费方重新实例化推送，每次为 True）
        on_close: 关闭回调（消费方用于清理引用）
        on_deep_scan: 深度扫描回调（消费方打开 HealthScanDialog）
    """
    ft.use_state(get_observable_state)
    open_, set_open = ft.use_state(open_state)

    width, height = _health_dialog_size(page)

    # 报告摘要日志仅在 open 变为 True 时记录一次（避免每次渲染重复打日志）。
    def _log_effect() -> None:
        if open_:
            _log_report_summary(report)

    ft.use_effect(_log_effect, dependencies=[open_])

    def _close(_e=None) -> None:
        set_open(False)
        if on_close is not None:
            on_close()

    def _deep_scan(_e=None) -> None:
        set_open(False)
        if on_close is not None:
            on_close()
        if on_deep_scan is not None:
            on_deep_scan()

    dialog = (
        ft.AlertDialog(
            content_padding=0,
            modal=True,
            title=ft.Text(I18n.get("health_report_title"), size=16, weight=ft.FontWeight.BOLD),
            title_padding=0,
            content=_build_health_content(report, width, height),
            actions=[
                ft.TextButton(
                    I18n.get("health_btn_deep_scan"),
                    on_click=_deep_scan,
                    style=ft.ButtonStyle(color=AppColors.ACCENT),
                ),
                ft.TextButton(
                    I18n.get("common_close"),
                    on_click=_close,
                    style=ft.ButtonStyle(color=AppColors.PRIMARY),
                ),
            ],
            actions_padding=10,
            shape=ft.RoundedRectangleBorder(radius=8),
        )
        if open_
        else None
    )
    ft.use_dialog(dialog)

    return ft.Container(width=0, height=0)


# ==============================================================================
# HealthScanDialog (声明式 V1，Phase E.3 重写)
# ==============================================================================


def _scan_dialog_size(page: ft.Page | None) -> tuple[int, int]:
    """基于窗口尺寸计算扫描对话框宽高，加上限约束（纯函数）。"""
    if not page:
        return 450, 300
    win_w = int(page.window.width or 1280)
    win_h = int(page.window.height or 800)
    w = min(max(win_w - 80, 360), 450)
    h = min(max(win_h - 80, 240), 300)
    return w, h


def _build_scan_result(result: dict) -> ft.Column:
    """构建扫描结果内容（纯函数，由旧 ``HealthScanDialog.show_results`` 转换）。"""
    score = result.get("score", 0)
    tier = result.get("tier", 1)
    avg_lag = result.get("avg_lag", 99)
    avg_cont = result.get("avg_continuity", 0)

    color = AppColors.SUCCESS if score > 80 else (AppColors.WARNING if score > 50 else AppColors.ERROR)
    avg_fundamental = result.get("avg_fundamental", 0)
    fundamental_color = (
        AppColors.SUCCESS
        if avg_fundamental > 0.7
        else (AppColors.WARNING if avg_fundamental > 0.5 else AppColors.ERROR)
    )
    fin_recency_ok = result.get("fin_recency_ok", False)

    return ft.Column(
        [
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
                                f"{avg_fundamental * 100:.1f}%",
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=fundamental_color,
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
                                "✓" if fin_recency_ok else "✗",
                                size=16,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.SUCCESS if fin_recency_ok else AppColors.ERROR,
                            ),
                        ],
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_AROUND,
            ),
        ],
    )


def _build_scan_content(
    scan_state: str,
    progress: float,
    status_text: str,
    result: dict | None,
    width: int,
    height: int,
    error_key: str | None = None,
) -> ft.Container:
    """构建扫描弹窗内容（纯函数，状态驱动渲染）。

    Args:
        scan_state: 扫描状态 ("idle" | "scanning" | "done" | "error")
        progress: 进度 0.0~1.0
        status_text: 状态文本（i18n 已解析）
        result: 扫描结果字典（scan_state="done" 时非 None）
        width: 对话框宽度
        height: 对话框高度
        error_key: 错误状态 i18n key（scan_state="error" 时使用，默认 "db_err_format"）
    """
    if scan_state == "done" and result is not None:
        return ft.Container(
            width=width,
            height=height,
            content=_build_scan_result(result),
        )

    # 进度阶段：idle / scanning / error
    status_display = I18n.get(error_key or "db_err_format") if scan_state == "error" else status_text
    progress_value: float | None = progress if scan_state == "scanning" else None

    return ft.Container(
        width=width,
        height=height,
        content=ft.Column(
            [
                ft.Container(height=20),
                ft.Text(status_display, size=12, color=AppColors.TEXT_SECONDARY),
                ft.ProgressBar(
                    value=progress_value,
                    width=400,
                    color=AppColors.PRIMARY,
                    bgcolor=AppColors.SURFACE_VARIANT,
                ),
            ],
        ),
    )


@ft.component
def HealthScanDialog(
    data_processor: Any = None,
    page: ft.Page | None = None,
    open_state: bool = False,
    on_close: Callable[[], None] | None = None,
) -> ft.Container:
    """深度健康扫描弹窗（声明式 V1）。

    CLAUDE.md §3.2 MVVM + §3.3 声明式范式：
    - ``use_state(open)`` 控制 dialog 显隐，``ft.use_dialog`` 自动挂载/卸载到 page overlay
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 业务状态（scan_state/progress/status_text/result/error_key）由 ``HealthScanViewModel``
      持有，View 经 ``use_viewmodel`` 消费仅渲染（View = f(ViewModel.state)）
    - 扫描任务通过 ``use_effect(setup, [open_], cleanup=cleanup)`` 启动，
      ``open_=True`` 时调 ``vm.start_scan()``；cleanup 调 ``vm.cancel_pending_futures()``
      取消 pending futures（R2 兼容：CancelledError 在 future.cancel() 内部消化）
    - 跨线程 ``on_progress`` 回调在 VM 内通过 ``asyncio.run_coroutine_threadsafe``
      调度回主 loop 更新 state（R11 loop-local 守卫）
    - 无命令式生命周期回调/手动刷新/``show_dialog``/``pop_dialog``

    Args:
        data_processor: DataProcessor 实例（注入 VM，由 VM 调用 ``run_quality_scan``）
        page: ft.Page 引用（用于计算对话框尺寸）
        open_state: 初始打开状态（消费方重新实例化推送，每次为 True）
        on_close: 关闭回调（消费方用于清理引用）
    """
    # --- i18n 订阅（locale 切换自动重渲染）---
    ft.use_state(get_observable_state)

    # --- dialog 显隐 state（从 prop 初始化）---
    open_, set_open = ft.use_state(open_state)

    # --- ViewModel（业务状态 + command，View 经 use_viewmodel 消费仅渲染）---
    state, vm = use_viewmodel(lambda: HealthScanViewModel(data_processor))

    width, height = _scan_dialog_size(page)

    async def _start_scan_effect() -> None:
        """open_=True 时启动扫描任务（use_effect 触发，转调 VM command）。"""
        if not open_:
            return
        await vm.start_scan()

    def _cleanup_scan() -> None:
        """卸载/open 变化时取消 pending futures（R2 兼容不重新抛出）。"""
        vm.cancel_pending_futures()

    ft.use_effect(_start_scan_effect, dependencies=[open_], cleanup=_cleanup_scan)

    def _close(_e=None) -> None:
        set_open(False)
        if on_close is not None:
            on_close()

    # --- 条件渲染 dialog + use_dialog 自动挂载/卸载 ---
    content = _build_scan_content(
        scan_state=state.scan_state,
        progress=state.progress,
        status_text=state.status_text,
        result=state.result,
        width=width,
        height=height,
        error_key=state.error_key,
    )

    dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("scan_title"), size=16, weight=ft.FontWeight.BOLD),
            content=content,
            actions=[
                ft.TextButton(I18n.get("common_close"), on_click=_close),
            ],
            actions_padding=10,
        )
        if open_
        else None
    )
    ft.use_dialog(dialog)

    # 宿主容器（不可见，仅承载 use_dialog hook）
    return ft.Container(width=0, height=0)
