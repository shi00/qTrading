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
import os
import typing
from decimal import Decimal

import flet as ft
import pandas as pd

from ui.components._markdown_safe import safe_open_url
from ui.components.flet_type_helpers import (
    get_control_attr,
    get_control_value,
    safe_controls,
    safe_on_change,
    safe_on_click,
    safe_on_select,
)
from ui.components.resizable_splitter import ResizableSplitter
from ui.components.slider_input import SliderInput
from ui.components.state_views import EmptyState
from ui.components.stock_detail_dialog import StockDetailDialog
from ui.components.toast_manager import open_export_folder
from ui.components.virtual_table import PaginatedTable
from ui.hooks import use_viewmodel
from ui.i18n import I18n, translate_strategy_name, get_observable_state
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.theme import AppColors, AppStyles
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.viewmodels import Message
from ui.viewmodels.screener_view_model import _MAX_LOG_CARDS, ScreenerViewModel, StreamCard
from ui.viewmodels.backtest_view_model import set_pending_prefill
from ui.viewmodels.watchlist_view_model import WatchlistViewModel
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

# R.2.6.3: VM 产出语义键 (error/warning/success/info), View 映射为 AppColors 实际颜色值 (§3.2 VM 不感知 UI 颜色).
_STATUS_COLOR_MAP = {
    "error": AppColors.ERROR,
    "warning": AppColors.WARNING,
    "success": AppColors.SUCCESS,
    "info": AppColors.INFO,
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
        "t1_price",
        "t5_price",
        "params_snapshot",
    }
)

_COLUMN_WIDTHS = {
    "ts_code": 100,
    "name": 120,
    "ai_score": 80,
    "ai_reason": 250,
    "confidence": 70,
    "industry": 120,
    "strategy_name": 120,
    "prediction_result": 80,
    "t1_pct": 80,
    "t5_pct": 80,
    "alpha": 80,
}

_VOLUME_COLS = frozenset({"vol", "volume", "amount"})

_DATE_COLS = frozenset({"list_date", "trade_date"})

# Task 4.3 (FR-UX-005): 复盘涨幅/超额收益列, 带符号格式化 (+1.23 / -1.23)
_PCT_COLS = frozenset({"t1_pct", "t5_pct", "alpha"})


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
            params[k[:-4]] = I18n.get(params[k])
            del params[k]
    return I18n.get(msg.key, **params)


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
    if col in _DATE_COLS:
        if isinstance(val, (datetime.date, datetime.datetime)):
            return val.strftime("%Y-%m-%d")
        val_str = str(val).split(".")[0]
        if len(val_str) == 8 and val_str.isdigit():
            return f"{val_str[:4]}-{val_str[4:6]}-{val_str[6:]}"
        return str(val)
    if isinstance(val, (float, int)) and col not in ("ts_code", "symbol"):
        if col in _VOLUME_COLS:
            if val > 1_000_000_000:
                return f"{val / 1_000_000_000:.2f}{I18n.get('unit_yi')}"
            if val > 10_000:
                return f"{val / 10_000:.2f}{I18n.get('unit_wan')}"
            return f"{val:,.0f}"
        if col in _PCT_COLS:  # Task 4.3 (FR-UX-005): 带符号格式化
            val_f = float(val)
            sign = "+" if val_f > 0 else ""
            return f"{sign}{val_f:.2f}"
        if isinstance(val, (float, Decimal)):
            return f"{val:.2f}"
    return str(val)


def _build_table_data(df: pd.DataFrame, vm: ScreenerViewModel) -> tuple[list, list]:
    vt_columns = []
    visible_cols = []
    for col in df.columns:
        if col in _HIDDEN_COLS:
            continue
        visible_cols.append(col)
        width = _COLUMN_WIDTHS.get(col, 80)
        label = vm.get_column_alias("screening_history", col)
        vt_columns.append({"id": col, "label": label, "width": width})

    raw_records = df.to_dict("records")  # type: ignore[call-overload]
    formatted_rows: list[dict[str, typing.Any]] = []
    for raw in raw_records:
        fmt: dict[str, typing.Any] = {col: _format_cell_value(col, raw[col]) for col in visible_cols}
        fmt["_raw"] = raw  # #423: 携带原始行引用, 供 _on_row_click 反查 (替代 ts_code 字典反查, 避免同名多行覆盖)
        formatted_rows.append(fmt)
    return vt_columns, formatted_rows


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _safe_show_toast(
    page: ft.Page,
    msg: str,
    msg_type: str = "info",
    action_text: str | None = None,
    on_action: typing.Callable[[], None] | None = None,
) -> None:
    """page.show_toast 是 main.py 动态挂载的，ft.Page 类型存根未声明。

    P2-10: action_text/on_action 透传 (导出成功"打开文件夹"按钮)。
    """
    show_toast = getattr(page, "show_toast", None)
    if show_toast is not None:
        show_toast(msg, msg_type, action_text=action_text, on_action=on_action)


def _build_strategy_options(strategies_with_dep: dict, strategy_mgr) -> list[ft.dropdown.Option]:
    """构建策略下拉框选项 (翻译策略名 + missing_apis 标记)。"""
    options = []
    for key, info in strategies_with_dep.items():
        strategy_obj = strategy_mgr.get_strategy(key)
        if strategy_obj and hasattr(strategy_obj, "name_key"):
            name = I18n.get(strategy_obj.name_key)
        else:
            name = info["name"]
        if info.get("missing_apis"):
            name = f"{name} (!)"  # P2-7: 警告 emoji 改为文本符号, 避免 UI 依赖 emoji 字体
        options.append(ft.dropdown.Option(key, name))
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


def _format_history_date(date_str) -> tuple[str, str]:
    """格式化历史树日期: 返回 (display_date, internal_key)。"""
    if isinstance(date_str, (datetime.date, datetime.datetime)):
        display = date_str.strftime("%Y-%m-%d")
        key = display
    else:
        s = str(date_str)
        display = f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else s
        key = s
    return display, key


@ft.component
def ScreenerView(
    initial_strategy: str | None = None,
    active: bool = True,
) -> ft.Container:
    """选股视图 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - ``use_viewmodel(factory=lambda: ScreenerViewModel())`` 内部模式实例化
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - FilePicker 通过 ``use_ref`` + ``use_effect`` 注册到 ``page.services``
    - PubSub (TaskManager) 通过 ``use_effect(setup, [], cleanup=cleanup)`` 订阅/退订
    - LLM 流式 Markdown 卡片从 ``state.stream_cards`` 渲染 (VM 侧节流 flush, state-driven)
    - page 访问用 ``ft.context.page`` (try/except 守卫)

    Args:
        initial_strategy: 深度链接策略 key (可选, 策略加载后自动执行)
    """
    # --- VM (内部模式: hook 实例化 + 卸载时 dispose) ---
    state, vm = use_viewmodel(factory=lambda: ScreenerViewModel())
    # FR-UX-004, Task 4.2: 关注列表 VM (详情对话框「加入关注」按钮消费)
    _wl_state, wl_vm = use_viewmodel(factory=lambda: WatchlistViewModel())

    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 本地 UI 状态 (R.2.2: selected_strategy/tier_hint 已迁入 VM state;
    #                     R.2.4: mode/page_size 已迁入 VM state;
    #                     R.2.6.1: strategies_loaded/strategy_options 已迁入 VM state;
    #                     R.2.6.2: strategy_desc/strategy_desc_color 已迁入 VM state;
    #                     R.2.6.3: status_msg/status_color 已迁入 VM state;
    #                     Task 3.2: progress_visible/run_disabled/export_disabled 改为派生;
    #                               历史树 rows/offset/has_more/loading 迁入 VM state.history_tree) ---
    detail_dialog_data, set_detail_dialog_data = ft.use_state(None)
    pending_strategy, set_pending_strategy = ft.use_state(initial_strategy)
    # params_version 触发重渲染; params_ref 持久化参数值 (避免 stale closure)
    params_ref = ft.use_ref(lambda: {})
    _params_version, bump_params = ft.use_state(0)

    # --- FilePicker 生命周期 (use_ref 持有 + use_effect 注册/移除) ---
    file_picker = ft.use_ref(lambda: ft.FilePicker()).current

    def _setup_file_picker() -> None:
        if not active:
            return
        page = _get_page()
        if page is not None and file_picker is not None and file_picker not in page.services:
            page.services.append(file_picker)

    def _cleanup_file_picker() -> None:
        page = _get_page()
        if page is not None and file_picker in page.services:
            page.services.remove(file_picker)

    ft.use_effect(_setup_file_picker, dependencies=[active], cleanup=_cleanup_file_picker)

    # --- PubSub (TaskManager) 订阅/退订 ---

    def _setup_task_manager() -> None:
        if not active:
            return
        vm.subscribe_task_manager()

    def _cleanup_task_manager() -> None:
        vm.unsubscribe_task_manager()

    ft.use_effect(_setup_task_manager, dependencies=[active], cleanup=_cleanup_task_manager)

    # --- 策略加载 (mount 时执行一次, R.2.6.1: VM.load_strategies 内聚) ---

    async def _load_strategies_async() -> None:
        if not active:
            return
        vm.load_strategies()

    ft.use_effect(_load_strategies_async, dependencies=[active])

    # --- 深度链接 (策略加载后执行 pending_strategy) ---

    async def _execute_pending_strategy() -> None:
        if not active:
            return
        if not state.strategies_loaded or not pending_strategy:
            return
        key = pending_strategy
        set_pending_strategy(None)
        # 验证策略存在 (R.2.6.1: 从 state.strategies_with_dep 检查)
        if key not in state.strategies_with_dep:
            logger.warning("[ScreenerView] Pending strategy %s not found.", key)
            return
        # 选中策略 (R.2.2: vm.select_strategy 内聚 selected_strategy + tier_hint 到 VM state)
        vm.select_strategy(key)
        # R.2.6.2: vm.update_strategy_desc 内聚 strategy_desc/color 到 VM state
        vm.update_strategy_desc(key)
        # 默认参数
        params_def = vm.get_strategy_params(key)
        for p in params_def:
            if p.get("name") == "ai_system_prompt":
                typing.cast(dict, params_ref.current)[p["name"]] = vm.get_base_prompt(key) or p.get("default", "")
            else:
                typing.cast(dict, params_ref.current)[p["name"]] = p.get("default")
        bump_params(_params_version + 1)
        # 执行 (VM 在 run_strategy 开始时自动清空 stream_cards)
        try:
            await vm.run_strategy(key, params=dict(params_ref.current or {}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "[ScreenerView] Pending strategy execution failed: %s", DataSanitizer.sanitize_error(e), exc_info=True
            )

    ft.use_effect(_execute_pending_strategy, dependencies=[state.strategies_loaded, pending_strategy, active])

    # --- 事件 handler ---

    def _on_strategy_change(e: ft.ControlEvent) -> None:
        new_val = get_control_value(e.control, ft.Dropdown) if e and e.control else None
        UILogger.log_action("ScreenerView", "Select", f"strategy={new_val}")
        # R.2.2: vm.select_strategy 内聚 selected_strategy + tier_hint 到 VM state
        # Task 3.2: run_disabled 改为派生 (state.loading or not state.selected_strategy)
        vm.select_strategy(new_val)
        # R.2.6.2: vm.update_strategy_desc 内聚 strategy_desc/color 到 VM state
        vm.update_strategy_desc(new_val)
        # 初始化参数默认值
        if new_val:
            params_def = vm.get_strategy_params(new_val)
            for p in params_def:
                if p.get("name") == "ai_system_prompt":
                    typing.cast(dict, params_ref.current)[p["name"]] = vm.get_base_prompt(new_val) or p.get(
                        "default", ""
                    )
                else:
                    typing.cast(dict, params_ref.current)[p["name"]] = p.get("default")
            bump_params(_params_version + 1)

    async def _on_run_click(e: ft.ControlEvent) -> None:
        UILogger.log_action("ScreenerView", "Click", f"btn_run | strategy={state.selected_strategy}")
        if not state.selected_strategy:
            return
        # Task 3.2: run_disabled 改为派生, VM run_strategy 内部设置 loading=True 自动禁用
        try:
            await vm.run_strategy(state.selected_strategy, params=dict(params_ref.current or {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[ScreenerView] Run strategy failed: %s", DataSanitizer.sanitize_error(exc), exc_info=True)

    def _on_run_click_sync(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_run_click, e)

    def _on_cancel_click_sync(e: ft.ControlEvent) -> None:
        # Task 3.2: cancel_strategy 是线程安全的 (call_soon_threadsafe), 可直接在 UI 线程调用
        vm.cancel_strategy()

    def _on_backtest_click_sync(e: ft.ControlEvent) -> None:
        """Task 8.3: 选股→回测参数透传 — 暂存 strategy_key + params 并跳转回测页."""
        UILogger.log_action("ScreenerView", "Click", "btn_jump_backtest")
        if not state.selected_strategy:
            return
        set_pending_prefill(state.selected_strategy, params=dict(params_ref.current or {}))
        page = _get_page()
        if page is not None:
            page.pubsub.send_all_on_topic(TOPIC_NAVIGATE, "backtest")

    async def _on_sort(col_id: str, new_asc: bool) -> None:
        try:
            await vm.sort_data(col_id, new_asc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ScreenerView] Sort failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    def _on_virtual_sort(col_id: str, new_asc: bool) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_sort, col_id, new_asc)

    async def _do_export(format_: str) -> None:
        """Export current results to CSV or Excel.

        Args:
            format_: "csv" or "excel"
        """
        UILogger.log_action("ScreenerView", "Click", f"btn_export_{format_}")
        df = vm.get_export_data()
        if df is None:
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("data_export_no_data"), "error")
            return
        timestamp = get_now().strftime("%Y%m%d_%H%M%S")
        ext = "csv" if format_ == "csv" else "xlsx"
        default_filename = f"screener_results_{timestamp}.{ext}"
        # Flet 0.86+ Web 模式: save_file 必须传 src_bytes, 否则抛 ValueError.
        # Flet 用 Blob + <a download>.click() 触发浏览器下载 (Playwright 可捕获 download 事件).
        # 桌面端: save_file 打开原生对话框返回路径, VM 写文件 (原逻辑保留).
        page = _get_page()
        is_web = page is not None and page.web
        if is_web:
            try:
                # R16: df.to_csv/to_excel 是 CPU 密集操作, 通过 VM 方法 offload 到 CPU 线程池
                src_bytes, error = await vm.export_results_bytes(format_)
                if src_bytes is None:
                    if page is not None:
                        _safe_show_toast(page, I18n.get("data_export_fail"), "error")
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
                    _safe_show_toast(page, I18n.get("data_export_success", file=default_filename), "success")
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logger.error("[ScreenerView] Export | Failed: %s", DataSanitizer.sanitize_error(ex))
                if page is not None:
                    _safe_show_toast(page, I18n.get("data_export_fail"), "error")
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
        # Task 3.2: export_disabled 改为派生 (state.total_items == 0), 不再手动 set
        try:
            if format_ == "csv":
                path, error = await vm.export_results(filepath)
            else:
                path, error = await vm.export_results_excel(filepath)
            page = _get_page()
            if path:
                filename = os.path.basename(filepath)
                if page is not None:
                    # P2-10: 导出成功 toast 附"打开文件夹" action (仅桌面端, Web 端走浏览器下载无此需求)
                    _safe_show_toast(
                        page,
                        I18n.get("data_export_success", file=filename),
                        "success",
                        action_text=I18n.get("data_export_open_folder"),
                        on_action=lambda: page.run_task(open_export_folder, filepath),
                    )
            elif page is not None:
                _safe_show_toast(page, I18n.get("data_export_fail"), "error")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] Export | Failed: %s", DataSanitizer.sanitize_error(ex))
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("data_export_fail"), "error")

    def _on_export_csv_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_export, "csv")

    def _on_export_excel_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_export, "excel")

    def _on_page_size_change(e: ft.ControlEvent) -> None:
        try:
            new_size = int(get_control_value(e.control, ft.Dropdown) if e and e.control else 50)
            vm.change_page_size(new_size)
        except (ValueError, TypeError):
            pass

    def _on_prev_page(e: ft.ControlEvent) -> None:
        vm.change_page(-1)

    def _on_next_page(e: ft.ControlEvent) -> None:
        vm.change_page(1)

    def _on_mode_change(e: ft.ControlEvent) -> None:
        selected = get_control_attr(e.control, ft.SegmentedButton, "selected") if e and e.control else []
        if not selected:
            return
        new_mode = list(selected)[0]
        UILogger.log_action("ScreenerView", "Toggle", f"mode={new_mode}")
        if new_mode == state.mode:
            return
        if new_mode == "HISTORY":
            vm.switch_to_history()
            # Task 3.2: history_tree state 由 VM switch_to_history 重置, View 仅触发加载
            page = _get_page()
            if page is not None:
                page.run_task(_load_history_tree, False)
        else:
            vm.switch_to_realtime()

    async def _load_history_tree(append: bool) -> None:
        """加载历史树数据 (Task 3.2: VM 更新 state.history_tree, View 不再处理 items)."""
        try:
            await vm.load_history_tree(append=append)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] History tree load failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("screener_load_failed"), "error")

    def _on_load_more_history(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_load_history_tree, True)

    async def _load_history_for_date(trade_date: str, strategy_name: str | None, run_id: str | None) -> None:
        # Task 3.2: progress_visible 改为派生 (state.loading), VM load_history_data 内聚 loading 管理
        if isinstance(trade_date, (datetime.date, datetime.datetime)):
            display = trade_date.strftime("%Y-%m-%d")
            trade_date = display
        else:
            ts = str(trade_date)
            display = f"{ts[:4]}-{ts[4:6]}-{ts[6:]}" if len(ts) == 8 and ts.isdigit() else ts
        # R.2.6.3: 传 raw strategy_name 给 VM, View 渲染时翻译 (§3.2 VM 不感知 locale)
        vm.set_history_viewing_status(display, strategy_name=strategy_name, run_id=run_id)
        try:
            await vm.load_history_data(trade_date, strategy_name, run_id)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(
                "[ScreenerView] Load history for date failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
            )
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("screener_load_failed"), "error")

    def _on_tree_item_click(trade_date: str, strategy_name: str | None = None, run_id: str | None = None) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_load_history_for_date, trade_date, strategy_name, run_id)

    def _on_row_click(row_data: dict) -> None:
        """行点击 → 打开详情对话框。

        #423: 通过 formatted_row 携带的 _raw 引用反查原始行 (含隐藏列),
        避免 ts_code 同名多行时字典反查错行. _raw 缺失时 fallback 到 row_data 本身.
        """
        raw_data = row_data.get("_raw", row_data)
        set_detail_dialog_data(typing.cast(typing.Any, raw_data))

    def _on_detail_close() -> None:
        set_detail_dialog_data(None)

    # FR-UX-004, Task 4.2: 加入关注 (详情对话框按钮 → WatchlistViewModel)
    async def _do_add_to_watchlist(ts_code: str, stock_name: str) -> None:
        try:
            await wl_vm.add_to_watchlist(ts_code, stock_name)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("watchlist_added"), "success")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] Add to watchlist failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("watchlist_add_failed"), "error")

    def _on_add_to_watchlist(ts_code: str, stock_name: str) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_add_to_watchlist, ts_code, stock_name)

    # --- 参数面板 helper ---

    def _update_param(name: str, value) -> None:
        params_ref.current = {**(params_ref.current or {}), name: value}
        bump_params(_params_version + 1)

    def _on_slider_value_change(name: str, val: float) -> None:
        _update_param(name, val)
        # R.2.6.2: 动态更新策略描述 (vm.update_strategy_desc 用当前 params 重算 desc+color)
        if state.selected_strategy:
            vm.update_strategy_desc(state.selected_strategy, params=dict(params_ref.current or {}))

    async def _do_restore_default_async(strat: str, ctrl_field: ft.TextField | None) -> None:
        # Phase 3.3: ConfigHandler.set_strategy_prompt + base_prompt 读取下沉到
        # vm.reset_strategy_prompt (返回 base_prompt 字符串), View 仅更新 UI state + 展示反馈.
        try:
            new_val = await vm.reset_strategy_prompt(strat)
            _update_param("ai_system_prompt", new_val)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("ai_settings_restored"), "info")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error(
                "[ScreenerView] Restore default prompt failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
            )
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("sys_snack_save_err"), "error")

    async def _do_save_prompt_async(strat: str) -> None:
        # Phase 3.3: validate_prompt + ConfigHandler.set_strategy_prompt 下沉到
        # vm.save_strategy_prompt (返回 (success, error_key)), View 仅展示反馈.
        try:
            prompt_val = (params_ref.current or {}).get("ai_system_prompt", "") or ""
            success, error_key = await vm.save_strategy_prompt(strat, prompt_val)
            page = _get_page()
            if page is None:
                return
            if success:
                UILogger.log_action("ScreenerView", "SavePrompt", f"strategy={strat}")
                _safe_show_toast(page, I18n.get("ai_settings_saved"), "success")
            else:
                from utils.prompt_guard import MAX_PROMPT_LENGTH

                assert error_key is not None  # validate_prompt 失败时返回 (False, warning)
                msg = I18n.get(error_key, error_key)
                if error_key == "prompt_err_length":
                    msg = I18n.get("prompt_err_length").format(max=MAX_PROMPT_LENGTH)
                _safe_show_toast(page, msg, "warning")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] Save prompt failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("sys_snack_save_err"), "error")

    def _on_restore_prompt(strat: str) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_restore_default_async, strat, None)

    def _on_save_prompt(strat: str) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_prompt_async, strat)

    # --- 派生渲染数据 ---

    # 状态栏: 从 VM state.status_message 渲染 (R.2.6.3: 单源真相, §3.2 VM 只产出 i18n key + params)
    status_text_value = _render_status_message(state.status_message)
    status_text_color = _STATUS_COLOR_MAP.get(state.status_color, AppColors.TEXT_SECONDARY)

    # 表格数据: 从 VM 读取当前页
    df = vm.get_current_page_data()
    if df is not None and not df.empty:
        vt_columns, formatted_rows = _build_table_data(df, vm)
    else:
        vt_columns = []
        formatted_rows = []

    # 分页信息
    page_no = state.page_no
    total_pages = state.total_pages
    total_items = state.total_items

    # Task 3.2: 派生状态 (单源真相: state.loading / state.selected_strategy / state.total_items)
    progress_visible = state.loading
    run_disabled = state.loading or state.is_retrying or not state.selected_strategy
    # 导出按钮: 有数据时启用
    export_btn_disabled = total_items == 0

    # --- 构建参数面板 ---

    def _build_param_control(p: dict) -> ft.Control | None:
        """构建单个参数控件。"""
        label = I18n.get(p.get("label_key", p["name"]))
        p_type = p.get("type", "number")
        p_name = p["name"]

        if p_type == "slider":
            min_val = p.get("min", 0)
            max_val = p.get("max", 100)
            default = p.get("default", min_val)
            step = p.get("step", 1)
            current_val = (params_ref.current or {}).get(p_name, default)
            return SliderInput(
                label=label,
                value=float(current_val),
                min_val=float(min_val),
                max_val=float(max_val),
                step=float(step),
                on_change=lambda v, n=p_name: _on_slider_value_change(n, v),
                # 固定宽度: 参数面板 ft.Row(wrap=True) 需要可测量宽度的子控件才能正确换行布局。
                # width=None + Slider(expand=True) 导致宽度不确定 → 控件独占一行 →
                # 参数面板变高 → table_card 视口高度被挤压到 0 → 表格行不生成语义节点 (PR #373 回归)。
                width=AppStyles.CONTROL_WIDTH_MD,
            )

        if p_type == "number":
            current_val = (params_ref.current or {}).get(p_name, p.get("default", ""))
            return ft.TextField(
                label=label,
                value=str(current_val),
                keyboard_type=ft.KeyboardType.NUMBER,
                dense=True,
                border_color=AppColors.DIVIDER,
                focused_border_color=AppColors.PRIMARY,
                text_size=AppStyles.FONT_SIZE_BODY,
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                width=AppStyles.CONTROL_WIDTH_MD,
                on_change=lambda e, n=p_name: _update_param(n, _parse_num(e.control.value if e and e.control else "")),
            )

        if p_type == "dropdown":
            options = p.get("options", [])
            current_val = (params_ref.current or {}).get(p_name, p.get("default", ""))
            return ft.Dropdown(
                label=label,
                value=str(current_val),
                options=[ft.dropdown.Option(str(o)) for o in options],
                dense=True,
                border_color=AppColors.DIVIDER,
                focused_border_color=AppColors.PRIMARY,
                text_size=AppStyles.FONT_SIZE_BODY,
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                width=AppStyles.CONTROL_WIDTH_MD,
                on_select=lambda e, n=p_name: _update_param(n, e.control.value if e and e.control else ""),
            )

        if p_type == "textarea":
            if p_name == "ai_system_prompt" and state.selected_strategy:
                current_val = (
                    (params_ref.current or {}).get(p_name)
                    or vm.get_base_prompt(state.selected_strategy)
                    or p.get("default", "")
                )
            else:
                current_val = (params_ref.current or {}).get(p_name, p.get("default", ""))
            ctrl = ft.TextField(
                label=label,
                value=str(current_val),
                multiline=True,
                min_lines=6,
                max_lines=15,
                border_color=AppColors.DIVIDER,
                focused_border_color=AppColors.PRIMARY,
                text_size=AppStyles.FONT_SIZE_BODY_SM,
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=10),
                on_change=lambda e, n=p_name: _update_param(n, e.control.value if e and e.control else ""),
            )
            if p_name == "ai_system_prompt":
                ctrl.label = None
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
                                        on_click=lambda e, s=state.selected_strategy: _on_save_prompt(s),
                                    ),
                                    ft.TextButton(
                                        content=I18n.get("ai_reset_default"),
                                        icon=ft.Icons.RESTORE,
                                        style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
                                        height=30,
                                        on_click=lambda e, s=state.selected_strategy: _on_restore_prompt(s),
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

    def _build_params_panel() -> list[ft.Control]:
        """构建策略参数面板。"""
        from ui.theme import PARAM_GROUP_ORDER

        if not state.selected_strategy:
            return []

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

        def _build_controls(params: list[dict]) -> list[ft.Control]:
            controls: list[ft.Control] = []
            for p in params:
                ctrl = _build_param_control(p)
                if ctrl is None:
                    continue
                ctrl.col = _PARAM_COL_TEXTAREA if p.get("type") == "textarea" else _PARAM_COL
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
                result.append(
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
                    )
                )

        return result

    # --- 构建流式卡片控件 ---

    def _on_retry_click(name: str) -> None:
        """UX-2.3: 重试按钮点击：调 vm.schedule_retry（同步签名，内部 loop.create_task）。

        task 加入 VM._background_tasks 跟踪，dispose 时自动取消。
        """
        vm.schedule_retry(name)

    def _build_log_card(card: StreamCard) -> ft.Container:
        """构建单张流式/AI 占位卡 (state-driven, 从 vm.state.stream_cards 渲染)。"""
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
                            card.error,
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.ERROR,
                            no_wrap=False,
                        ),
                        ft.TextButton(
                            icon=ft.Icons.REFRESH,
                            content=I18n.get("ai_card_retry"),
                            tooltip=I18n.get("ai_card_retry"),
                            on_click=safe_on_click(lambda e, n=name: _on_retry_click(n)),
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

    # --- 构建历史树控件 ---

    def _build_history_tree() -> ft.Control:
        """构建历史树侧栏 (Task 3.2: 从 state.history_tree.rows 派生, 不持有 use_state)."""
        # Task 3.2: 历史树状态从 VM state 派生 (消除双轨状态)
        history_tree_rows = state.history_tree.rows
        history_tree_offset = state.history_tree.offset
        history_load_more_visible = state.history_tree.has_more

        tree_controls: list[ft.Control] = []
        if not history_tree_rows:
            tree_controls.append(
                ft.Container(
                    content=ft.Text(
                        I18n.get("screener_no_results"), color=AppColors.TEXT_SECONDARY, size=AppStyles.FONT_SIZE_BODY
                    ),
                    padding=AppStyles.SPACING_XL,
                )
            )
        else:
            first_expand = history_tree_offset <= 5 and len(history_tree_rows) <= 5
            for idx, item in enumerate(history_tree_rows):
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
                        on_click=lambda e, d=d_key: _on_tree_item_click(d, run_id=None),
                        dense=True,
                    )
                ]
                for s in strategies:
                    strategy_display = translate_strategy_name(s["strategy_name"])
                    run_suffix = f" [{s['run_id'][:8]}]" if len(strategies) > 1 else ""
                    subtiles.append(
                        ft.ListTile(
                            leading=ft.Icon(
                                ft.Icons.TRENDING_UP, size=AppStyles.FONT_SIZE_TITLE, color=AppColors.TEXT_SECONDARY
                            ),
                            title=ft.Text(
                                f"{strategy_display}{run_suffix} ({s['cnt']})", size=AppStyles.FONT_SIZE_BODY
                            ),
                            on_click=lambda e, d=d_key, rid=s["run_id"]: _on_tree_item_click(d, run_id=rid),
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
            on_click=safe_on_click(_on_load_more_history),
            visible=history_load_more_visible,
        )

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
                    ft.ListView(tree_controls, expand=True, spacing=0),
                    load_more_btn,
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=ft.Colors.SURFACE,
            border=ft.Border.only(right=ft.BorderSide(1, AppColors.DIVIDER)),
        )

    # --- 构建 UI ---

    is_realtime = state.mode == "REALTIME"

    # 1. 顶部控制区
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
                    on_change=safe_on_change(_on_mode_change),
                ),
            ]
        ),
        alignment=ft.MainAxisAlignment.START,
        spacing=10,
    )

    # R.2.6.1: 从 state.strategies_with_dep 构建 Flet Options (每次渲染重新翻译, locale 切换自动刷新)
    strategy_label = I18n.get("select_strategy")
    strategy_options = _build_strategy_options(state.strategies_with_dep, vm.strategy_mgr)
    strategy_dropdown = anchored(
        EIDS.SCREENER.STRATEGY_DROPDOWN,
        ft.Dropdown(
            label=strategy_label,
            options=strategy_options,
            value=state.selected_strategy,
            on_select=safe_on_select(_on_strategy_change),
            width=AppStyles.calc_dropdown_width(strategy_options, label=strategy_label),
            text_size=AppStyles.FONT_SIZE_LG,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
            focused_border_color=AppColors.PRIMARY,
        ),
    )

    realtime_controls = ft.Column(
        [
            ft.Row([strategy_dropdown], spacing=10),
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
            *_build_params_panel(),
        ],
        spacing=10,
        visible=is_realtime,
    )

    left_controls = ft.Column([title_row, realtime_controls], spacing=10)

    status_row = ft.Row(
        [
            ft.ProgressRing(visible=progress_visible, width=20, height=20, color=AppColors.ACCENT),
            ft.Text(status_text_value, color=status_text_color),
        ],
        alignment=ft.MainAxisAlignment.END,
        spacing=10,
    )

    # Task 3.2: loading 时切换为停止按钮 (STOP icon + cancel handler, 可点击)
    if state.loading:
        run_btn = ft.Button(
            content=I18n.get("stop_screening"),
            icon=ft.Icons.STOP,
            on_click=safe_on_click(_on_cancel_click_sync),
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
                on_click=safe_on_click(_on_run_click_sync),
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
            on_click=safe_on_click(_on_export_csv_click),
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
            on_click=safe_on_click(_on_export_excel_click),
            disabled=export_btn_disabled,
            style=AppStyles.outline_button(),
            height=45,
        ),
    )
    # Task 8.3: 选股→回测跳转按钮 (仅 realtime 模式 + 选中策略时可用)
    backtest_btn = ft.Button(
        content=I18n.get("screener_run_backtest"),
        icon=ft.Icons.SCIENCE,
        on_click=safe_on_click(_on_backtest_click_sync),
        disabled=run_disabled or not is_realtime,
        style=AppStyles.outline_button(),
        height=45,
        visible=is_realtime,
    )

    right_controls = ft.Column(
        [
            status_row,
            ft.Row([export_btn, export_excel_btn, run_btn], spacing=15, alignment=ft.MainAxisAlignment.END),
            # backtest_btn 独立一行右对齐：与导出/执行按钮同行会加宽 right_controls ~109px,
            # 压缩左侧 left_controls 使参数面板宽度受控, 表体视口被压到 0 →
            # 虚拟化行不构建 → 行文本从语义树消失 (PR #373 E2E 回归根因)。
            # 独立成行只增加 right_controls 固有高度, 不改变宽度。
            ft.Row([backtest_btn], alignment=ft.MainAxisAlignment.END),
        ],
        alignment=ft.MainAxisAlignment.START,
        spacing=8,
        horizontal_alignment=ft.CrossAxisAlignment.END,
    )

    control_card = ft.Container(
        content=ft.Row(
            [ft.Container(content=left_controls, expand=True), right_controls],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        **AppStyles.dashboard_card(padding=AppStyles.SPACING_XL),
    )

    # 2. 表格区
    pagination_row = ft.Row(
        safe_controls(
            [
                ft.IconButton(
                    ft.Icons.CHEVRON_LEFT,
                    on_click=safe_on_click(_on_prev_page),
                    icon_color=AppColors.PRIMARY,
                    disabled=page_no <= 1,
                    tooltip=I18n.get("screener_page_prev"),
                ),
                ft.Text(
                    I18n.get("screener_page_info").format(current=page_no, total=total_pages),
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.IconButton(
                    ft.Icons.CHEVRON_RIGHT,
                    on_click=safe_on_click(_on_next_page),
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
                    on_select=safe_on_select(_on_page_size_change),
                ),
            ]
        ),
        alignment=ft.MainAxisAlignment.CENTER,
    )

    # P1-3 批次 2 #70/#71: 表格空态分支 (formatted_rows 为空且非 loading 时显示 EmptyState)
    # Task 3.5: 移除误导性 CTA (clear_filters 不清结果集也不触发重运行),
    # 用户可通过工具栏的运行按钮重新执行策略
    table_content: ft.Control
    if not formatted_rows and not state.loading:
        table_content = EmptyState(
            icon=ft.Icons.INBOX,
            title=I18n.get("screener_no_results"),
            message=I18n.get("screener_no_data_context"),
        )
    else:
        table_content = ft.Column(
            [
                ft.Container(
                    content=PaginatedTable(
                        rows=formatted_rows,
                        columns=vt_columns,
                        sort_col=state.sort_column,
                        sort_asc=state.sort_ascending,
                        on_sort=_on_virtual_sort,
                        on_row_click=_on_row_click,
                        col_anchor=EIDS.SCREENER.column_header,
                        row_anchor=lambda row: EIDS.SCREENER.result_row(row["ts_code"]) if row.get("ts_code") else None,
                    ),
                    expand=True,
                ),
                ft.Divider(height=1, color=AppColors.DIVIDER),
                pagination_row,
            ],
            spacing=0,
            expand=True,
        )

    table_card = ft.Container(
        content=table_content,
        **AppStyles.dashboard_card(padding=0),
        # expand=2: 表格区分得 main_body 剩余高度的 2/3 (log_card 1/3)。
        # 1:1 均分时控制卡内容稍高 (如参数面板换行) 就会把表体视口压到 0,
        # 虚拟化行不构建 → 行文本从语义树消失 (PR #373 E2E 回归根因)。
        expand=2,
    )

    # 3. AI 分析报告区 (仅 REALTIME 模式)
    log_column_controls: list[ft.Control] = [
        ft.Text(
            I18n.get("ai_analysis_report"),
            font_family="Roboto",
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        ),
        ft.Container(
            content=ft.Column(
                [_build_log_card(c) for c in state.stream_cards],
                expand=True,
                spacing=4,
                scroll=ft.ScrollMode.ALWAYS,
                auto_scroll=True,
            ),
            border_radius=8,
            padding=5,
            expand=True,
        ),
    ]
    # Task 8.4: 卡片截断提示 — 超过 _MAX_LOG_CARDS 时显示折叠提示
    if state.stream_cards_truncated:
        log_column_controls.append(
            ft.Text(
                I18n.get("ai_cards_truncated_hint").format(max=_MAX_LOG_CARDS),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
                text_align=ft.TextAlign.CENTER,
            )
        )
    log_card = ft.Container(
        content=ft.Column(
            log_column_controls,
            spacing=5,
        ),
        expand=True,
        padding=ft.Padding.only(top=10),
        visible=is_realtime,
    )

    # 4. 右侧内容 (表格 + 日志)
    right_content = ft.Column(
        [table_card, log_card] if is_realtime else [table_card],
        expand=True,
        spacing=10,
    )

    # 5. 主布局: REALTIME 模式无侧栏; HISTORY 模式 ResizableSplitter(历史树 + 右侧)
    if is_realtime:
        main_body = right_content
    else:
        main_body = ResizableSplitter(
            left_content=_build_history_tree(),
            right_content=right_content,
            config_key="ui_splitter_screener_history",
            default_width=250,
            min_width=220,
            max_width=420,
            collapsible=True,
            collapsed=False,
            on_load_width=lambda: vm.get_splitter_width("ui_splitter_screener_history", 250),
            on_persist_width=lambda w: vm.persist_splitter_width("ui_splitter_screener_history", w),
        )

    # 6. 详情对话框 (条件渲染)
    dialog_control: ft.Control | None = None
    if detail_dialog_data is not None:
        page = _get_page()
        dialog_control = StockDetailDialog(
            stock_data=detail_dialog_data,
            data_processor=vm.data_processor,
            page=page,
            open_state=True,
            on_close=_on_detail_close,
            on_add_to_watchlist=_on_add_to_watchlist,
        )

    content_controls = [control_card, ft.Container(content=main_body, expand=True)]
    if dialog_control is not None:
        content_controls.append(dialog_control)

    return ft.Container(
        content=ft.Column(
            content_controls,
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
