"""screener_view — 声明式组件 (Phase F.3).

从命令式容器子类重写为 ``@ft.component def ScreenerView(...) -> ft.Container``
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 命令式 ``class ScreenerView(ft.Container)`` → ``@ft.component def ScreenerView(...)``
- VM 通过 ``use_viewmodel(factory=lambda: ScreenerViewModel())`` 内部模式消费
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- FilePicker 通过 ``use_ref`` + ``use_effect`` 注册到 ``page.services``, cleanup 时移除
- PubSub (TaskManager) 通过 ``use_effect(setup, [], cleanup=cleanup)`` 订阅/退订
- LLM 流式 Markdown 卡片从 ``state.stream_cards`` 渲染 (VM 侧节流 flush, state-driven)
- page 访问用 ``ft.context.page`` (try/except 守卫 RuntimeError)
- 移除全部命令式生命周期/主题/locale/resize/page_ref/占位字典 API (改用 state 驱动)
- 消费声明式 ResizableSplitter/PaginatedTable/StockDetailDialog (函数调用, props 推送)
"""

import asyncio
import datetime
import logging
import math
import os
import typing

import flet as ft
import pandas as pd

from data.domain_services import AiAttributionRow, SampleGrade, StrategyStatRow
from ui.components._markdown_safe import safe_open_url
from ui.components.flet_type_helpers import (
    get_control_attr,
    get_control_value,
    safe_controls,
    safe_on_change,
    safe_on_click,
    safe_on_select,
)
from ui.components.confirm_dialog import ConfirmDialog
from ui.components.resizable_splitter import ResizableSplitter
from ui.components.risk_disclaimer import build_risk_disclaimer
from ui.components.slider_input import SliderInput
from ui.components.state_views import EmptyState
from ui.components.stock_detail_dialog import StockDetailDialog
from ui.components.toast_manager import open_export_folder
from ui.components.unit_format import column_header_unit, format_metadata_cell
from ui.components.virtual_table import PaginatedTable
from ui.hooks import use_viewmodel
from ui.i18n import I18n, translate_strategy_name, get_observable_state
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.theme import AppColors, AppStyles
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.viewmodels import Message
from ui.viewmodels.screener_view_model import (
    _MAX_LOG_CARDS,
    HistoryTreeRow,
    ScreenerState,
    ScreenerRow,
    ScreenerViewModel,
    StrategyDepRow,
    StreamCard,
)
from ui.viewmodels.backtest_view_model import set_pending_prefill
from ui.viewmodels.news_insight_view_model import NewsInsightViewModel
from ui.viewmodels.watchlist_view_model import WatchlistViewModel
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

# R.2.6.3: VM 产出语义键 (error/warning/success/info), View 映射为 AppColors 实际颜色值 (§3.2 VM 不感知 UI 颜色).
# F04: 映射保存 AppColors 语义属性名 (非导入期 Hex 快照), 渲染时经 _resolve_status_color 取当期主题色.
_STATUS_COLOR_MAP = {
    "error": "ERROR",
    "warning": "WARNING",
    "success": "SUCCESS",
    "info": "INFO",
}


def _resolve_status_color(status_color: str) -> str:
    """解析 VM 语义状态色到当期主题色 (F04: 渲染时读取 AppColors, 不固化导入期快照)."""
    name = _STATUS_COLOR_MAP.get(status_color, "TEXT_SECONDARY")
    return getattr(AppColors, name)


# UX-03 (单位单一数据源): 策略参数定义的 ``unit`` 字段 (策略层声明) → 本层 i18n key。
# View 统一据此拼接参数阈值 label 的单位后缀，杜绝策略文案与代码换算单位漂移；
# 值域须与 strategies/utils.py 的 threshold_in_data_unit 单位约定保持一致。
_UNIT_SUFFIX_I18N_KEYS = {
    "yi_cny": "unit_yi",
    "wan_cny": "unit_wan",
}

_HIDDEN_COLS = frozenset(
    {
        "symbol",
        "id",
        "list_status",
        "list_date",
        "trade_date",
        "ann_date",
        "open",
        "high",
        "low",
        "pre_close",
        "change",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "circ_mv",
        "float_share",
        "free_share",
        "total_share",
        "area",
        "market",
        "thinking",
        "review_status",
        "created_at",
        "is_st",  # SC-01: 内部过滤列（排除后恒为 False），不进结果表
        "is_delisting",  # G2: 内部过滤列（排除后恒为 False），不进结果表
        "t1_price",
        "t5_price",
        "params_snapshot",
        "_filter_attribution",  # UX-04: 结构化筛选归因列 (仅详情弹窗展示, 不上表格)
        # D4-M3: 学习上下文注入样本元数据（样本 ID / 学习开关状态），随结果落库到
        # params_snapshot；仅作可追溯用途，不上结果表格。
        "learning_context_meta",
        # CRITICAL-02 (R21/BT-03): screening_history 落库的执行期可信度元数据
        # （exec_warnings 经 warnings 横幅呈现；filter_attribution 供历史回看消费），不上表格。
        "exec_warnings",
        "filter_attribution",
    }
)

_COLUMN_WIDTHS = {
    "ts_code": 100,
    "name": 120,
    "ai_score": 80,
    "ai_reason": 250,
    "confidence": 70,
    "industry": 120,
    "industry_sw_l2": 140,
    "industry_tushare": 110,
    "strategy_name": 120,
    "prediction_result": 80,
    "conclusion_label": 100,
    "t1_pct": 80,
    "t5_pct": 80,
    "alpha": 80,
}

_DATE_COLS = frozenset({"list_date", "trade_date"})

# MAJOR-01: 模型结论枚举（conclusion_label）→ i18n key 的呈现映射。
# reject 呈现为「模型否决」，使被整体丢弃的定性结论在结果卡片中显式可见。
_CONCLUSION_LABEL_KEYS = {
    "strong_buy": "ai_conclusion_strong_buy",
    "watchlist": "ai_conclusion_watchlist",
    "uncertain": "ai_conclusion_uncertain",
    "reject": "ai_conclusion_reject",
}

# MINOR-09 item 3: PaginatedTable 列宽持久化键 (经 VM 读写 ConfigHandler, 对齐 splitter 模式)
_VT_COL_WIDTHS_KEY = "ui_vt_screener_col_widths"


def _render_status_message(msg: Message | None) -> str:
    """渲染状态消息, 翻译 ``*_key`` 后缀 params 为当前 locale (§3.2 VM 不感知 locale).

    VM 通过 params 传递 i18n key (如 ``name_key=strategy.name_key``),
    View 渲染时翻译为当前 locale 字符串并替换原 ``*_key`` 字段,
    避免 VM 持有翻译字符串导致 locale 切换后 state 残留旧 locale 翻译.
    """
    if msg is None:
        return ""
    params = dict(msg.params)
    for k in list(params):
        if k.endswith("_key") and isinstance(params[k], str):
            # R.3 D5-6: 翻译 *_key 指向的模板时, 注入当前已平铺的非 *_key 参数
            # (如 ai_progress_concurrent_info 的 concurrency), 使模板内占位符
            # ({concurrency}) 可填充, 避免字面量残留. 排除其它 *_key 原始 key,
            # 避免把未翻译的 key 字符串当成占位符值混入模板; 多余 kwargs 会被忽略.
            inject = {kk: vv for kk, vv in params.items() if not kk.endswith("_key")}
            params[k[:-4]] = I18n.get(params[k], **inject)
            del params[k]
    return I18n.get(msg.key, **params)


def _build_screener_warning_banner(warnings: tuple[Message, ...]) -> ft.Control | None:
    """D3-4: 结果区上方的策略业务警告横幅。

    渲染 ``ScreenerState.warnings``（策略执行期业务警告，如 VolumeBreakout 参数自动调整）。
    与状态栏并存，逐条按当前 locale 翻译 (View 感知 locale)；空时返回 None 不渲染空块。
    与 ``_render_status_message`` 相同的翻译契约 (VM 只产 i18n key)。
    """
    if not warnings:
        return None
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.ERROR_OUTLINE, color=AppColors.WARNING, size=AppStyles.FONT_SIZE_TITLE),
                        ft.Text(
                            I18n.get(msg.key, **dict(msg.params)),
                            color=AppColors.WARNING,
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            no_wrap=False,
                            expand=True,
                        ),
                    ],
                    spacing=8,
                )
                for msg in warnings
            ],
            spacing=6,
        ),
        padding=AppStyles.SPACING_MD,
        border_radius=8,
        bgcolor=AppColors.SURFACE_VARIANT,
    )


def _format_cell_value(col: str, val) -> str:
    if pd.isna(val):
        return "-"
    if col == "strategy_name":
        return translate_strategy_name(str(val)) or str(val)
    if col == "prediction_result":  # Task 4.3 (FR-UX-005): WIN/LOSS 文本映射
        val_str = str(val).upper()
        if val_str == "WIN":
            return I18n.get("prediction_win")
        if val_str == "LOSS":
            return I18n.get("prediction_loss")
        return "-"
    if col == "conclusion_label":  # MAJOR-01: 模型结论枚举 → i18n 呈现（含「模型否决」）
        # 纯呈现映射（枚举 → i18n key），不在 UI 层做业务判断。
        label_key = _CONCLUSION_LABEL_KEYS.get(str(val).lower().strip())
        return I18n.get(label_key) if label_key else "-"
    if col in _DATE_COLS:
        if isinstance(val, (datetime.date, datetime.datetime)):
            return val.strftime("%Y-%m-%d")
        val_str = str(val).split(".")[0]
        if len(val_str) == 8 and val_str.isdigit():
            return f"{val_str[:4]}-{val_str[4:6]}-{val_str[6:]}"
        return str(val)
    # CRITICAL-02: 布尔 / 单位换算 / 百分比列统一经「列 → 原始单位 → 展示单位」
    # 元数据（ui/components/unit_format.py，与详情框共用同一单一数据源，杜绝两套口径）。
    meta = format_metadata_cell(col, val)
    if meta is not None:
        return meta
    if isinstance(val, (float, int)) and col not in ("ts_code", "symbol"):
        if isinstance(val, float):
            return f"{val:.2f}"
    return str(val)


def _build_table_data(current_page_rows: tuple[ScreenerRow, ...], vm: ScreenerViewModel) -> tuple[list, list]:
    vt_columns = []
    visible_cols = []
    # C2b: 列序来自首行 values 的 key (df column 顺序), 空态无行则无列 (EmptyState 兜底)
    columns = tuple(current_page_rows[0].values.keys()) if current_page_rows else ()
    for col in columns:
        if col in _HIDDEN_COLS:
            continue
        visible_cols.append(col)
        width = _COLUMN_WIDTHS.get(col, 80)
        label = vm.get_column_alias("screening_history", col)
        # CRITICAL-02: 有单位的列在表头标注展示单位（与单元格换算同源，避免口径漂移）。
        unit = column_header_unit(col)
        if unit is not None:
            label = f"{label}{I18n.get('col_header_unit_suffix', unit=unit)}"
        vt_columns.append({"id": col, "label": label, "width": width})

    formatted_rows = _format_rows(current_page_rows, visible_cols)
    return vt_columns, formatted_rows


def _format_rows(
    rows: tuple[ScreenerRow, ...] | list[ScreenerRow],
    visible_cols: list[str],
) -> list[dict[str, typing.Any]]:
    """按可见列格式化行 (D7-3 抽出复用: 主表与三分区共用同一格式规则)."""
    formatted: list[dict[str, typing.Any]] = []
    for row in rows:
        raw = row.values
        fmt: dict[str, typing.Any] = {col: _format_cell_value(col, raw[col]) for col in visible_cols}
        fmt["_raw"] = raw  # #423: 携带原始行引用, 供 _on_row_click 反查 (替代 ts_code 字典反查, 避免同名多行覆盖)
        formatted.append(fmt)
    return formatted


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _show_toast(
    page: ft.Page,
    msg: str,
    msg_type: str = "info",
    action_text: str | None = None,
    on_action: typing.Callable[[], None] | None = None,
) -> None:
    """显式调用页面统一 Toast 组件 (``page.toast`` 由 application.py 挂载)。

    P2-10: action_text/on_action 透传 (导出成功"打开文件夹"按钮)。
    """
    page.toast.show(  # type: ignore[attr-defined]  # [reason: page.toast 由 application.py 动态挂载, ft.Page 存根未声明]
        msg,
        msg_type,
        action_text=action_text,
        on_action=on_action,
    )


def _build_strategy_options(strategies_with_dep: tuple[StrategyDepRow, ...]) -> list[ft.dropdown.Option]:
    """构建策略下拉框选项 (D10: 行对象含 name_key, 渲染时按当前 locale 翻译 + missing_apis 标记).

    VM 不感知 locale (§3.2): name_key 为 raw i18n key, 组件已订阅 locale, 切换自动重渲染.
    """
    options = []
    for row in strategies_with_dep:
        name = I18n.get(row.name_key)
        if row.supports_ai:
            name = f"{name} [{I18n.get('strategy_ai_badge')}]"  # AI-05: 支持 AI 的策略加徽章
        if row.missing_apis:
            name = f"{name} (!)"  # P2-7: 警告 emoji 改为文本符号, 避免 UI 依赖 emoji 字体
        options.append(ft.dropdown.Option(row.key, name))
    return options


def _build_page_size_options() -> list[ft.dropdown.Option]:
    """构建每页大小下拉框选项。"""
    per_page = I18n.get("screener_per_page")
    return [ft.dropdown.Option(k, text=f"{k} {per_page}") for k in ("10", "20", "50", "100")]


def _resolve_group_title(group_name: str, label_key: str | None = None) -> str:
    """Resolve group title with priority: label_key > DEFAULT_GROUP_LABELS[group_name] > group_name.

    DEFAULT_GROUP_LABELS 为 group_name→i18n_key 映射表（CLAUDE.md §3.2 i18n 状态驱动），
    View 经 I18n.get(key) 渲染，不感知 locale。
    """
    from ui.theme import DEFAULT_GROUP_LABELS

    if label_key:
        return I18n.get(label_key)
    i18n_key = DEFAULT_GROUP_LABELS.get(group_name)
    if i18n_key:
        return I18n.get(i18n_key)
    return group_name


def _resolve_strategy_desc_color(color_key: str) -> str:
    """映射策略描述颜色语义标识符到 AppColors (R.2.6.2: VM 不感知 UI 颜色, §3.2).

    VM 通过 state.strategy_desc_color 产出语义标识符 ("default"/"warning"),
    View 渲染时映射为 AppColors 实际颜色值.
    """
    if color_key == "warning":
        return AppColors.WARNING
    return AppColors.TEXT_PRIMARY


def _render_strategy_desc(msg: Message | None) -> str:
    """渲染策略描述 Message 为当前 locale 字符串 (P3-ScreenerVM-I18n-Get-Residual, §3.2).

    VM 通过 ``state.strategy_desc`` 产出 ``Message`` (desc_key + params), View 渲染时
    翻译为当前 locale 字符串. 当 params 含 ``missing_apis`` 字段时, 追加
    ``strategy_missing_apis`` 翻译后缀 (locale 切换后自动重新翻译).
    """
    if msg is None:
        return ""
    params = dict(msg.params)
    missing_apis = params.pop("missing_apis", None)
    text = I18n.get(msg.key, **params)
    if missing_apis:
        text += f" ({I18n.get('strategy_missing_apis')}: {missing_apis})"
    return text


# =============================================================================
# D15: ScreenerView 子组件提取 — 纯函数接收 props, 无闭包/状态, 可独立测试
# -----------------------------------------------------------------------------
# 从 ScreenerView 巨型组件中拆出三个独立渲染单元 (报告 04 D15):
#   - build_stream_card    流式/AI 占位卡 (state.stream_cards 逐卡渲染)
#   - build_params_panel   策略参数面板 (含 build_param_control 单控件)
#   - build_history_tree   历史树侧栏 (state.history_tree 派生)
# 仅作机械搬移 + props 化, 不引入新抽象层 (宪法 §1.2 禁推测性设计).
# =============================================================================


def build_stream_card(card: StreamCard, on_retry: typing.Callable[[str], None]) -> ft.Container:
    """构建单张流式/AI 占位卡 (D15: 从 ScreenerView._build_log_card 提取, props 化)."""
    name = card.name
    # UX-2.3: 错误状态分支（含重试按钮）
    if card.error:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(name, weight=ft.FontWeight.W_600, size=AppStyles.FONT_SIZE_TITLE),
                            ft.Icon(ft.Icons.ERROR_OUTLINE, color=AppColors.ERROR, size=AppStyles.FONT_SIZE_TITLE),
                        ],
                        spacing=8,
                    ),
                    ft.Text(
                        I18n.get(card.error, default=card.error) if card.error else "",
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.ERROR,
                        no_wrap=False,
                    ),
                    ft.TextButton(
                        icon=ft.Icons.REFRESH,
                        content=I18n.get("ai_card_retry"),
                        tooltip=I18n.get("ai_card_retry"),
                        on_click=safe_on_click(lambda e, n=name: on_retry(n)),
                    ),
                ],
                spacing=8,
            ),
            border=ft.Border.all(1, AppColors.ERROR),
            border_radius=8,
            padding=AppStyles.SPACING_LG,
            bgcolor=AppColors.SURFACE,
            margin=ft.Margin.only(bottom=10),
        )
    if card.is_analyzing:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(name, weight=ft.FontWeight.W_600, size=AppStyles.FONT_SIZE_TITLE),
                            ft.ProgressRing(width=14, height=14, stroke_width=2),
                        ],
                        spacing=8,
                    ),
                    ft.Container(
                        content=ft.Markdown(
                            I18n.get("ai_card_analyzing"),
                            selectable=True,
                            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                            on_tap_link=safe_open_url,
                        ),
                        padding=ft.Padding.only(left=5, right=5),
                    ),
                ],
                spacing=8,
            ),
            border=ft.Border.all(1, AppColors.DIVIDER),
            border_radius=8,
            padding=AppStyles.SPACING_LG,
            bgcolor=AppColors.SURFACE,
            margin=ft.Margin.only(bottom=10),
        )

    reasoning = card.reasoning
    content = card.content
    reasoning_visible = bool(reasoning)
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(name, weight=ft.FontWeight.W_600, size=AppStyles.FONT_SIZE_TITLE),
                ft.ExpansionTile(
                    title=ft.Text(f"{I18n.get('ai_thinking')}..."),
                    subtitle=ft.Text(
                        I18n.get("ai_expand_reasoning"),
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    controls=[
                        ft.Container(
                            content=ft.Markdown(
                                reasoning,
                                selectable=True,
                                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                                code_theme="atom-one-dark",  # type: ignore[arg-type]
                                on_tap_link=safe_open_url,
                            ),
                            padding=AppStyles.SPACING_SM,
                            bgcolor=AppColors.BACKGROUND,
                            border_radius=4,
                        )
                    ],
                    expanded=True,
                    visible=reasoning_visible,
                ),
                ft.Container(
                    content=ft.Markdown(
                        content,
                        selectable=True,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                        code_theme="atom-one-dark",  # type: ignore[arg-type]
                        on_tap_link=safe_open_url,
                    ),
                    padding=ft.Padding.only(left=5, right=5),
                ),
            ],
            spacing=10,
        ),
        border=ft.Border.all(1, AppColors.DIVIDER),
        border_radius=8,
        padding=AppStyles.SPACING_LG,
        bgcolor=AppColors.SURFACE,
        margin=ft.Margin.only(bottom=10),
    )


def _validate_strategy_params(params_def: list, params: typing.Mapping[str, typing.Any]) -> dict[str, Message]:
    """校验策略参数面板中的数值型参数 (UX-09 MINOR-07)。

    仅校验渲染为数字框的参数 (``type == "number"``, 与 ``build_param_control`` 同判据);
    返回 ``{参数名: 错误 Message}``, 全部合法返回空 dict。View 渲染期派生调用, 不持有状态,
    切换策略/切换 locale 后自动重算 (无残留)。

    判定:
    - 空值 → ``screener_param_required``
    - 非数字 / 非有限 (inf/nan, 如 ``"1e999"``) → ``screener_param_invalid``
    - 越界 (参数定义声明 ``min`` / ``max`` 时) → ``screener_param_below_min`` / ``screener_param_above_max``

    范围仅以策略元数据声明的 ``min`` / ``max`` 为准, 未声明时只校验可解析性,
    不在 UI 层硬编码业务阈值 (与 UX-03 单位单一数据源同理念)。
    """
    errors: dict[str, Message] = {}
    for p in params_def:
        if p.get("type", "number") != "number":
            continue
        name = p.get("name")
        if not name:
            continue
        raw = params.get(name, p.get("default"))
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            errors[name] = Message("screener_param_required", {})
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            errors[name] = Message("screener_param_invalid", {})
            continue
        if not math.isfinite(value):
            errors[name] = Message("screener_param_invalid", {})
            continue
        lower = p.get("min")
        upper = p.get("max")
        # min/max 由策略元数据声明; 未声明 (None) 或非数值时不作范围校验, 避免臆造业务阈值。
        if isinstance(lower, (int, float)) and value < lower:
            errors[name] = Message("screener_param_below_min", {"min": lower})
        elif isinstance(upper, (int, float)) and value > upper:
            errors[name] = Message("screener_param_above_max", {"max": upper})
    return errors


def build_param_control(
    p: dict,
    selected_strategy: str | None,
    params: dict,
    vm: ScreenerViewModel,
    on_slider_change: typing.Callable[[str, float], None],
    on_update: typing.Callable[[str, typing.Any], None],
    on_save_prompt: typing.Callable[[str], None],
    on_restore_prompt: typing.Callable[[str], None],
    prompt_error: str = "",
    param_error: Message | None = None,
) -> ft.Control | None:
    """构建单个策略参数控件 (D15: 从 ScreenerView._build_param_control 提取, props 化)."""
    label = I18n.get(p.get("label_key", p["name"]))
    p_type = p.get("type", "number")
    p_name = p["name"]

    # UX-03 (单位单一数据源): 参数单位由策略层 unit 字段声明, 本层统一拼接后缀,
    # 不再在 i18n 文案里硬编码单位 (避免文案与 threshold_in_data_unit 换算单位漂移)。
    _param_unit = p.get("unit")
    unit_i18n_key = _UNIT_SUFFIX_I18N_KEYS.get(_param_unit) if isinstance(_param_unit, str) else None
    if unit_i18n_key is not None:
        label = f"{label} ({I18n.get(unit_i18n_key)})"

    if p_type == "slider":
        min_val = p.get("min", 0)
        max_val = p.get("max", 100)
        default = p.get("default", min_val)
        step = p.get("step", 1)
        current_val = params.get(p_name, default)
        # NOTE: SliderInput 为 @ft.component 组件, 返回 Component wrapper (继承 BaseControl 而非
        # flet.Control), 无 col/width 布局属性, 直接在 wrapper 上赋 col 不会随 patch 下发客户端,
        # ResponsiveRow 默认按 col=12 布局 → 控件独占整行 → 参数面板变高 → table_card 视口高度
        # 被挤压到 0 → 表格行不生成语义节点 (PR #373 回归 / PR #550 E2E 失败)。
        # 修复: 外层包 Container 承载 width 与 col (Container 为 Control, 布局属性可正常下发)。
        return ft.Container(
            content=SliderInput(
                label=label,
                value=float(current_val),
                min_val=float(min_val),
                max_val=float(max_val),
                step=float(step),
                on_change=lambda v, n=p_name: on_slider_change(n, v),
            ),
            width=AppStyles.CONTROL_WIDTH_MD,
        )

    if p_type == "number":
        current_val = params.get(p_name, p.get("default", ""))
        return ft.TextField(
            label=label,
            value=str(current_val),
            keyboard_type=ft.KeyboardType.NUMBER,
            dense=True,
            border={
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.DIVIDER)),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.PRIMARY)),
            },
            text_size=AppStyles.FONT_SIZE_BODY,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            width=AppStyles.CONTROL_WIDTH_MD,
            # UX-09 MINOR-07: 越界/非法输入 inline 错误 (Message → 当前 locale), 并禁用运行按钮
            error=_render_status_message(param_error) or None,
            on_change=lambda e, n=p_name: on_update(n, _parse_num(e.control.value if e and e.control else "")),
        )

    if p_type == "dropdown":
        options = p.get("options", [])
        current_val = params.get(p_name, p.get("default", ""))
        return ft.Dropdown(
            label=label,
            value=str(current_val),
            options=[ft.dropdown.Option(str(o)) for o in options],
            dense=True,
            border={
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.DIVIDER)),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.PRIMARY)),
            },
            text_size=AppStyles.FONT_SIZE_BODY,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
            width=AppStyles.CONTROL_WIDTH_MD,
            on_select=lambda e, n=p_name: on_update(n, e.control.value if e and e.control else ""),
        )

    if p_type == "textarea":
        if p_name == "ai_system_prompt" and selected_strategy:
            current_val = params.get(p_name) or vm.get_base_prompt(selected_strategy) or p.get("default", "")
        else:
            current_val = params.get(p_name, p.get("default", ""))
        ctrl = ft.TextField(
            label=label,
            value=str(current_val),
            multiline=True,
            min_lines=6,
            max_lines=15,
            border={
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.DIVIDER)),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.PRIMARY)),
            },
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=10),
            error=(prompt_error or None)
            if p_name == "ai_system_prompt"
            else None,  # D19: AI prompt 校验失败 inline 错误
            on_change=lambda e, n=p_name: on_update(n, e.control.value if e and e.control else ""),
        )
        if p_name == "ai_system_prompt":
            ctrl.label = None
            # 面板仅在选中策略时渲染 (build_params_panel 先判空), 此处 selected_strategy 必非 None;
            # or "" 仅为消除 pyright str|None→str 告警 (D15 props 化后参数类型为 str | None).
            strat = selected_strategy or ""
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(label, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY),
                                ft.Container(expand=True),
                                ft.TextButton(
                                    content=I18n.get("ai_save_prompt"),
                                    icon=ft.Icons.SAVE,
                                    style=ft.ButtonStyle(color=AppColors.PRIMARY),
                                    height=30,
                                    on_click=lambda e, s=strat: on_save_prompt(s),
                                ),
                                ft.TextButton(
                                    content=I18n.get("ai_reset_default"),
                                    icon=ft.Icons.RESTORE,
                                    style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
                                    height=30,
                                    on_click=lambda e, s=strat: on_restore_prompt(s),
                                ),
                            ],
                        ),
                        ctrl,
                    ],
                    spacing=5,
                ),
                margin=ft.Margin.only(top=10, bottom=5),
            )
        return ft.Container(content=ctrl, margin=ft.Margin.only(top=10, bottom=5))

    return None


def build_params_panel(
    state: ScreenerState,
    vm: ScreenerViewModel,
    params: dict,
    on_slider_change: typing.Callable[[str, float], None],
    on_update: typing.Callable[[str, typing.Any], None],
    on_save_prompt: typing.Callable[[str], None],
    on_restore_prompt: typing.Callable[[str], None],
    prompt_error: str = "",
    param_errors: typing.Mapping[str, Message] | None = None,
) -> list[ft.Control]:
    """构建策略参数面板 (D15: 从 ScreenerView._build_params_panel 提取, props 化)."""
    from ui.theme import PARAM_GROUP_ORDER

    if not state.selected_strategy:
        return []

    errors = param_errors or {}

    params_def = vm.get_strategy_params(state.selected_strategy)
    if not params_def:
        return []

    groups: dict[str, list] = {g: [] for g in PARAM_GROUP_ORDER}
    custom_groups: dict[str, str | None] = {}
    group_labels: dict[str, str | None] = {}

    for p in params_def:
        group = p.get("group", "default")
        if group not in groups:
            custom_groups[group] = p.get("group_label_key")
            groups[group] = []
        groups[group].append(p)
        if group not in group_labels:
            group_labels[group] = p.get("group_label_key")

    # 参数面板改用 ResponsiveRow (12 列栅格) 承载控件, 避免 ft.Slider 置于
    # ft.Row(wrap=True) (Flutter Wrap) 触发 Flet Web 端 Dart 类型错误
    # (TypeError: ... is not a subtype of ...)。textarea 多行文本占满整行,
    # 其余小控件(滑块/数字/下拉)按断点多列排布。
    _PARAM_COL = {"sm": 12, "md": 6, "lg": 4}
    _PARAM_COL_TEXTAREA = 12

    def _build_controls(params_list: list[dict]) -> list[ft.Control]:
        controls: list[ft.Control] = []
        for p in params_list:
            ctrl = build_param_control(
                p,
                state.selected_strategy,
                params,
                vm,
                on_slider_change,
                on_update,
                on_save_prompt,
                on_restore_prompt,
                prompt_error,  # D19: 透传 AI prompt inline 错误
                errors.get(p["name"]),  # UX-09 MINOR-07: 透传数值参数越界/非法 inline 错误
            )
            if ctrl is None:
                continue
            ctrl.col = _PARAM_COL_TEXTAREA if p.get("type") == "textarea" else _PARAM_COL  # type: ignore[reportAttributeAccessIssue]  # Flet Control.col 期望 ResponsiveNumber；dict[str,int] 字面量推断与 stub 声明不符，运行时合法
            controls.append(ctrl)
        return controls

    rendered_groups: list[tuple[str, str, list[ft.Control]]] = []

    for group_name in PARAM_GROUP_ORDER:
        if group_name == "default":
            continue
        if groups[group_name]:
            controls = _build_controls(groups[group_name])
            if controls:
                title = _resolve_group_title(group_name, group_labels.get(group_name))
                rendered_groups.append((group_name, title, controls))

    if groups["default"]:
        controls = _build_controls(groups["default"])
        if controls:
            title = _resolve_group_title("default", group_labels.get("default"))
            rendered_groups.append(("default", title, controls))

    for group_name in custom_groups:
        if groups[group_name]:
            controls = _build_controls(groups[group_name])
            if controls:
                title = _resolve_group_title(group_name, custom_groups[group_name])
                rendered_groups.append((group_name, title, controls))

    result: list[ft.Control] = []
    for group_name, title, controls in rendered_groups:
        if group_name == "advanced":
            continue
        result.append(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            title,
                            size=AppStyles.FONT_SIZE_BODY,
                            weight=ft.FontWeight.W_500,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                        ft.Divider(height=1, color=AppColors.DIVIDER),
                        ft.ResponsiveRow(controls, spacing=15, run_spacing=15),
                    ],
                    spacing=8,
                ),
                padding=ft.Padding.all(12),
                bgcolor=AppColors.SURFACE_VARIANT,
                border_radius=8,
                margin=ft.Margin.only(bottom=8),
            )
        )

    if groups["advanced"]:
        controls = _build_controls(groups["advanced"])
        if controls:
            # MAJOR-07 DoD：高级设置入口挂稳定 anchor，E2E 在最小视口下展开它，
            # 验证控制区不被裁切且结果表首行仍可见。
            result.append(
                anchored(
                    EIDS.SCREENER.ADVANCED_SETTINGS,
                    ft.ExpansionTile(
                        title=ft.Text(
                            I18n.get("ai_advanced_settings"), size=AppStyles.FONT_SIZE_LG, weight=ft.FontWeight.W_500
                        ),
                        subtitle=ft.Text(
                            I18n.get("ai_advanced_settings_desc"),
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        controls=controls,
                        collapsed_text_color=AppColors.TEXT_PRIMARY,
                        text_color=AppColors.PRIMARY,
                        expanded=False,
                    ),
                )
            )

    return result


def build_history_tree(
    rows: tuple[HistoryTreeRow, ...],
    offset: int,
    has_more: bool,
    strategy_stats: tuple[StrategyStatRow, ...],
    ai_attribution: tuple[AiAttributionRow, ...],
    on_item_click: typing.Callable[[str, str | None, str | None], None],
    on_load_more: typing.Callable[[ft.ControlEvent], None],
) -> ft.Control:
    """构建历史树侧栏 (D15: 从 ScreenerView._build_history_tree 提取, props 化).

    状态从 ``state.history_tree`` 派生 (Task 3.2 消除双轨, 不持有 use_state)。
    含 UX-05「策略汇总」展开区 (state.strategy_stats 派生, T4) 与 BIZ-04 第二层
    「AI 效果归因」展开区 (state.ai_attribution 派生)。
    """
    tree_controls: list[ft.Control] = []
    if not rows:
        tree_controls.append(
            ft.Container(
                content=ft.Text(
                    I18n.get("screener_no_results"), color=AppColors.TEXT_SECONDARY, size=AppStyles.FONT_SIZE_BODY
                ),
                padding=AppStyles.SPACING_XL,
            )
        )
    else:
        first_expand = offset <= 5 and len(rows) <= 5
        for idx, item in enumerate(rows):
            display_date = item.display_date
            d_key = item.d_key
            total_cnt = item.total_cnt
            strategies = item.strategies

            subtiles: list[ft.Control] = [
                ft.ListTile(
                    leading=ft.Icon(ft.Icons.SELECT_ALL, size=AppStyles.FONT_SIZE_HEADLINE, color=AppColors.ACCENT),
                    title=ft.Text(
                        f"{I18n.get('screener_all_strategies')} ({total_cnt})", size=AppStyles.FONT_SIZE_BODY
                    ),
                    on_click=lambda e, d=d_key: on_item_click(d, None, None),
                    dense=True,
                )
            ]
            for s in strategies:
                strategy_display = translate_strategy_name(s.strategy_name)
                # RV-03: append-only 语义下历史树按 (trade_date, strategy_name, run_id) 分组，
                # 每条策略行对应当次运行（run_id 精确标识），点击按该 run_id 载入该次运行的
                # 精确股票集（多 run 快照分别可开，不再被覆盖语义合并）。
                run_suffix = f" [{s.run_id[:8]}]" if len(strategies) > 1 and s.run_id else ""
                subtiles.append(
                    ft.ListTile(
                        leading=ft.Icon(
                            ft.Icons.TRENDING_UP, size=AppStyles.FONT_SIZE_TITLE, color=AppColors.TEXT_SECONDARY
                        ),
                        title=ft.Text(f"{strategy_display}{run_suffix} ({s.cnt})", size=AppStyles.FONT_SIZE_BODY),
                        on_click=lambda e, d=d_key, sn=s.strategy_name, rid=s.run_id: on_item_click(d, sn, rid),
                        dense=True,
                    )
                )

            tree_controls.append(
                ft.ExpansionTile(
                    title=ft.Text(display_date, size=AppStyles.FONT_SIZE_LG, weight=ft.FontWeight.W_500),
                    subtitle=ft.Text(
                        I18n.get("history_total").format(count=total_cnt),
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    controls=subtiles,
                    expanded=(first_expand and idx == 0),
                    collapsed_icon_color=AppColors.TEXT_SECONDARY,
                )
            )

    load_more_btn = ft.TextButton(
        content=I18n.get("history_load_more"),
        icon=ft.Icons.EXPAND_MORE,
        on_click=safe_on_click(on_load_more),
        visible=has_more,
    )

    suffix_items: list[ft.Control] = []
    if ai_attribution:
        suffix_items.append(_build_ai_attribution_section(ai_attribution))
    if strategy_stats:
        suffix_items.append(_build_review_stats_section(strategy_stats))

    return ft.Container(
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Text(
                        I18n.get("screener_mode_history"),
                        weight=ft.FontWeight.BOLD,
                        color=AppColors.TEXT_PRIMARY,
                        size=AppStyles.FONT_SIZE_LG,
                    ),
                    padding=ft.Padding.only(left=12, top=10, bottom=5),
                ),
                ft.Divider(height=1, color=AppColors.DIVIDER),
                *suffix_items,
                ft.ListView(tree_controls, expand=True, spacing=0),
                load_more_btn,
            ],
            spacing=0,
            expand=True,
        ),
        bgcolor=AppColors.SURFACE,
        border=ft.Border.only(right=ft.BorderSide(1, AppColors.DIVIDER)),
    )


def _format_pct(value: float | None) -> str:
    """收益分位值 (0.0123 = 1.23%) 格式化; None 用占位符 '—'（R21 None 哨兵, 不伪装 0）。"""
    return "—" if value is None else f"{value * 100:.2f}%"


def _grade_badge(grade: SampleGrade) -> ft.Control | None:
    """样本量分级角标: insufficient/limited 提示文案; adequate 不显示以减少噪音。"""
    key = {
        SampleGrade.INSUFFICIENT: "review_stats_insufficient",
        SampleGrade.LIMITED: "review_stats_limited",
        SampleGrade.ADEQUATE: None,
    }.get(grade)
    if key is None:
        return None
    color = AppColors.WARNING if grade == SampleGrade.LIMITED else AppColors.ERROR
    return ft.Container(
        content=ft.Text(I18n.get(key), size=AppStyles.FONT_SIZE_CAPTION, color=color),
        padding=ft.Padding.only(top=2),
    )


def _ci_text(lo: float | None, hi: float | None) -> str:
    """t 分布 95% 置信区间文本; 任一端 None 表示无法计算 (n<30 或 std 无效)。"""
    if lo is None or hi is None:
        return "—"
    return f"[{lo * 100:.2f}%, {hi * 100:.2f}%]"


# RV-10: 覆盖缺口阈值——row_n / (avg_daily_count * n) 低于此值时即判定大量入选
# 记录未参与该指标计算（基准缺失/未成熟等），均值存在幸存者偏差放大风险。
_COVERAGE_GAP_RATIO = 0.8


def _coverage_gap_pct(alpha: object, avg_daily_count: float) -> float | None:
    """计算 Alpha 指标的实际参与率 (row_n / (avg_daily_count × n))。

    理论参与股票行数 = avg_daily_count(日均纳入) × n(日序列长)；实际参与 =
    alpha.row_n (窗口内 Σ alpha_n)。row_n 或 avg_daily_count*n 为 0 时无法
    计算返回 None (R21: 缺失不伪装)。
    返回值为参与率（0~1），调用方以 _COVERAGE_GAP_RATIO=0.8 判定缺口。

    已知局限（对抗检视）：整日 alpha 全 NULL 时，分母乘以 n_alpha（alpha 有效
    日数）与分子 row_n 同步折算，ratio 仍 ≈1，不告警——此类「整日缺口」由
    日序列 N 缩水本身暴露（alpha.n < t1.n 用户可见），本比率专注「同日部分
    股票缺失」的横截面缺口。
    """
    row_n = getattr(alpha, "row_n", None)
    if row_n is None or row_n <= 0 or avg_daily_count <= 0:
        return None
    total = avg_daily_count * getattr(alpha, "n", 0)
    if total <= 0:
        return None
    ratio = float(row_n) / total
    return ratio


def _coverage_gap_hint(alpha: object, avg_daily_count: float) -> ft.Control | None:
    """覆盖缺口提示控件: 仅当实际参与率 < _COVERAGE_GAP_RATIO 时渲染 (RV-10)。

    缺口说明大量入选记录（基准缺失/未成熟/被覆盖）未参与 Alpha 计算，日组合均值
    存在幸存者偏差放大风险——让用户看得见「日均纳入 20 只」与「实际参与 12 只」
    之间的缺口，而不是一个看似确定的均值。
    """
    ratio = _coverage_gap_pct(alpha, avg_daily_count)
    if ratio is None or ratio >= _COVERAGE_GAP_RATIO:
        return None
    pct = max(0, int((1 - ratio) * 100))
    return ft.Text(
        I18n.get("review_stats_coverage_gap", pct=pct),
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.WARNING,
    )


def _coverage_gap_controls(alpha: object, avg_daily_count: float) -> list[ft.Control]:
    """覆盖缺口提示包装为列表（0 或 1 项），供列布局直接展开 (RV-10)。"""
    hint = _coverage_gap_hint(alpha, avg_daily_count)
    return [hint] if hint is not None else []


def _build_review_stats_section(strategy_stats: tuple[StrategyStatRow, ...]) -> ft.Control:
    """history 侧栏「策略汇总」展开区 (UX-05, T4)。

    按 (strategy_name, benchmark_code) 分组: 行内主指标 = T+1 Alpha / CI / 日序列 N +
    胜率独立 N 角标 + 日均纳入股票数; T+5 为折叠内容并标注「无超额基准」; 基准未知组与
    可比组强视觉分隔 (四审 L3)。纯声明式 (props 派生自 state.strategy_stats), 不持业务状态 (§3.2)。
    """
    tiles: list[ft.Control] = []
    for row in strategy_stats:
        alpha = row.alpha
        if row.benchmark_known:
            hm_line = f"{I18n.get('review_stats_alpha')}: {_format_pct(alpha.mean)}"
            ci_line = f"{I18n.get('review_stats_ci')}: {_ci_text(alpha.ci_lower, alpha.ci_upper)}"
            main_badge = _grade_badge(row.alpha_grade)
        else:
            hm_line = f"{I18n.get('review_stats_benchmark_unknown')} · {I18n.get('review_stats_no_excess_benchmark')}"
            ci_line = ""
            main_badge = None

        # RV-09: 名义 N 与有效样本量并排展示（120 个交易日 / 有效 24），让用户
        # 看得见重叠窗口折算过程，而非只给已折算的黑箱分级。
        main_lines = [
            hm_line,
            ci_line,
            f"{I18n.get('review_stats_n_dates')}: {alpha.n}",
            f"{I18n.get('review_stats_n_eff')}: {alpha.n_eff:.0f}",
        ]
        subtitle_items = [ft.Text("  ·  ".join(x for x in main_lines if x), size=AppStyles.FONT_SIZE_CAPTION)]
        if main_badge is not None:
            subtitle_items.append(main_badge)

        win_rate_text = (
            f"{I18n.get('review_stats_win_rate')}: {f'{row.win_rate * 100:.1f}%' if row.win_rate is not None else '—'}"
        )
        win_n_text = f"{I18n.get('review_stats_win_n')}: {row.win_n}"
        win_badge = _grade_badge(row.win_grade)
        win_items: list[ft.Control] = [
            ft.Text("  ·  ".join([win_rate_text, win_n_text]), size=AppStyles.FONT_SIZE_CAPTION)
        ]
        if win_badge is not None:
            win_items.append(win_badge)

        details: list[ft.Control] = []
        details.append(ft.Column(win_items, spacing=2))
        details.append(
            ft.Text(
                f"{I18n.get('review_stats_avg_daily_count')}: {row.avg_daily_count:.1f}",
                size=AppStyles.FONT_SIZE_CAPTION,
            )
        )
        # RV-10: 覆盖缺口条件提示——仅当 Alpha 实际参与率 < 0.8 时渲染
        # （大量入选记录未参与该指标计算 → 幸存者偏差放大风险）。
        details.extend(_coverage_gap_controls(row.alpha, row.avg_daily_count))
        details.append(
            ft.Text(
                f"{I18n.get('review_stats_t5')}: {_format_pct(row.t5.mean)} "
                f"({I18n.get('review_stats_no_excess_benchmark')})",
                size=AppStyles.FONT_SIZE_CAPTION,
            )
        )
        details.append(
            ft.Text(
                I18n.get("review_stats_survivor_bias"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            )
        )

        benchmark_label = row.benchmark_code or I18n.get("review_stats_benchmark_unknown")
        tiles.append(
            ft.ExpansionTile(
                title=ft.Text(
                    f"{translate_strategy_name(row.strategy_name)} · {benchmark_label}",
                    size=AppStyles.FONT_SIZE_BODY,
                ),
                subtitle=ft.Column(subtitle_items, spacing=2),
                controls=[ft.Column(details, spacing=8)],
                collapsed_icon_color=AppColors.TEXT_SECONDARY,
                dense=True,
            )
        )

    return ft.Column(
        [
            ft.Container(
                content=ft.Text(
                    I18n.get("review_stats_title"),
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                    size=AppStyles.FONT_SIZE_LG,
                ),
                padding=ft.Padding.only(left=12, top=10, bottom=5),
            ),
            *tiles,
            ft.Divider(height=1, color=AppColors.DIVIDER),
        ],
        spacing=0,
    )


def _build_ai_attribution_section(ai_attribution: tuple[AiAttributionRow, ...]) -> ft.Control:
    """history 侧栏「AI 效果归因」展开区 (BIZ-04 第二层)。

    按 (strategy_name, benchmark_code, has_ai) 分组: 同策略下 AI 组 (has_ai=True, 历史上
    真实产生过 AI 判断) 与无 AI 组 (has_ai=False) 并排展示 T+1/T+5/Alpha/胜率。归因口径为
    「历史上真实发生的 AI 判断」的事后统计 (不重放 LLM, 无前视偏差)，相关非因果 —— 文案
    明示自选择偏差与代理口径局限 (ADR-0009)。纯声明式 (props 派生自 state.ai_attribution),
    不持业务状态 (§3.2)。
    """
    tiles: list[ft.Control] = []
    for row in ai_attribution:
        alpha = row.alpha
        if row.benchmark_known:
            hm_line = f"{I18n.get('ai_attribution_alpha')}: {_format_pct(alpha.mean)}"
            ci_line = f"{I18n.get('review_stats_ci')}: {_ci_text(alpha.ci_lower, alpha.ci_upper)}"
            main_badge = _grade_badge(row.alpha_grade)
        else:
            hm_line = f"{I18n.get('review_stats_benchmark_unknown')} · {I18n.get('review_stats_no_excess_benchmark')}"
            ci_line = ""
            main_badge = None

        # RV-09: 名义 N 与有效样本量并排展示（120 个交易日 / 有效 24），让用户
        # 看得见重叠窗口折算过程，而非只给已折算的黑箱分级。
        main_lines = [
            hm_line,
            ci_line,
            f"{I18n.get('review_stats_n_dates')}: {alpha.n}",
            f"{I18n.get('review_stats_n_eff')}: {alpha.n_eff:.0f}",
        ]
        subtitle_items = [ft.Text("  ·  ".join(x for x in main_lines if x), size=AppStyles.FONT_SIZE_CAPTION)]
        if main_badge is not None:
            subtitle_items.append(main_badge)

        win_rate_text = (
            f"{I18n.get('review_stats_win_rate')}: {f'{row.win_rate * 100:.1f}%' if row.win_rate is not None else '—'}"
        )
        win_n_text = f"{I18n.get('review_stats_win_n')}: {row.win_n}"
        win_badge = _grade_badge(row.win_grade)
        win_items: list[ft.Control] = [
            ft.Text("  ·  ".join([win_rate_text, win_n_text]), size=AppStyles.FONT_SIZE_CAPTION)
        ]
        if win_badge is not None:
            win_items.append(win_badge)

        group_key = "ai_attribution_ai" if row.has_ai else "ai_attribution_no_ai"
        benchmark_label = row.benchmark_code or I18n.get("review_stats_benchmark_unknown")
        tiles.append(
            ft.ExpansionTile(
                title=ft.Text(
                    f"{translate_strategy_name(row.strategy_name)} · {benchmark_label} · {I18n.get(group_key)}",
                    size=AppStyles.FONT_SIZE_BODY,
                ),
                subtitle=ft.Column(subtitle_items, spacing=2),
                controls=[
                    ft.Column(
                        [
                            ft.Column(win_items, spacing=2),
                            ft.Text(
                                f"{I18n.get('review_stats_avg_daily_count')}: {row.avg_daily_count:.1f}",
                                size=AppStyles.FONT_SIZE_CAPTION,
                            ),
                            # RV-10: 覆盖缺口条件提示（与策略汇总区同语义，AI 归因亦受
                            # 幸存者偏差放大影响，见 ADR-0009 自选择偏差与代理口径局限）。
                            *_coverage_gap_controls(row.alpha, row.avg_daily_count),
                            ft.Text(
                                f"{I18n.get('review_stats_t5')}: {_format_pct(row.t5.mean)} "
                                f"({I18n.get('review_stats_no_excess_benchmark')})",
                                size=AppStyles.FONT_SIZE_CAPTION,
                            ),
                        ],
                        spacing=8,
                    )
                ],
                collapsed_icon_color=AppColors.TEXT_SECONDARY,
                dense=True,
            )
        )

    return ft.Column(
        [
            ft.Container(
                content=ft.Text(
                    I18n.get("ai_attribution_title"),
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                    size=AppStyles.FONT_SIZE_LG,
                ),
                padding=ft.Padding.only(left=12, top=10, bottom=5),
            ),
            ft.Container(
                content=ft.Text(
                    I18n.get("ai_attribution_caveat"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
                padding=ft.Padding.only(left=12, right=12, bottom=5),
            ),
            *tiles,
            ft.Divider(height=1, color=AppColors.DIVIDER),
        ],
        spacing=0,
    )


async def _execute_screener_export(
    vm: ScreenerViewModel,
    file_picker: ft.FilePicker | None,
    page: ft.Page | None,
    format_: str,
) -> None:
    """Export current results to CSV or Excel.

    Args:
        vm: ScreenerViewModel
        file_picker: FilePicker instance
        page: Page instance
        format_: "csv" or "excel"
    """
    UILogger.log_action("ScreenerView", "Click", f"btn_export_{format_}")
    df = vm.get_export_data()
    if df is None:
        if page is not None:
            _show_toast(page, I18n.get("data_export_no_data"), "error")
        return
    timestamp = get_now().strftime("%Y%m%d_%H%M%S")
    ext = "csv" if format_ == "csv" else "xlsx"
    default_filename = f"screener_results_{timestamp}.{ext}"
    is_web = page is not None and page.web
    if is_web:
        try:
            # R16: df.to_csv/to_excel 是 CPU 密集操作, 通过 VM 方法 offload 到 CPU 线程池
            src_bytes, _err = await vm.export_results_bytes(
                format_
            )  # _err 意图性未用（下划线命名豁免 RUF059），错误经 vm.state 呈现
            if src_bytes is None:
                if page is not None:
                    _show_toast(page, I18n.get("data_export_fail"), "error")
                return
            if file_picker is None:
                return
            await file_picker.save_file(
                dialog_title=I18n.get("data_export_save_title"),
                file_name=default_filename,
                allowed_extensions=[ext],
                src_bytes=src_bytes,
            )
            if page is not None:
                _show_toast(page, I18n.get("data_export_success", file=default_filename), "success")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] Export | Failed: %s", DataSanitizer.sanitize_error(ex))
            if page is not None:
                _show_toast(page, I18n.get("data_export_fail"), "error")
        return

    if file_picker is None:
        return
    filepath = await file_picker.save_file(
        dialog_title=I18n.get("data_export_save_title"),
        file_name=default_filename,
        allowed_extensions=[ext],
    )
    if not filepath:
        return
    try:
        if format_ == "csv":
            path, _error = await vm.export_results(filepath)
        else:
            path, _error = await vm.export_results_excel(filepath)  # 错误经 vm.state 呈现，返回值仅需 path
        if path:
            filename = os.path.basename(filepath)
            if page is not None:
                _show_toast(
                    page,
                    I18n.get("data_export_success", file=filename),
                    "success",
                    action_text=I18n.get("data_export_open_folder"),
                    on_action=lambda: page.run_task(open_export_folder, filepath),
                )
        elif page is not None:
            _show_toast(page, I18n.get("data_export_fail"), "error")
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error("[ScreenerView] Export | Failed: %s", DataSanitizer.sanitize_error(ex))
        if page is not None:
            _show_toast(page, I18n.get("data_export_fail"), "error")


async def _execute_load_history_tree(
    vm: ScreenerViewModel,
    page: ft.Page | None,
    append: bool,
) -> None:
    """加载历史树数据 (Task 3.2: VM 更新 state.history_tree, View 不再处理 items)."""
    try:
        await vm.load_history_tree(append=append)
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error("[ScreenerView] History tree load failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
        if page is not None:
            _show_toast(page, I18n.get("screener_load_failed"), "error")


async def _execute_load_strategy_stats(vm: ScreenerViewModel, page: ft.Page | None) -> None:
    """加载复盘聚合统计 (UX-05: VM 更新 state.strategy_stats).

    与 ``_execute_load_history_tree`` 保持一致的异常处理: CancelledError 传播,
    普通异常记录日志并发错误 toast, 避免计算异常静默吞没。
    """
    try:
        await vm.load_strategy_stats()
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error("[ScreenerView] Review stats load failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
        if page is not None:
            _show_toast(page, I18n.get("screener_load_failed"), "error")


async def _execute_load_ai_attribution(vm: ScreenerViewModel, page: ft.Page | None) -> None:
    """加载 AI 结论快照回放归因统计 (BIZ-04 第二层: VM 更新 state.ai_attribution).

    与 ``_execute_load_strategy_stats`` 保持一致的异常处理: CancelledError 传播,
    普通异常记录日志并发错误 toast, 避免计算异常静默吞没。
    """
    try:
        await vm.load_ai_attribution()
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error("[ScreenerView] AI attribution load failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
        if page is not None:
            _show_toast(page, I18n.get("screener_load_failed"), "error")


async def _execute_load_history_for_date(
    vm: ScreenerViewModel,
    page: ft.Page | None,
    trade_date: str | datetime.date | datetime.datetime,
    strategy_name: str | None,
    run_id: str | None,
) -> None:
    """加载指定日期的历史选股结果."""
    if isinstance(trade_date, (datetime.date, datetime.datetime)):
        display = trade_date.strftime("%Y-%m-%d")
        trade_date = display
    else:
        ts = str(trade_date)
        display = f"{ts[:4]}-{ts[4:6]}-{ts[6:]}" if len(ts) == 8 and ts.isdigit() else ts
    vm.set_history_viewing_status(display, strategy_name=strategy_name, run_id=run_id)
    try:
        await vm.load_history_data(trade_date, strategy_name, run_id)
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error("[ScreenerView] Load history for date failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
        if page is not None:
            _show_toast(page, I18n.get("screener_load_failed"), "error")


async def _execute_add_to_watchlist(
    wl_vm: WatchlistViewModel,
    page: ft.Page | None,
    ts_code: str,
    stock_name: str,
) -> None:
    """FR-UX-004, Task 4.2: 加入关注."""
    try:
        await wl_vm.add_to_watchlist(ts_code, stock_name)
        if page is not None:
            _show_toast(page, I18n.get("watchlist_added"), "success")
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error("[ScreenerView] Add to watchlist failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
        if page is not None:
            _show_toast(page, I18n.get("watchlist_add_failed"), "error")


async def _execute_restore_default_prompt(
    vm: ScreenerViewModel,
    page: ft.Page | None,
    strat: str,
    on_update_param: typing.Callable[[str, typing.Any], None],
) -> None:
    """恢复默认 AI Prompt."""
    try:
        new_val = await vm.reset_strategy_prompt(strat)
        on_update_param("ai_system_prompt", new_val)
        if page is not None:
            _show_toast(page, I18n.get("ai_settings_restored"), "info")
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error(
            "[ScreenerView] Restore default prompt failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
        )
        if page is not None:
            _show_toast(page, I18n.get("sys_snack_save_err"), "error")


async def _execute_save_prompt(
    vm: ScreenerViewModel,
    page: ft.Page | None,
    strat: str,
    set_prompt_error: typing.Callable[[str], None],
) -> None:
    """保存 AI Prompt."""
    try:
        prompt_val = (vm.state.strategy_params).get("ai_system_prompt", "") or ""
        success, error_key = await vm.save_strategy_prompt(strat, prompt_val)
        if page is None:
            return
        if success:
            set_prompt_error("")
            UILogger.log_action("ScreenerView", "SavePrompt", f"strategy={strat}")
            _show_toast(page, I18n.get("ai_settings_saved"), "success")
        else:
            from utils.prompt_guard import MAX_PROMPT_LENGTH

            assert error_key is not None
            msg = I18n.get(error_key, error_key)
            if error_key == "prompt_err_length":
                msg = I18n.get("prompt_err_length").format(max=MAX_PROMPT_LENGTH)
            set_prompt_error(msg)
    except asyncio.CancelledError:
        raise
    except Exception as ex:
        logger.error("[ScreenerView] Save prompt failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
        if page is not None:
            _show_toast(page, I18n.get("sys_snack_save_err"), "error")


async def _execute_pending_strategy_run(vm: ScreenerViewModel, key: str) -> None:
    """选中并执行挂起策略."""
    vm.select_strategy(key)
    vm.update_strategy_desc(key)
    vm.init_strategy_params(key)
    try:
        await vm.run_strategy(key, params=dict(vm.state.strategy_params), exclude_st=vm.state.exclude_st)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(
            "[ScreenerView] Pending strategy execution failed: %s", DataSanitizer.sanitize_error(e), exc_info=True
        )


def _handle_backtest_jump(state: ScreenerState, vm: ScreenerViewModel, page: ft.Page | None) -> None:
    """选股→回测参数透传跳转."""
    UILogger.log_action("ScreenerView", "Click", "btn_jump_backtest")
    if not state.selected_strategy:
        return
    set_pending_prefill(state.selected_strategy, params=dict(vm.state.strategy_params))
    if page is not None:
        page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, "backtest")


def _handle_go_sync(page: ft.Page | None) -> None:
    """质量门失败恢复动作 — 跳转数据源同步."""
    UILogger.log_action("ScreenerView", "Click", "btn_go_sync")
    if page is not None:
        page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, "settings:data")


def _handle_page_size_change(vm: ScreenerViewModel, e: ft.ControlEvent) -> None:
    """分页大小变更处理."""
    try:
        vm.change_page_size(int(get_control_value(e.control, ft.Dropdown) if e and e.control else 50))
    except (ValueError, TypeError):
        pass


def _handle_update_param(
    vm: ScreenerViewModel,
    name: str,
    value: typing.Any,
    prompt_err: str,
    set_err: typing.Callable[[str], None],
) -> None:
    """策略参数更新处理."""
    vm.set_strategy_param(name, value)
    if name == "ai_system_prompt" and prompt_err:
        set_err("")


def _sync_file_picker_service(page: ft.Page | None, picker: ft.FilePicker | None, attach: bool) -> None:
    """注册或移除 FilePicker 服务."""
    if page is not None and picker is not None:
        if attach and picker not in page.services:
            page.services.append(picker)
        elif not attach and picker in page.services:
            page.services.remove(picker)


async def _debounced_desc_update(vm: ScreenerViewModel, strat: str) -> None:
    """B14: 参数拖动防抖更新策略描述."""
    try:
        await asyncio.sleep(0.15)
        vm.update_strategy_desc(strat, params=dict(vm.state.strategy_params))
    except asyncio.CancelledError:
        raise


def _schedule_desc_update(timer_ref: ft.Ref, vm: ScreenerViewModel, strat: str) -> None:
    """调度策略描述防抖更新."""
    prev = timer_ref.current
    if prev is not None:
        prev.cancel()
    task = asyncio.create_task(_debounced_desc_update(vm, strat))
    timer_ref.current = task
    task.add_done_callback(lambda t: _clear_desc_timer(timer_ref, task))


def _clear_desc_timer(timer_ref: ft.Ref, task: asyncio.Task) -> None:
    """清理已完成的防抖任务引用."""
    if timer_ref.current is task:
        timer_ref.current = None


def _resolve_table_data(
    current_page_rows: tuple[ScreenerRow, ...],
    memo_ref: ft.Ref[tuple | None],
    vm: ScreenerViewModel,
) -> tuple[list, list[dict]]:
    """解析表格列与行数据 (带基于引用与 locale 的 memo 缓存)."""
    if not current_page_rows:
        memo_ref.current = None
        return [], []
    memo = memo_ref.current
    if memo is not None and memo[0] is current_page_rows and memo[1] == get_observable_state().locale:
        return memo[2], memo[3]
    vt_columns, formatted_rows = _build_table_data(current_page_rows, vm)
    memo_ref.current = (current_page_rows, get_observable_state().locale, vt_columns, formatted_rows)
    return vt_columns, formatted_rows


# D7-3: AI 三分区标题 → (i18n key, 图标, 标题颜色)。仅 View 渲染期使用; 数据按 ai_status
# 已由 VM 拆分为 ai_recommended_rows/ai_excluded_rows/ai_failed_rows (§3.2 VM 只产出 key)。
# CRITICAL-01: 分区标题只表达 AI 处理状态, 不作投资判断。"analyzed"(score>0) 分区原用
# 「AI 推荐」+ SUCCESS 对勾, 会被误读为买入建议; 现改中性文案 + 中性色, 投资判断交由每行
# ai_score 数值承载 (不依赖分区颜色暗示)。
_SECTION_META: tuple[tuple[str, typing.Any, str, str], ...] = (
    # (i18n key, 图标名, 图标色, 分区标签)
    ("screener_section_recommended", ft.Icons.ANALYTICS, AppColors.TEXT_SECONDARY, "recommended"),
    ("screener_section_excluded", ft.Icons.DO_NOT_DISTURB, AppColors.WARNING, "excluded"),
    ("screener_section_failed", ft.Icons.ERROR_OUTLINE, AppColors.ERROR, "failed"),
)


def _build_screener_sections_card(
    *,
    section_counts: tuple[int, int, int],
    active_section: str,
    section_rows: list[dict],
    vt_columns: list,
    sort_col: str | None,
    sort_asc: bool,
    on_virtual_sort: typing.Callable[[str, bool], None],
    on_row_click: typing.Callable[[dict], None],
    on_section_change: typing.Callable[[ft.ControlEvent], None],
    on_load_col_widths: typing.Callable[[], dict[str, int] | None],
    on_persist_col_widths: typing.Callable[[dict[str, int]], None],
) -> ft.Container:
    """构建 AI 三分区卡片 (D7-3 + MAJOR-06): 分组页签 + 活动分组的表格/空态.

    MAJOR-06 (review 09-24): 「分组先于分页」——
    1. 页签标签承载**全量**分组计数 ``section_counts`` (VM 对过滤后全量结果的统计,
       非当前页切片长度), 修掉「标题只统计当前页」的失真;
    2. 一次只渲染活动分组的表格, 三张表不再同屏互相挤占高度 (视口 1280×720 下
       每张表可独占卡片高度)。

    页签仍只表达 AI 处理状态 (CRITICAL-01 中性文案), 不做投资判断。
    本函数仅消费 state 快照, 不持有业务状态 (§3.2 VM 只产出 key, View 渲染期翻译)。
    """
    switcher = ft.SegmentedButton(
        segments=[
            ft.Segment(
                value=meta[3],
                label=ft.Text(I18n.get(meta[0]).format(count=section_counts[index])),
                icon=ft.Icon(meta[1], color=meta[2]),
            )
            for index, meta in enumerate(_SECTION_META)
        ],
        selected=[active_section],
        on_change=on_section_change,
    )

    if section_rows:
        body: ft.Control = ft.Column(
            [
                PaginatedTable(
                    rows=section_rows,
                    columns=vt_columns,
                    sort_col=sort_col,
                    sort_asc=sort_asc,
                    on_sort=on_virtual_sort,
                    on_row_click=on_row_click,
                    col_anchor=EIDS.SCREENER.column_header,
                    row_anchor=lambda row: EIDS.SCREENER.result_row(row["ts_code"]) if row.get("ts_code") else None,
                    on_row_detail=on_row_click,
                    detail_anchor=lambda row: (
                        EIDS.SCREENER.detail_button(row["ts_code"]) if row.get("ts_code") else None
                    ),
                    col_widths_key=_VT_COL_WIDTHS_KEY,
                    on_load_col_widths=on_load_col_widths,
                    on_persist_col_widths=on_persist_col_widths,
                ),
            ],
            spacing=0,
            expand=True,
        )
    else:
        body = ft.Container(
            content=ft.Row(
                safe_controls(
                    [
                        ft.Text(
                            I18n.get("screener_no_results"),
                            color=AppColors.TEXT_SECONDARY,
                            size=AppStyles.FONT_SIZE_BODY_SM,
                        )
                    ]
                ),
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=8,
            expand=True,
        )

    return ft.Container(
        content=ft.Column([switcher, ft.Divider(height=1, color=AppColors.DIVIDER), body], spacing=4),
        **AppStyles.dashboard_card(padding=AppStyles.SPACING_MD),
        expand=True,
    )


def _build_screener_params_sidebar(
    state: ScreenerState,
    vm: ScreenerViewModel,
    *,
    handlers: dict[str, typing.Any],
    prompt_error: str,
    param_errors: typing.Mapping[str, Message] | None = None,
) -> ft.Control | None:
    """构建左侧策略参数侧栏 (MAJOR-07)。

    参数面板从顶部控制卡移入可调宽度侧栏（复用 ``ResizableSplitter``），控制卡高度
    不再随参数组/参数数量增长，结果表在 1024×640 等小窗口下独占剩余全高，不再被挤没。

    返回 ``None`` 表示当前无可渲染参数（未选策略或所选策略无参数定义），调用方据此
    退化为单栏布局（不渲染空侧栏）。

    侧栏内部用可滚动 ``Column``，小高度窗口下参数多于可视区时可滚动，不裁切。
    """
    panels = build_params_panel(
        state,
        vm,
        dict(state.strategy_params),
        handlers["on_slider_value_change"],
        handlers["on_update_param"],
        handlers["on_save_prompt"],
        handlers["on_restore_prompt"],
        prompt_error,
        param_errors,
    )
    if not panels:
        return None
    return ft.Container(
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Text(
                        I18n.get("screener_params_panel_title"),
                        weight=ft.FontWeight.BOLD,
                        color=AppColors.TEXT_PRIMARY,
                        size=AppStyles.FONT_SIZE_LG,
                    ),
                    padding=ft.Padding.only(left=12, top=10, bottom=5),
                ),
                ft.Divider(height=1, color=AppColors.DIVIDER),
                ft.Column(panels, scroll=ft.ScrollMode.AUTO, expand=True, spacing=0),
            ],
            spacing=0,
            expand=True,
        ),
        bgcolor=AppColors.SURFACE,
        border=ft.Border.only(right=ft.BorderSide(1, AppColors.DIVIDER)),
    )


def _build_screener_control_card(
    state: ScreenerState,
    vm: ScreenerViewModel,
    *,
    status_text_value: str,
    status_text_color: str,
    progress_visible: bool,
    run_disabled: bool,
    export_btn_disabled: bool,
    handlers: dict[str, typing.Any],
) -> ft.Container:
    """构建顶部控制卡 (标题栏/模式切换/策略下拉/策略说明/操作按钮).

    MAJOR-07: 策略参数面板已从本卡移出，改由 ``_build_screener_params_sidebar`` 渲染到
    可调宽度的左侧栏，使结果表独占右侧全高（控制卡高度不再随参数数量增长）。
    """
    is_realtime = state.mode == "REALTIME"

    title_row = ft.Row(
        safe_controls(
            [
                ft.Icon(ft.Icons.ELECTRIC_BOLT, color=AppColors.PRIMARY, size=AppStyles.FONT_SIZE_XL),
                ft.Text(
                    I18n.get("screener_title"),
                    size=AppStyles.FONT_SIZE_XL,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(width=20),
                ft.SegmentedButton(
                    segments=[
                        ft.Segment(
                            value="REALTIME",
                            label=ft.Text(I18n.get("screener_mode_run")),
                            icon=ft.Icon(ft.Icons.ELECTRIC_BOLT),
                        ),
                        ft.Segment(
                            value="HISTORY",
                            label=ft.Text(I18n.get("screener_mode_history")),
                            icon=ft.Icon(ft.Icons.HISTORY),
                        ),
                    ],
                    selected=[state.mode],
                    on_change=safe_on_change(handlers["on_mode_change"]),
                ),
            ]
        ),
        alignment=ft.MainAxisAlignment.START,
        spacing=10,
    )

    strategy_label = I18n.get("select_strategy")
    strategy_options = _build_strategy_options(state.strategies_with_dep)
    strategy_dropdown = anchored(
        EIDS.SCREENER.STRATEGY_DROPDOWN,
        ft.Dropdown(
            label=strategy_label,
            options=strategy_options,
            value=state.selected_strategy,
            on_select=safe_on_select(handlers["on_strategy_change"]),
            width=AppStyles.calc_dropdown_width(strategy_options, label=strategy_label),
            text_size=AppStyles.FONT_SIZE_LG,
            bgcolor=AppColors.INPUT_BG,
            border={
                ft.ControlState.DEFAULT: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
                ft.ControlState.FOCUSED: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.PRIMARY)),
            },
            color=AppColors.INPUT_TEXT,
        ),
    )

    stock_filter_field = ft.TextField(
        label=I18n.get("screener_filter_stock"),
        value=state.stock_filter,
        dense=True,
        border={
            ft.ControlState.DEFAULT: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.DIVIDER)),
            ft.ControlState.FOCUSED: ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.PRIMARY)),
        },
        text_size=AppStyles.FONT_SIZE_BODY,
        width=AppStyles.CONTROL_WIDTH_SM,
        on_change=safe_on_change(handlers["on_stock_filter_change"]),
    )
    # DS-02: 风险警示股（ST/*ST）排除开关，经 on_exclude_st_change → vm.set_exclude_st 透传数据层行过滤
    exclude_st_switch = ft.Switch(
        label=I18n.get("screener_exclude_st"),
        value=state.exclude_st,
        on_change=safe_on_change(handlers["on_exclude_st_change"]),
        active_color=AppColors.PRIMARY,
    )
    filter_row = ft.Row([stock_filter_field, exclude_st_switch, ft.Container(expand=True)], spacing=10)

    realtime_controls = ft.Column(
        [
            ft.Row([strategy_dropdown, filter_row], spacing=10),
            ft.Text(
                _render_strategy_desc(state.strategy_desc) or I18n.get("screener_no_strategy_hint"),
                size=AppStyles.FONT_SIZE_BODY,
                color=_resolve_strategy_desc_color(state.strategy_desc_color),
                no_wrap=False,
            ),
            ft.Text(
                I18n.get(state.tier_hint) if state.tier_hint else "",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.WARNING,
                visible=state.tier_hint is not None,
                no_wrap=False,
            ),
        ],
        spacing=10,
        visible=is_realtime,
    )

    left_controls = ft.Column([title_row, realtime_controls], spacing=10)

    go_sync_btn = ft.TextButton(
        content=I18n.get(state.status_action_key) if state.status_action_key else "",
        icon=ft.Icons.SYNC,
        style=ft.ButtonStyle(color=AppColors.PRIMARY),
        height=30,
        visible=state.status_action_key is not None,
        on_click=safe_on_click(handlers["on_go_sync_click"]),
    )

    status_row = ft.Row(
        [
            ft.ProgressRing(visible=progress_visible, width=20, height=20, color=AppColors.ACCENT),
            ft.Text(status_text_value, color=status_text_color),
            go_sync_btn,
        ],
        alignment=ft.MainAxisAlignment.END,
        spacing=10,
    )

    if state.loading:
        run_btn = ft.Button(
            content=I18n.get("stop_screening"),
            icon=ft.Icons.STOP,
            on_click=safe_on_click(handlers["on_cancel_click_sync"]),
            disabled=False,
            style=AppStyles.primary_button(),
            height=45,
            visible=is_realtime,
        )
    else:
        run_btn = anchored(
            EIDS.SCREENER.RUN_BUTTON,
            ft.Button(
                content=I18n.get("run_screening"),
                icon=ft.Icons.PLAY_ARROW,
                on_click=safe_on_click(handlers["on_run_click_sync"]),
                disabled=run_disabled,
                style=AppStyles.primary_button(),
                height=45,
                visible=is_realtime,
            ),
        )
    export_btn = anchored(
        EIDS.SCREENER.EXPORT_CSV_BUTTON,
        ft.Button(
            content=I18n.get("screener_export"),
            icon=ft.Icons.DOWNLOAD,
            on_click=safe_on_click(handlers["on_export_csv_click"]),
            disabled=export_btn_disabled,
            style=AppStyles.outline_button(),
            height=45,
        ),
    )
    export_excel_btn = anchored(
        EIDS.SCREENER.EXPORT_EXCEL_BUTTON,
        ft.Button(
            content=I18n.get("data_export_excel"),
            icon=ft.Icons.TABLE_VIEW,
            on_click=safe_on_click(handlers["on_export_excel_click"]),
            disabled=export_btn_disabled,
            style=AppStyles.outline_button(),
            height=45,
        ),
    )
    backtest_btn = anchored(
        EIDS.SCREENER.RUN_BACKTEST_BUTTON,
        ft.Button(
            content=I18n.get("screener_run_backtest"),
            icon=ft.Icons.SCIENCE,
            on_click=safe_on_click(handlers["on_backtest_click_sync"]),
            disabled=run_disabled or not is_realtime,
            style=AppStyles.outline_button(),
            height=45,
            visible=is_realtime,
        ),
    )

    right_controls = ft.Column(
        [
            status_row,
            ft.Row([export_btn, export_excel_btn, run_btn], spacing=15, alignment=ft.MainAxisAlignment.END),
            ft.Row([backtest_btn], alignment=ft.MainAxisAlignment.END),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        horizontal_alignment=ft.CrossAxisAlignment.END,
    )

    return ft.Container(
        content=ft.Row(
            [ft.Container(content=left_controls, expand=True), right_controls],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        **AppStyles.dashboard_card(padding=AppStyles.SPACING_XL),
    )


def _build_screener_table_card(
    *,
    state: ScreenerState,
    formatted_rows: list[dict],
    vt_columns: list,
    section_formatted: dict[str, list[dict]],
    on_prev_page: typing.Callable[[ft.ControlEvent], None],
    on_next_page: typing.Callable[[ft.ControlEvent], None],
    on_page_size_change: typing.Callable[[ft.ControlEvent], None],
    on_section_change: typing.Callable[[ft.ControlEvent], None],
    on_virtual_sort: typing.Callable[[str, bool], None],
    on_row_click: typing.Callable[[dict], None],
    on_load_col_widths: typing.Callable[[], dict[str, int] | None],
    on_persist_col_widths: typing.Callable[[dict[str, int]], None],
    is_realtime: bool,
) -> ft.Container:
    """构建表格卡片区 (D7-3: AI 三分区 + 统一分页栏/空态).

    REALTIME 模式按 ai_status 渲染 recommended/excluded/failed 三分区;
    HISTORY 模式的历史记录无 ai_status 列 (ScreeningHistory 表无该字段),
    无法按 AI 三态分区, 渲染单一结果表, 避免全部行误入「分析失败」分区误导用户。
    """
    page_no = state.page_no
    total_pages = state.total_pages

    pagination_row = ft.Row(
        safe_controls(
            [
                ft.IconButton(
                    ft.Icons.CHEVRON_LEFT,
                    on_click=safe_on_click(on_prev_page),
                    icon_color=AppColors.PRIMARY,
                    disabled=page_no <= 1,
                    tooltip=I18n.get("screener_page_prev"),
                ),
                ft.Text(
                    I18n.get("screener_page_info").format(current=page_no, total=total_pages, count=state.total_items),
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.IconButton(
                    ft.Icons.CHEVRON_RIGHT,
                    on_click=safe_on_click(on_next_page),
                    icon_color=AppColors.PRIMARY,
                    disabled=page_no >= total_pages,
                    tooltip=I18n.get("screener_page_next"),
                ),
                ft.Container(width=20),
                ft.Dropdown(
                    label=I18n.get("screener_page_size"),
                    options=_build_page_size_options(),
                    value=str(state.page_size),
                    width=AppStyles.CONTROL_WIDTH_SM,
                    dense=True,
                    text_size=AppStyles.FONT_SIZE_BODY,
                    on_select=safe_on_select(on_page_size_change),
                ),
            ]
        ),
        alignment=ft.MainAxisAlignment.CENTER,
    )

    table_content: ft.Control
    if not formatted_rows and not state.loading:
        # UX-03 (空态区分): 有候选但筛选后无匹配时, VM 产出 empty_message (可操作原因),
        # 用于提示「可调低筛选条件」; 命中无数据场景 (empty_message 为 None) 时回落到
        # 默认「请先同步」上下文, 避免两种空态混淆。
        empty_context = (
            _render_status_message(state.empty_message)
            if state.empty_message is not None
            else I18n.get("screener_no_data_context")
        )
        table_content = ft.Column(
            [
                # UX-09 / MAJOR-01: 选股结果区顶部固定风险提示（含空态）。
                build_risk_disclaimer(compact=True),
                ft.Container(
                    content=EmptyState(
                        icon=ft.Icons.INBOX,
                        title=I18n.get("screener_no_results"),
                        message=empty_context,
                    ),
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        )
    else:
        if is_realtime and state.show_ai_sections:
            # MAJOR-06 (review 09-24): 分组先于分页 —— 页签标签承载**全量**分组计数
            # (state.ai_section_counts, 由 VM 对过滤后全量结果集统计), 修掉「标题只统计
            # 当前页」的失真; 页签一次只呈现一个分组 (活动分组由 VM 在组内分页), 三张表
            # 不再同屏挤占高度。VM 已保证三分组计数之和 = 全量条数 (非三分区值归入 failed),
            # 且当前页切片恒属活动分组, 故此处只消费 section_formatted[active]。
            body_rows = [
                _build_screener_sections_card(
                    section_counts=state.ai_section_counts,
                    active_section=state.ai_active_section,
                    section_rows=section_formatted.get(state.ai_active_section, []),
                    vt_columns=vt_columns,
                    sort_col=state.sort_column,
                    sort_asc=state.sort_ascending,
                    on_virtual_sort=on_virtual_sort,
                    on_row_click=on_row_click,
                    on_section_change=on_section_change,
                    on_load_col_widths=on_load_col_widths,
                    on_persist_col_widths=on_persist_col_widths,
                )
            ]
        else:
            # 单表渲染路径: HISTORY (历史记录来自 ScreeningHistory 表, 无 ai_status 列)
            # 或 REALTIME 下结果无 AI 介入 (非AI策略 enable_ai_analysis=False / AI 未执行,
            # VM 置 show_ai_sections=False), 两者均无法按 AI 三态分区, 渲染单一结果表 —
            # 避免把成功的数学筛选误标为「分析失败」(05-explainability-ux UX-02)。
            body_rows = [
                ft.Container(
                    content=PaginatedTable(
                        rows=formatted_rows,
                        columns=vt_columns,
                        sort_col=state.sort_column,
                        sort_asc=state.sort_ascending,
                        on_sort=on_virtual_sort,
                        on_row_click=on_row_click,
                        col_anchor=EIDS.SCREENER.column_header,
                        row_anchor=(
                            lambda row: EIDS.SCREENER.result_row(row["ts_code"]) if row.get("ts_code") else None
                        ),
                        on_row_detail=on_row_click,
                        detail_anchor=(
                            lambda row: EIDS.SCREENER.detail_button(row["ts_code"]) if row.get("ts_code") else None
                        ),
                        col_widths_key=_VT_COL_WIDTHS_KEY,
                        on_load_col_widths=on_load_col_widths,
                        on_persist_col_widths=on_persist_col_widths,
                    ),
                    expand=True,
                )
            ]
        table_content = ft.Column(
            # UX-09 / MAJOR-01: 选股结果区顶部固定风险提示。
            [
                build_risk_disclaimer(compact=True),
                *body_rows,
                ft.Divider(height=1, color=AppColors.DIVIDER),
                pagination_row,
            ],
            spacing=8,
            expand=True,
        )

    return ft.Container(
        content=table_content,
        **AppStyles.dashboard_card(padding=0),
        expand=2,
    )


# MINOR-06 (AI 流式卡片区强制回底): 判定「是否已滚离底部」的像素容差。
# Flet 1.0.x ``OnScrollEvent.extent_after`` 为滚动视口「之后」剩余的内容量(逻辑像素);
# 用户上滚后该值 > 容差, 视为已滚离底部(此时须停止自动跟随)。
_LOG_AT_BOTTOM_TOLERANCE_PX = 4.0


def _is_log_scrolled_away_from_bottom(extent_after: float) -> bool:
    """AI 流式卡片区是否已滚离底部 (MINOR-06)。

    ``extent_after`` 为滚动视口「之后」剩余的内容量(逻辑像素)。仅当用户主动上滚、
    且剩余可滚动内容超过容差时返回 ``True``; 位于底部(或内容不足以滚动, 此时
    ``extent_after`` 为 0)返回 ``False``, 保证自动跟随仅在贴底时启用。
    """
    return extent_after > _LOG_AT_BOTTOM_TOLERANCE_PX


def _build_screener_log_card(
    *,
    stream_cards: tuple[StreamCard, ...],
    stream_cards_truncated: bool,
    is_realtime: bool,
    ai_usage_summary: tuple[int, int, float, int, int] | None,
    on_retry_click: typing.Callable[[str], None],
    follow_latest: bool,
    rebuild_token: int,
    on_log_scroll: typing.Callable[[ft.OnScrollEvent], None],
    on_jump_to_latest: typing.Callable[[ft.ControlEvent], None],
) -> ft.Container:
    """构建 AI 流式分析卡片区 (仅 REALTIME 模式有效)."""
    log_column_controls: list[ft.Control] = [
        ft.Text(
            I18n.get("ai_analysis_report"),
            font_family="Roboto",
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        ),
        # UX-09 / MAJOR-01: AI 分析报告区固定风险提示。
        build_risk_disclaimer(compact=True),
    ]
    # AI-03(完整版): 本次选股实际消耗的 LLM 调用次数、token 总量与成本(元)。
    # 仅当确有消耗时渲染; None 表示当次未执行 AI 分析, 不展示 "消耗 0" 的误导信息。
    if ai_usage_summary is not None:
        calls, tokens, cost_cny, unpriced_calls, unpriced_tokens = ai_usage_summary
        log_column_controls.append(
            ft.Text(
                I18n.get("ai_usage_summary").format(
                    calls=calls,
                    tokens=tokens,
                    cost_cny=format(float(cost_cny), ".4f").rstrip("0").rstrip("."),
                ),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            )
        )
        # AI-01/R21: 存在不可计价调用时追加「成本未知」诚实说明，避免「无法计量」被误读为「零成本」。
        if unpriced_calls > 0:
            log_column_controls.append(
                ft.Text(
                    I18n.get("ai_usage_unpriced_note").format(
                        unpriced_calls=unpriced_calls,
                        unpriced_tokens=unpriced_tokens,
                    ),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.WARNING,
                    text_align=ft.TextAlign.CENTER,
                )
            )
    log_column_controls.append(
        ft.Container(
            content=ft.Column(
                [build_stream_card(c, on_retry_click) for c in stream_cards],
                expand=True,
                spacing=4,
                scroll=ft.ScrollMode.ALWAYS,
                # MINOR-06: 仅当用户仍停留在底部时才自动跟随新 token。用户上滚查看历史时
                # follow_latest 转 False → auto_scroll 关闭, 新 token 到达不再把视口强制
                # 拉回底部(on_scroll 依据 extent_after 判定是否已滚离底部)。
                auto_scroll=follow_latest,
                # 0ms 立即贴底: 默认 1s 缓动动画期间会产生「中途位置」的滚动事件而被误判为
                # 已滚离底部; Flet 1.0.x 亦推荐 token 级流式跟随用 0 时长保持紧贴。
                auto_scroll_animation=0,
                on_scroll=on_log_scroll,
                # rebuild_token 变化时按 key 重建滚动 Column, 使「回到最新」点击后立即贴底
                # (对齐 virtual_table.py 既有 key 重建模式; scroll_to 对声明式 Column
                #  ineffective, 见 docs/flet/project-differences.md §4.10)。
                key=f"screener_log_{rebuild_token}",
            ),
            border_radius=8,
            padding=5,
            expand=True,
        ),
    )
    if stream_cards_truncated:
        log_column_controls.append(
            ft.Text(
                I18n.get("ai_cards_truncated_hint").format(max=_MAX_LOG_CARDS),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            )
        )
    # MINOR-06: 用户滚离底部时提供「回到最新」入口 (点击恢复自动跟随并立即贴底)。
    if not follow_latest:
        log_column_controls.append(
            ft.Row(
                [
                    ft.TextButton(
                        content=I18n.get("ai_log_jump_to_latest"),
                        icon=ft.Icons.ARROW_DOWNWARD,
                        style=ft.ButtonStyle(color=AppColors.PRIMARY),
                        height=30,
                        tooltip=I18n.get("ai_log_jump_to_latest"),
                        on_click=safe_on_click(on_jump_to_latest),
                    )
                ],
                alignment=ft.MainAxisAlignment.END,
            )
        )
    return ft.Container(
        content=ft.Column(log_column_controls, spacing=5),
        expand=bool(stream_cards),
        padding=ft.Padding.only(top=10),
        visible=is_realtime,
    )


def _build_screener_main_body(
    *,
    is_realtime: bool,
    table_card: ft.Container,
    log_card: ft.Container,
    history_tree: typing.Any,
    strategy_stats: tuple[StrategyStatRow, ...],
    ai_attribution: tuple[AiAttributionRow, ...],
    params_sidebar: ft.Control | None,
    on_tree_item_click: typing.Callable[[str, str | None, str | None], None],
    on_load_more_history: typing.Callable[[ft.ControlEvent], None],
    on_load_width: typing.Callable[[], int | None],
    on_persist_width: typing.Callable[[int], None],
    on_load_params_width: typing.Callable[[], int | None] | None,
    on_persist_params_width: typing.Callable[[int], None] | None,
) -> ft.Control:
    """构建选股视图主体 (REALTIME 单栏/参数侧栏 vs HISTORY 分割器).

    MAJOR-07: REALTIME 模式在存在参数侧栏时返回「参数侧栏 + 结果区」左右分割器，
    使结果表独占右侧全高；无参数可渲染时退化为单栏（``params_sidebar is None``）。
    """
    right_content = ft.Column(
        [table_card, log_card] if is_realtime else [table_card],
        expand=True,
        spacing=10,
    )
    if is_realtime:
        if params_sidebar is None:
            return right_content
        return ResizableSplitter(
            left_content=params_sidebar,
            right_content=right_content,
            config_key="ui_splitter_screener_params",
            default_width=340,
            min_width=280,
            max_width=560,
            collapsible=True,
            collapsed=False,
            on_load_width=on_load_params_width,
            on_persist_width=on_persist_params_width,
        )

    return ResizableSplitter(
        left_content=build_history_tree(
            history_tree.rows,
            history_tree.offset,
            history_tree.has_more,
            strategy_stats,
            ai_attribution,
            on_tree_item_click,
            on_load_more_history,
        ),
        right_content=right_content,
        config_key="ui_splitter_screener_history",
        default_width=250,
        min_width=220,
        max_width=420,
        collapsible=True,
        collapsed=False,
        on_load_width=on_load_width,
        on_persist_width=on_persist_width,
    )


def _build_stock_detail_dialog(
    *,
    detail_dialog_data: typing.Any,
    data_processor: typing.Any,
    page: ft.Page | None,
    on_close: typing.Callable[[], None],
    on_add_to_watchlist: typing.Callable[[str, str], None],
    column_label_fn: typing.Callable[[str], str] | None = None,
    news_state: typing.Any = None,
    news_on_generate: typing.Callable[[], None] | None = None,
    news_on_retry: typing.Callable[[], None] | None = None,
    news_on_cancel: typing.Callable[[], None] | None = None,
) -> ft.Control | None:
    """按需构建股票详情对话框."""
    if detail_dialog_data is None:
        return None
    return StockDetailDialog(
        stock_data=detail_dialog_data,
        data_processor=data_processor,
        page=page,
        open_state=True,
        on_close=on_close,
        on_add_to_watchlist=on_add_to_watchlist,
        column_label_fn=column_label_fn,
        news_state=news_state,
        news_on_generate=news_on_generate,
        news_on_retry=news_on_retry,
        news_on_cancel=news_on_cancel,
    )


@ft.component
def ScreenerView(
    initial_strategy: str | None = None,
    active: bool = True,
    stock_filter_request: tuple[str, int] | None = None,
) -> ft.Container:
    """选股视图 (声明式)."""
    state, vm = use_viewmodel(factory=lambda: ScreenerViewModel())
    _wl_state, wl_vm = use_viewmodel(factory=lambda: WatchlistViewModel())
    nis_state, news_vm = use_viewmodel(factory=lambda: NewsInsightViewModel())

    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    detail_dialog_data, set_detail_dialog_data = ft.use_state(None)
    pending_strategy, set_pending_strategy = ft.use_state(initial_strategy)
    prompt_error, set_prompt_error = ft.use_state("")
    desc_timer_ref = ft.use_ref(lambda: None)
    table_memo_ref = ft.use_ref(lambda: typing.cast(tuple | None, None))
    file_picker = ft.use_ref(lambda: ft.FilePicker()).current

    # MINOR-06: AI 流式卡片区滚动跟随状态。属「纯 UI 交互态」(滚动位置), 非业务状态,
    # 故留在 View 层 (MVVM 状态归属: View 仅禁持业务状态/双源真相, 不禁交互态)。
    # follow_log_latest=True 表示用户仍在底部, 自动跟随新 token; 用户上滚后置 False。
    # log_rebuild_token 变化时按 key 重建滚动 Column, 使「回到最新」点击后立即贴底。
    follow_log_latest, set_follow_log_latest = ft.use_state(True)
    log_rebuild_token, set_log_rebuild_token = ft.use_state(0)

    # MINOR-06: 新一轮运行开始(state.loading 转 True; VM 于运行起点清空流式卡片)时恢复
    # 自动跟随, 避免沿用上一轮「用户已上滚」的旧交互态导致新一轮卡片不再跟随。
    ft.use_effect(lambda: set_follow_log_latest(True) if state.loading else None, dependencies=[state.loading])

    def _on_log_scroll(e: ft.OnScrollEvent) -> None:
        """MINOR-06: 依据滚动事件更新「是否仍在底部」, 仅在状态翻转时 set_state。"""
        scrolled_away = _is_log_scrolled_away_from_bottom(e.extent_after)
        if scrolled_away == follow_log_latest:
            set_follow_log_latest(not scrolled_away)

    def _on_jump_to_latest(e: ft.ControlEvent) -> None:
        """MINOR-06: 回到最新 — 恢复自动跟随并重建滚动区以立即贴底。"""
        UILogger.log_action("ScreenerView", "Click", "ai_log_jump_to_latest")
        set_follow_log_latest(True)
        set_log_rebuild_token(log_rebuild_token + 1)

    ft.use_effect(
        lambda: _sync_file_picker_service(_get_page(), file_picker, True) if active else None,
        dependencies=[active],
        cleanup=lambda: _sync_file_picker_service(_get_page(), file_picker, False),
    )

    ft.use_effect(
        lambda: vm.subscribe_task_manager() if active else None,
        dependencies=[active],
        cleanup=vm.unsubscribe_task_manager,
    )

    ft.use_effect(lambda: vm.load_strategies() if active else None, dependencies=[active])

    async def _execute_pending_strategy() -> None:
        if not active or not state.strategies_loaded or not pending_strategy:
            return
        key = pending_strategy
        set_pending_strategy(None)
        if not any(r.key == key for r in state.strategies_with_dep):
            logger.warning("[ScreenerView] Pending strategy %s not found.", key)
            return
        await _execute_pending_strategy_run(vm, key)

    ft.use_effect(_execute_pending_strategy, dependencies=[state.strategies_loaded, pending_strategy, active])

    ft.use_effect(
        lambda: (
            vm.set_stock_filter(stock_filter_request[0]) if stock_filter_request and stock_filter_request[0] else None
        ),
        dependencies=[stock_filter_request],
    )

    def _on_strategy_change(e: ft.ControlEvent) -> None:
        new_val = get_control_value(e.control, ft.Dropdown) if e and e.control else None
        UILogger.log_action("ScreenerView", "Select", f"strategy={new_val}")
        vm.select_strategy(new_val)
        vm.update_strategy_desc(new_val)
        if new_val:
            vm.init_strategy_params(new_val)
        else:
            vm.reset_strategy_params()

    async def _on_run_click(e: ft.ControlEvent) -> None:
        UILogger.log_action("ScreenerView", "Click", f"btn_run | strategy={state.selected_strategy}")
        if not state.selected_strategy:
            return
        try:
            await vm.run_strategy(
                state.selected_strategy,
                params=dict(vm.state.strategy_params),
                exclude_st=state.exclude_st,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[ScreenerView] Run strategy failed: %s", DataSanitizer.sanitize_error(exc), exc_info=True)

    def _on_run_click_sync(e: ft.ControlEvent) -> None:
        if page := _get_page():
            page.run_task(_on_run_click, e)

    async def _on_sort(col_id: str, new_asc: bool) -> None:
        try:
            await vm.sort_data(col_id, new_asc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ScreenerView] Sort failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    def _on_virtual_sort(col_id: str, new_asc: bool) -> None:
        if page := _get_page():
            page.run_task(_on_sort, col_id, new_asc)

    async def _do_export(format_: str) -> None:
        await _execute_screener_export(vm, file_picker, _get_page(), format_)

    def _on_export_csv_click(e: ft.ControlEvent) -> None:
        if page := _get_page():
            page.run_task(_do_export, "csv")

    def _on_export_excel_click(e: ft.ControlEvent) -> None:
        if page := _get_page():
            page.run_task(_do_export, "excel")

    def _on_stock_filter_change(e: ft.ControlEvent) -> None:
        UILogger.log_action("ScreenerView", "Input", "stock_filter")
        vm.set_stock_filter(get_control_value(e.control, ft.TextField) or "")

    def _on_exclude_st_change(e: ft.ControlEvent) -> None:
        UILogger.log_action("ScreenerView", "Toggle", "exclude_st")
        vm.set_exclude_st(bool(get_control_value(e.control, ft.Switch)))

    def _on_page_size_change(e: ft.ControlEvent) -> None:
        _handle_page_size_change(vm, e)

    async def _load_history_tree(append: bool) -> None:
        await _execute_load_history_tree(vm, _get_page(), append)

    def _on_load_more_history(e: ft.ControlEvent) -> None:
        if page := _get_page():
            page.run_task(_load_history_tree, True)

    async def _load_strategy_stats() -> None:
        await _execute_load_strategy_stats(vm, _get_page())

    async def _load_ai_attribution() -> None:
        await _execute_load_ai_attribution(vm, _get_page())

    def _on_mode_change(e: ft.ControlEvent) -> None:
        selected = get_control_attr(e.control, ft.SegmentedButton, "selected") if e and e.control else []
        if not selected:
            return
        new_mode = next(iter(selected))  # RUF015: 已判空，直接用 next(iter()) 取首元素
        UILogger.log_action("ScreenerView", "Toggle", f"mode={new_mode}")
        if new_mode == state.mode:
            return
        if new_mode == "HISTORY":
            vm.switch_to_history()
            if page := _get_page():
                page.run_task(_load_strategy_stats)
                page.run_task(_load_ai_attribution)
                page.run_task(_load_history_tree, False)
        else:
            vm.switch_to_realtime()

    def _on_section_change(e: ft.ControlEvent) -> None:
        # MAJOR-06: 分组页签切换 —— 分组选择属业务状态 (决定组内分页切片), 故交 VM 命令,
        # View 不落地任何本地状态 (§3.2 MVVM)。Flet 分段控件可能回传空 selected
        # (取消选择), 此类事件直接忽略, 保持当前分组不变。
        selected = get_control_attr(e.control, ft.SegmentedButton, "selected") if e and e.control else []
        if not selected:
            return
        section = next(iter(selected))
        UILogger.log_action("ScreenerView", "Switch", f"ai_section={section}")
        vm.select_ai_section(section)

    async def _load_history_for_date(trade_date: str, strategy_name: str | None, run_id: str | None) -> None:
        await _execute_load_history_for_date(vm, _get_page(), trade_date, strategy_name, run_id)

    def _on_tree_item_click(trade_date: str, strategy_name: str | None = None, run_id: str | None = None) -> None:
        if page := _get_page():
            page.run_task(_load_history_for_date, trade_date, strategy_name, run_id)

    def _on_row_click(row_data: dict) -> None:
        set_detail_dialog_data(typing.cast(typing.Any, row_data.get("_raw", row_data)))
        # #423 反查：详情数据可用 _raw（mappingproxy，行原始投影）展示；但 ts_code 需优先
        # 取 row_data 顶层（可见列），因 row.values/_raw 为只读 mappingproxy 不含 ts_code。
        src = (
            row_data
            if isinstance(row_data, dict) and row_data.get("ts_code")
            else (row_data.get("_raw") if isinstance(row_data.get("_raw"), dict) else None)
        )
        ts_code = typing.cast(str | None, src.get("ts_code") if isinstance(src, dict) else None)
        if ts_code:
            news_vm.select_stock(ts_code)

    async def _do_add_to_watchlist(ts_code: str, stock_name: str) -> None:
        await _execute_add_to_watchlist(wl_vm, _get_page(), ts_code, stock_name)

    def _on_add_to_watchlist(ts_code: str, stock_name: str) -> None:
        if page := _get_page():
            page.run_task(_do_add_to_watchlist, ts_code, stock_name)

    def _update_param(name: str, val: typing.Any) -> None:
        _handle_update_param(vm, name, val, prompt_error, set_prompt_error)

    def _on_slider_value_change(name: str, val: float) -> None:
        _update_param(name, val)
        if state.selected_strategy:
            _schedule_desc_update(desc_timer_ref, vm, state.selected_strategy)

    async def _do_restore_default_async(strat: str, ctrl_field: ft.TextField | None) -> None:
        await _execute_restore_default_prompt(vm, _get_page(), strat, _update_param)

    async def _do_save_prompt_async(strat: str) -> None:
        await _execute_save_prompt(vm, _get_page(), strat, set_prompt_error)

    def _on_restore_prompt(strat: str) -> None:
        if page := _get_page():
            page.run_task(_do_restore_default_async, strat, None)

    def _on_save_prompt(strat: str) -> None:
        if page := _get_page():
            page.run_task(_do_save_prompt_async, strat)

    status_text_value = _render_status_message(state.status_message)
    status_text_color = _resolve_status_color(state.status_color)
    vt_columns, formatted_rows = _resolve_table_data(state.current_page_rows, table_memo_ref, vm)
    # D7-3: 当前页三分区行 (recommended/excluded/failed) 复用同一可见列集格式化。
    # VM 已按 ai_status 拆分 (零丢失兜底), View 仅据此渲染, 不引入额外状态机 (§3.2)。
    _visible_cols = [c["id"] for c in vt_columns]
    # OSS B1: 三分区行格式化与无关 state 变更（mode/loading 等）解耦 —— ai_*_rows 为
    # frozen 元组、_visible_cols 由 vt_columns 派生（经 table_memo_ref 已稳化），
    # 数据未变时 use_memo 跳过重建。deps 含 locale：_format_cell_value 经 I18n.get
    # 渲染 prediction_result 文案与成交量单位（unit_yi/unit_wan），与主表
    # _resolve_table_data 的 locale 纬度保持一致，防三分区与主表跨 locale 不一致。
    section_formatted = ft.use_memo(
        lambda: {
            "recommended": _format_rows(state.ai_recommended_rows, _visible_cols),
            "excluded": _format_rows(state.ai_excluded_rows, _visible_cols),
            "failed": _format_rows(state.ai_failed_rows, _visible_cols),
        },
        dependencies=[
            state.ai_recommended_rows,
            state.ai_excluded_rows,
            state.ai_failed_rows,
            _visible_cols,
            get_observable_state().locale,
        ],
    )

    progress_visible = state.loading
    run_disabled = state.loading or state.is_retrying or not state.selected_strategy
    # UX-09 MINOR-07: 数值参数越界/非法时禁用「运行选股」; 校验在渲染期派生 (不持有状态),
    # 切换策略/locale 后自动重算。参数定义与判定同 build_param_control 同源。
    param_errors: dict[str, Message] = (
        _validate_strategy_params(list(vm.get_strategy_params(state.selected_strategy)), dict(state.strategy_params))
        if state.selected_strategy
        else {}
    )
    run_disabled = run_disabled or bool(param_errors)
    export_btn_disabled = not vm.has_export_data
    is_realtime = state.mode == "REALTIME"

    handlers: dict[str, typing.Any] = {
        "on_mode_change": _on_mode_change,
        "on_strategy_change": _on_strategy_change,
        "on_stock_filter_change": _on_stock_filter_change,
        "on_exclude_st_change": _on_exclude_st_change,
        "on_run_click_sync": _on_run_click_sync,
        "on_cancel_click_sync": lambda _: vm.cancel_strategy(),
        "on_slider_value_change": _on_slider_value_change,
        "on_update_param": _update_param,
        "on_save_prompt": _on_save_prompt,
        "on_restore_prompt": _on_restore_prompt,
        "on_go_sync_click": lambda _: _handle_go_sync(_get_page()),
        "on_export_csv_click": _on_export_csv_click,
        "on_export_excel_click": _on_export_excel_click,
        "on_backtest_click_sync": lambda _: _handle_backtest_jump(state, vm, _get_page()),
    }

    control_card = _build_screener_control_card(
        state,
        vm,
        status_text_value=status_text_value,
        status_text_color=status_text_color,
        progress_visible=progress_visible,
        run_disabled=run_disabled,
        export_btn_disabled=export_btn_disabled,
        handlers=handlers,
    )

    # MAJOR-07: 参数区从控制卡移入左侧可调宽度侧栏（复用 ResizableSplitter + 既有
    # ConfigHandler 持久化通道，键 ui_splitter_screener_params）。
    params_sidebar = _build_screener_params_sidebar(
        state, vm, handlers=handlers, prompt_error=prompt_error, param_errors=param_errors
    )

    table_card = _build_screener_table_card(
        state=state,
        formatted_rows=formatted_rows,
        vt_columns=vt_columns,
        section_formatted=section_formatted,
        on_prev_page=lambda _: vm.change_page(-1),
        on_next_page=lambda _: vm.change_page(1),
        on_page_size_change=_on_page_size_change,
        on_section_change=safe_on_change(_on_section_change),
        on_virtual_sort=_on_virtual_sort,
        on_row_click=_on_row_click,
        on_load_col_widths=lambda: vm.get_col_widths(_VT_COL_WIDTHS_KEY),
        on_persist_col_widths=lambda widths: vm.persist_col_widths(_VT_COL_WIDTHS_KEY, widths),
        is_realtime=is_realtime,
    )

    log_card = _build_screener_log_card(
        stream_cards=state.stream_cards,
        stream_cards_truncated=state.stream_cards_truncated,
        is_realtime=is_realtime,
        ai_usage_summary=state.ai_usage_summary,
        on_retry_click=lambda name: vm.schedule_retry(name),
        follow_latest=follow_log_latest,
        rebuild_token=log_rebuild_token,
        on_log_scroll=_on_log_scroll,
        on_jump_to_latest=_on_jump_to_latest,
    )

    main_body = _build_screener_main_body(
        is_realtime=is_realtime,
        table_card=table_card,
        log_card=log_card,
        history_tree=state.history_tree,
        strategy_stats=state.strategy_stats,
        ai_attribution=state.ai_attribution,
        params_sidebar=params_sidebar,
        on_tree_item_click=_on_tree_item_click,
        on_load_more_history=_on_load_more_history,
        on_load_width=lambda: int(vm.get_splitter_width("ui_splitter_screener_history", 250)),
        on_persist_width=lambda w: vm.persist_splitter_width("ui_splitter_screener_history", w),
        on_load_params_width=lambda: int(vm.get_splitter_width("ui_splitter_screener_params", 340)),
        on_persist_params_width=lambda w: vm.persist_splitter_width("ui_splitter_screener_params", w),
    )

    dialog_control = _build_stock_detail_dialog(
        detail_dialog_data=detail_dialog_data,
        data_processor=vm.data_processor,
        page=_get_page(),
        on_close=lambda: set_detail_dialog_data(None),
        on_add_to_watchlist=_on_add_to_watchlist,
        column_label_fn=lambda col: vm.get_column_alias("screening_history", col),
        news_state=nis_state,
        news_on_generate=news_vm.generate,
        news_on_retry=news_vm.retry,
        news_on_cancel=news_vm.cancel,
    )

    # SEC-01 gap3: 运行时 AI 外发确认对话框。pending_egress_ack_preview 非空时渲染，
    # 用户「同意」调 vm.resolve_ai_egress_ack(True) 继续 AI；「拒绝」落 False 跳过。
    egress_ack_dialog = (
        ConfirmDialog(
            open_state=bool(state.pending_egress_ack_preview),
            title=I18n.get("ai_external_acknowledgment_dialog_title"),
            body=f"{I18n.get('ai_external_acknowledgment_checkbox')}\n\n{state.pending_egress_ack_preview}",
            on_confirm=lambda: vm.resolve_ai_egress_ack(True),
            on_cancel=lambda: vm.resolve_ai_egress_ack(False),
            confirm_text=I18n.get("ai_external_acknowledgment_dialog_confirm"),
            cancel_text=I18n.get("ai_external_acknowledgment_dialog_cancel"),
        )
        if state.pending_egress_ack_preview
        else None
    )

    # AI-01: 「本月存在不可计价调用」保守确认对话框。pending_unpriced_ack=True 时渲染，
    # 用户「继续」调 vm.resolve_ai_unpriced_ack(True) 放行（进程级一次，本进程不再弹）；
    # 「取消」落 False 回退，跳过本次云端 AI。
    unpriced_ack_dialog = (
        ConfirmDialog(
            open_state=bool(state.pending_unpriced_ack),
            title=I18n.get("ai_budget_unpriced_dialog_title"),
            body=I18n.get("ai_budget_unpriced_dialog_body"),
            on_confirm=lambda: vm.resolve_ai_unpriced_ack(True),
            on_cancel=lambda: vm.resolve_ai_unpriced_ack(False),
            confirm_text=I18n.get("ai_budget_unpriced_dialog_confirm"),
            cancel_text=I18n.get("ai_budget_unpriced_dialog_cancel"),
        )
        if state.pending_unpriced_ack
        else None
    )

    return ft.Container(
        content=ft.Column(
            [
                control_card,
                *([banner] if (banner := _build_screener_warning_banner(state.warnings)) is not None else []),
                main_body,
                *([dialog_control] if dialog_control is not None else []),
                *([egress_ack_dialog] if egress_ack_dialog is not None else []),
                *([unpriced_ack_dialog] if unpriced_ack_dialog is not None else []),
            ],
            expand=True,
            spacing=15,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
        expand=True,
    )


def _parse_num(val):
    """尝试解析数值, 失败时返回原字符串。"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return val
