"""股票详情弹窗（声明式 V1）。

变更要点（Phase 3.2.7）：
- 旧命令式 ``ft.AlertDialog`` 子类 → ``@ft.component def StockDetailDialog(...)``
- ``use_state(open)`` 控制 dialog 显隐，``ft.use_dialog`` 自动挂载/卸载到 page overlay
- i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
- K 线图通过 ``use_effect`` 异步加载，``chart_content`` state 驱动渲染
- 移除命令式生命周期回调、手动 update、``show_dialog``/``pop_dialog``、
  ``refresh_locale``、``update_data``
- 实例方法转为模块级纯函数（``_format_val``/``_info_chip``/``_build_title``/``_build_content``/
  ``_dialog_size``/``_load_chart_async``），可独立单测
- 消费方（ScreenerView）通过重新实例化推送 props（过渡期，Task 3.6.2 ScreenerView
  声明式重写后改为父组件 state 驱动）
"""

import json
import logging
import math
import typing
from collections.abc import Callable
from decimal import Decimal

import flet as ft
import flet_charts as fch

from ui.components._markdown_safe import safe_open_url
from ui.components.chart_utils import generate_kline_chart_data
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles

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


# --- 模块级纯函数（由旧实例方法转换） ---


def _format_val(stock_data: dict, key: str, suffix: str = "") -> str:
    """格式化 stock_data[key]，处理 NaN/非数值（纯函数）。"""
    val = stock_data.get(key)
    if not is_valid_number(val):
        return "-"
    try:
        return f"{float(typing.cast('float | int', val)):.2f}{suffix}"
    except (ValueError, TypeError):
        return "-"


def _format_mv(stock_data: dict, key: str) -> str:
    """格式化市值（万元 → 亿）。"""
    return format_mv(stock_data.get(key))


def _format_vol(stock_data: dict, key: str) -> str:
    """格式化成交量。"""
    return format_vol(stock_data.get(key))


def _format_amount(stock_data: dict, key: str) -> str:
    """格式化成交额（千元 → 亿）。"""
    return format_amount(stock_data.get(key))


def _info_chip(label, value, color=None) -> ft.Container:
    """Create an info chip with label and value."""
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(label, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                ft.Text(
                    str(value),
                    size=AppStyles.FONT_SIZE_LG,
                    weight=ft.FontWeight.W_500,
                    color=color or AppColors.TEXT_PRIMARY,
                ),
            ],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.all(8),
        bgcolor=AppColors.SURFACE_VARIANT,
        border_radius=8,
        width=120,
    )


def _build_prediction_badge(value) -> ft.Container:
    """Task 4.3 (FR-UX-005): 构建 WIN/LOSS 徽章 (模块级纯函数, 可独立单测)."""
    val_str = str(value).upper() if value is not None else ""
    if val_str == "WIN":
        color = AppColors.SUCCESS
        text = I18n.get("prediction_win")
        icon = ft.Icons.CHECK_CIRCLE
    elif val_str == "LOSS":
        color = AppColors.ERROR
        text = I18n.get("prediction_loss")
        icon = ft.Icons.CANCEL
    else:
        color = AppColors.TEXT_HINT
        text = "-"
        icon = ft.Icons.REMOVE
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(icon, color=color, size=AppStyles.FONT_SIZE_TITLE),
                ft.Text(
                    text,
                    color=color,
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    weight=ft.FontWeight.BOLD,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=4,
        ),
        border=ft.Border.all(1, color),
        border_radius=12,
        padding=ft.Padding.symmetric(horizontal=10, vertical=3),
    )


def _format_pct_with_sign(val) -> str:
    """Task 4.3 (FR-UX-005): 带符号格式化百分比数值 (纯函数)."""
    if not is_valid_number(val):
        return "-"
    val_f = float(val)
    sign = "+" if val_f > 0 else ""
    return f"{sign}{val_f:.2f}"


def _has_review_value(v) -> bool:
    """Task 4.3: 检查复盘值是否非空 (None/NaN/空字符串 → False)."""
    if v is None:
        return False
    if isinstance(v, float) and v != v:  # NaN (v != v 是 NaN 标准 idiom)
        return False
    return not (isinstance(v, str) and not v.strip())


def _build_params_snapshot_section(params) -> ft.Control:
    """Task 4.3 (FR-UX-005): 结构化展示 params_snapshot (dict/str/None 三态)."""
    if params is None:
        return ft.Container()
    # 如果是 str, 尝试解析为 dict (JSONB 字段读取后可能是 str)
    if isinstance(params, str):
        if not params.strip():
            return ft.Container()
        try:
            params = json.loads(params)
        except (ValueError, TypeError):
            # 解析失败, 原样展示
            return ft.Container(
                content=ft.Text(
                    params,
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                    selectable=True,
                ),
                padding=10,
                bgcolor=AppColors.SURFACE_VARIANT,
                border_radius=8,
            )
    if not isinstance(params, dict) or not params:
        return ft.Container()
    # key-value 列表
    rows = [
        ft.Row(
            [
                ft.Text(
                    str(k),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Text(
                    str(v),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                    selectable=True,
                    expand=True,
                ),
            ],
            spacing=8,
        )
        for k, v in params.items()
    ]
    return ft.Container(
        content=ft.Column(rows, spacing=4),
        padding=10,
        bgcolor=AppColors.SURFACE_VARIANT,
        border_radius=8,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _build_review_section(stock_data: dict) -> ft.Control:
    """Task 4.3 (FR-UX-005): 复盘区 (徽章 + t1/t5/alpha + params_snapshot).

    无复盘数据时返回空 Container.
    """
    prediction_result = stock_data.get("prediction_result")
    t1_pct = stock_data.get("t1_pct")
    t5_pct = stock_data.get("t5_pct")
    alpha = stock_data.get("alpha")
    params_snapshot = stock_data.get("params_snapshot")

    if not any(_has_review_value(v) for v in [prediction_result, t1_pct, t5_pct, alpha, params_snapshot]):
        return ft.Container()

    return ft.Column(
        [
            ft.Container(height=10),
            ft.Text(
                I18n.get("review_section_title"),
                size=AppStyles.FONT_SIZE_LG,
                weight=ft.FontWeight.BOLD,
                color=AppColors.PRIMARY,
            ),
            ft.Divider(height=5, color=AppColors.DIVIDER),
            ft.Row(
                [
                    _build_prediction_badge(prediction_result),
                    _info_chip(I18n.get("col_t1_pct"), _format_pct_with_sign(t1_pct)),
                    _info_chip(I18n.get("col_t5_pct"), _format_pct_with_sign(t5_pct)),
                    _info_chip(I18n.get("col_alpha"), _format_pct_with_sign(alpha)),
                ],
            ),
            _build_params_snapshot_section(params_snapshot),
        ],
    )


def _build_title(stock_data: dict) -> ft.Row:
    """构建对话框标题（股票名称 + 代码）。"""
    code = stock_data.get("ts_code", "")
    name = stock_data.get("name", "")
    return ft.Row(
        [
            ft.Text(
                f"{name}",
                size=AppStyles.FONT_SIZE_HEADLINE,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.Text(f"({code})", size=AppStyles.FONT_SIZE_LG, color=AppColors.TEXT_SECONDARY),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _initial_chart_content() -> ft.Control:
    """K 线图初始占位（加载中）。"""
    return ft.Column(
        [
            ft.ProgressRing(),
            ft.Text(
                I18n.get("detail_loading_chart"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _dialog_size(page: ft.Page | None) -> tuple[int, int]:
    """基于窗口尺寸计算对话框宽高，加上限约束（纯函数）。"""
    if not page:
        return 900, 700  # 回退默认值
    win_w = int(page.window.width or 1280)
    win_h = int(page.window.height or 800)
    w = min(max(win_w - 80, 600), 900)
    h = min(max(win_h - 80, 500), 700)
    return w, h


def _build_content(
    stock_data: dict,
    chart_content: ft.Control,
    width: int,
    height: int,
) -> ft.Container:  # pragma: no cover
    """构建详情内容（K线图 + AI分析 + 价格 + 估值 + 财务 + 基础信息）。"""
    # Chart container（content 由 chart_content state 驱动）
    chart_container = ft.Container(
        content=chart_content,
        height=350,
        alignment=ft.Alignment.CENTER,
        bgcolor=AppColors.BACKGROUND,
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=8,
    )

    # Price section
    close = _format_val(stock_data, "close", I18n.get("unit_yuan"))
    pct = stock_data.get("pct_chg", 0)
    # Guard against NaN from raw DataFrame data
    pct = float(pct) if is_valid_number(pct) else 0
    pct_color = AppColors.UP_RED if pct > 0 else AppColors.DOWN_GREEN
    pct_str = f"+{pct:.2f}%" if pct > 0 else f"{pct:.2f}%"

    price_section = ft.Column(
        [
            ft.Text(
                I18n.get("detail_sec_price"),
                size=AppStyles.FONT_SIZE_LG,
                weight=ft.FontWeight.BOLD,
                color=AppColors.PRIMARY,
            ),
            ft.Divider(height=5, color=AppColors.DIVIDER),
            ft.Row(
                [
                    _info_chip(I18n.get("detail_price"), close),
                    _info_chip(
                        I18n.get("detail_pct_chg"),
                        pct_str,
                        color=pct_color,
                    ),
                    _info_chip(
                        I18n.get("detail_turnover"),
                        _format_val(stock_data, "turnover_rate", "%"),
                    ),
                ],
            ),
            ft.Row(
                [
                    _info_chip(
                        I18n.get("detail_vol"),
                        _format_vol(stock_data, "vol"),
                    ),
                    _info_chip(
                        I18n.get("detail_amount"),
                        _format_amount(stock_data, "amount"),
                    ),
                ],
            ),
        ],
    )

    # Valuation section
    valuation_section = ft.Column(
        [
            ft.Container(height=10),
            ft.Text(
                I18n.get("detail_sec_valuation"),
                size=AppStyles.FONT_SIZE_LG,
                weight=ft.FontWeight.BOLD,
                color=AppColors.PRIMARY,
            ),
            ft.Divider(height=5, color=AppColors.DIVIDER),
            ft.Row(
                [
                    _info_chip(
                        I18n.get("detail_pe"),
                        _format_val(stock_data, "pe_ttm"),
                    ),
                    _info_chip(I18n.get("detail_pb"), _format_val(stock_data, "pb")),
                    _info_chip(
                        I18n.get("detail_ps"),
                        _format_val(stock_data, "ps_ttm"),
                    ),
                ],
            ),
            ft.Row(
                [
                    _info_chip(
                        I18n.get("detail_dividend"),
                        _format_val(stock_data, "dv_ttm", "%"),
                    ),
                    _info_chip(
                        I18n.get("detail_total_mv"),
                        _format_mv(stock_data, "total_mv"),
                    ),
                    _info_chip(
                        I18n.get("detail_circ_mv"),
                        _format_mv(stock_data, "circ_mv"),
                    ),
                ],
            ),
        ],
    )

    # Financial section
    financial_section = ft.Column(
        [
            ft.Container(height=10),
            ft.Text(
                I18n.get("detail_sec_financial"),
                size=AppStyles.FONT_SIZE_LG,
                weight=ft.FontWeight.BOLD,
                color=AppColors.PRIMARY,
            ),
            ft.Divider(height=5, color=AppColors.DIVIDER),
            ft.Row(
                [
                    _info_chip(
                        I18n.get("detail_roe"),
                        _format_val(stock_data, "roe", "%"),
                    ),
                    _info_chip(
                        I18n.get("detail_gpm"),
                        _format_val(stock_data, "grossprofit_margin", "%"),
                    ),
                    _info_chip(
                        I18n.get("detail_debt_ratio"),
                        _format_val(stock_data, "debt_to_assets", "%"),
                    ),
                ],
            ),
            ft.Row(
                [
                    _info_chip(
                        I18n.get("detail_rev_yoy"),
                        _format_val(stock_data, "or_yoy", "%"),
                    ),
                    _info_chip(
                        I18n.get("detail_profit_yoy"),
                        _format_val(stock_data, "netprofit_yoy", "%"),
                    ),
                ],
            ),
        ],
    )

    # Basic info section
    basic_section = ft.Column(
        [
            ft.Container(height=10),
            ft.Text(
                I18n.get("detail_sec_basic"),
                size=AppStyles.FONT_SIZE_LG,
                weight=ft.FontWeight.BOLD,
                color=AppColors.PRIMARY,
            ),
            ft.Divider(height=5, color=AppColors.DIVIDER),
            ft.Row(
                [
                    _info_chip(
                        I18n.get("detail_industry"),
                        str(stock_data.get("industry", "-")),
                    ),
                    _info_chip(
                        I18n.get("detail_list_date"),
                        str(stock_data.get("list_date", "-")),
                    ),
                ],
            ),
        ],
    )

    # AI Analysis Section
    ai_section: ft.Control = ft.Container()
    ai_reason = stock_data.get("ai_reason")
    ai_score = stock_data.get("ai_score")

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
                            size=AppStyles.FONT_SIZE_TITLE,
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
                            padding=ft.Padding.symmetric(horizontal=10, vertical=5),
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
                    ),
                    padding=10,
                    bgcolor=AppColors.SURFACE_VARIANT,
                    border_radius=8,
                    border=ft.Border.all(1, AppColors.BORDER),
                ),
                # --- AI Thinking Chain ---
                ft.ExpansionTile(
                    title=ft.Text(
                        I18n.get("detail_ai_thinking"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    controls=[
                        ft.Container(
                            content=ft.Markdown(
                                stock_data.get(
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
                            border=ft.Border.all(1, AppColors.BORDER),
                        ),
                    ],
                )
                if stock_data.get("thinking")
                else ft.Container(),
            ],
        )

    return ft.Container(
        content=ft.Column(
            [
                chart_container,
                ai_section,
                price_section,
                valuation_section,
                financial_section,
                basic_section,
                _build_review_section(stock_data),  # Task 4.3 (FR-UX-005)
            ],
            scroll=ft.ScrollMode.AUTO,
        ),
        width=width,
        height=height,
    )


async def _load_chart_async(
    data_processor,
    stock_data: dict,
    ts_code: str,
    set_chart_content: Callable[[ft.Control], None],
) -> None:
    """异步加载 K 线图并通过 set_chart_content 更新状态。

    纯逻辑函数（接收 set_chart_content 回调），可独立单测。
    CancelledError（BaseException）不被 ``except Exception`` 捕获，自动传播（R2）。
    """
    if not data_processor:
        set_chart_content(
            ft.Text(
                I18n.get("detail_err_no_processor"),
                color=AppColors.ERROR,
            )
        )
        return

    try:
        # 显示加载中
        set_chart_content(
            ft.Column(
                [
                    ft.ProgressRing(),
                    ft.Text(
                        I18n.get("detail_loading_history"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

        # 拉取历史数据（365 天）
        df = await data_processor.get_stock_history(ts_code, days=365)

        if df.empty:
            set_chart_content(
                ft.Text(
                    I18n.get("detail_no_history"),
                    color=AppColors.TEXT_HINT,
                )
            )
            return

        # 确保 vol 列存在
        if "vol" not in df.columns:
            df["vol"] = 0

        chart_title = f"{stock_data.get('name', '')} ({ts_code})"

        # 数据转换为 flet-charts 原生数据结构（纯内存操作，无需 ThreadPoolManager）
        chart_data = generate_kline_chart_data(df, title=chart_title)

        # 构建 K 线主图 + 成交量副图
        chart_controls: list[ft.Control] = [
            fch.CandlestickChart(
                spots=chart_data.spots,
                interactive=True,
                min_x=chart_data.min_x,
                max_x=chart_data.max_x,
                min_y=chart_data.min_y,
                max_y=chart_data.max_y,
                bottom_axis=fch.ChartAxis(labels=chart_data.date_labels),
                tooltip=fch.CandlestickChartTooltip(max_width=200),
                expand=2,
            ),
        ]

        # 成交量副图（仅有量数据时显示）
        if chart_data.volume_groups:
            chart_controls.append(
                fch.BarChart(
                    groups=chart_data.volume_groups,
                    interactive=True,
                    min_y=0,
                    max_y=chart_data.max_volume * 1.1,
                    bottom_axis=fch.ChartAxis(labels=chart_data.date_labels),
                    expand=True,
                )
            )

        # P3-15 色盲友好: K 线图例文字标注涨/跌, 不依赖颜色区分
        chart_controls.append(
            ft.Row(
                [
                    ft.Icon(ft.Icons.CIRCLE, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.UP_RED),
                    ft.Text(
                        I18n.get("detail_kline_rise"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    ft.Container(width=AppStyles.SPACING_MD),
                    ft.Icon(ft.Icons.CIRCLE, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.DOWN_GREEN),
                    ft.Text(
                        I18n.get("detail_kline_fall"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=AppStyles.SPACING_XS,
            )
        )

        set_chart_content(
            ft.Column(
                chart_controls,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

    except Exception as e:
        from utils.error_classifier import classify_error, get_error_message

        logger.error("Error loading chart: %s", e, exc_info=True)
        error_info = classify_error(e, context="chart")
        set_chart_content(
            ft.Text(
                get_error_message(error_info),
                color=AppColors.ERROR,
            )
        )


@ft.component
def StockDetailDialog(
    stock_data: dict | None = None,
    data_processor=None,
    page: ft.Page | None = None,
    open_state: bool = False,
    on_close: Callable[[], None] | None = None,
    on_add_to_watchlist: Callable[[str, str], None] | None = None,
) -> ft.Container:
    """股票详情弹窗（声明式 V1）。

    CLAUDE.md §3.2 MVVM + §3.3 声明式范式 + Phase 3.0.2 spike 模式：
    - ``use_state(open)`` 控制 dialog 显隐，``ft.use_dialog`` 自动挂载/卸载到 page overlay
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - K 线图通过 ``use_effect`` 异步加载，``chart_content`` state 驱动渲染
    - 无 ``did_mount``/``will_unmount``/手动 update/``show_dialog``/``pop_dialog``

    Args:
        stock_data: 股票原始数据字典
        data_processor: DataProcessor 实例（用于拉取历史数据）
        page: ft.Page 引用（用于计算对话框尺寸）
        open_state: 初始打开状态（消费方重新实例化推送，每次为 True）
        on_close: 关闭回调（消费方用于清理引用）
        on_add_to_watchlist: 加入关注回调 (ts_code, stock_name); 为 None 时不显示按钮
    """
    # --- i18n 订阅（locale 切换自动重渲染）---
    ft.use_state(get_observable_state)

    # --- dialog 显隐 state（从 prop 初始化）---
    open_, set_open = ft.use_state(open_state)

    # --- K 线图加载状态 ---
    chart_content, set_chart_content = ft.use_state(_initial_chart_content)

    data = stock_data or {}

    # 对话框尺寸
    width, height = _dialog_size(page)

    # --- K 线图异步加载 effect（open 变为 True 时触发）---
    async def _load_chart_effect() -> None:
        if not open_ or not data_processor:
            return
        ts_code = data.get("ts_code", "")
        if not ts_code:
            return
        await _load_chart_async(
            data_processor,
            data,
            ts_code,
            set_chart_content,
        )

    ft.use_effect(_load_chart_effect, dependencies=[open_, data.get("ts_code", "")])

    # --- 关闭处理（state 驱动，非 pop_dialog）---
    def _close(_e) -> None:
        set_open(False)
        if on_close is not None:
            on_close()

    # --- 加入关注处理（FR-UX-004, Task 4.2）---
    def _add_to_watchlist(_e) -> None:
        if on_add_to_watchlist is not None:
            on_add_to_watchlist(data.get("ts_code", ""), data.get("name", ""))

    # --- 条件渲染 dialog + use_dialog 自动挂载/卸载 ---
    actions = [anchored(EIDS.DETAIL_DIALOG.CLOSE_BUTTON, ft.TextButton(I18n.get("common_close"), on_click=_close))]
    if on_add_to_watchlist is not None:
        actions.insert(0, ft.TextButton(I18n.get("watchlist_add"), on_click=_add_to_watchlist))
    dialog = (
        ft.AlertDialog(
            modal=False,
            on_dismiss=_close,
            title=_build_title(data),
            content=_build_content(data, chart_content, width, height),
            actions=actions,
            actions_alignment=ft.MainAxisAlignment.END,
        )
        if open_
        else None
    )
    ft.use_dialog(dialog)

    # 宿主容器（不可见，仅承载 use_dialog hook）
    return ft.Container(width=0, height=0)
