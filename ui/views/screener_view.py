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
from decimal import Decimal

import flet as ft
import pandas as pd

from data.persistence.metadata_manager import MetaDataManager
from ui.components._markdown_safe import safe_open_url
from ui.components.resizable_splitter import ResizableSplitter
from ui.components.stock_detail_dialog import StockDetailDialog
from ui.components.virtual_table import PaginatedTable
from ui.hooks import use_viewmodel
from ui.i18n import I18n, translate_strategy_name
from ui.theme import AppColors, AppStyles
from ui.viewmodels.screener_view_model import ScreenerViewModel, StreamCard
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

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
        "prediction_result",
        "review_status",
        "created_at",
        "t1_price",
        "t1_pct",
        "t5_price",
        "t5_pct",
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
}

_VOLUME_COLS = frozenset({"vol", "volume", "amount"})

_DATE_COLS = frozenset({"list_date", "trade_date"})


def _format_cell_value(col: str, val) -> str:
    if pd.isna(val):
        return "-"
    if col == "strategy_name":
        return translate_strategy_name(str(val)) or str(val)
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
        if isinstance(val, (float, Decimal)):
            return f"{val:.2f}"
    return str(val)


def _build_table_data(df: pd.DataFrame) -> tuple[list, list]:
    vt_columns = []
    visible_cols = []
    for col in df.columns:
        if col in _HIDDEN_COLS:
            continue
        visible_cols.append(col)
        width = _COLUMN_WIDTHS.get(col, 80)
        label = MetaDataManager.get_column_alias("screening_history", col)
        vt_columns.append({"id": col, "label": label, "width": width})

    records = df[visible_cols].to_dict("records")  # type: ignore[call-overload]
    formatted_rows = [{col: _format_cell_value(col, val) for col, val in row.items()} for row in records]  # type: ignore[arg-type]
    return vt_columns, formatted_rows


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


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
            name = f"{name} ⚠️"
        options.append(ft.dropdown.Option(key, name))
    return options


def _build_page_size_options() -> list[ft.dropdown.Option]:
    """构建每页大小下拉框选项。"""
    per_page = I18n.get("screener_per_page")
    return [ft.dropdown.Option(k, text=f"{k} {per_page}") for k in ("10", "20", "50", "100")]


def _resolve_group_title(group_name: str, label_key: str | None = None) -> str:
    """Resolve group title with priority: label_key > DEFAULT_GROUP_LABELS > group_name."""
    from ui.theme import DEFAULT_GROUP_LABELS

    if label_key:
        return I18n.get(label_key)
    if group_name in DEFAULT_GROUP_LABELS:
        return DEFAULT_GROUP_LABELS[group_name]
    return group_name


def _compute_tier_hint(selected_strategy: str | None) -> str | None:
    """检查策略档位是否足够，不足时返回提示文案 key，否则 None。"""
    if not selected_strategy:
        return None
    try:
        from data.external.tushare_client import TushareClient
        from services.ai_service import get_strategy_min_tier
        from utils.config_handler import ConfigHandler

        current_tier = ConfigHandler.get_tushare_point_tier()
        min_tier = get_strategy_min_tier(selected_strategy)
        client = TushareClient()
        if client.get_tier_order(current_tier) < client.get_tier_order(min_tier):
            return I18n.get("sys_strategy_tier_hint")
    except Exception as e:
        logger.debug("[ScreenerView] tier hint check skipped: %s", e, exc_info=True)
    return None


def _build_strategy_desc(
    selected_strategy: str | None,
    vm: ScreenerViewModel,
) -> tuple[str, str]:
    """构建策略描述文本和颜色 (desc, color)。"""
    if not selected_strategy:
        return "", AppColors.TEXT_PRIMARY

    strategy_obj = vm.strategy_mgr.get_strategy(selected_strategy)
    strategies_with_dep = vm.strategy_mgr.get_all_with_dependencies()
    dep_info = strategies_with_dep.get(selected_strategy, {})

    if strategy_obj:
        defaults = {p["name"]: p.get("default") for p in strategy_obj.get_parameters()}
        desc = strategy_obj.get_dynamic_description(defaults)
    else:
        desc = vm.get_strategy_desc(selected_strategy)

    if dep_info.get("missing_apis"):
        warning_suffix = f"\n⚠️ {I18n.get('strategy_missing_apis')}: {', '.join(dep_info['missing_apis'])}"
        desc = f"{desc}{warning_suffix}"
        color = AppColors.WARNING
    else:
        color = AppColors.TEXT_PRIMARY

    return desc, color


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
def ScreenerView(initial_strategy: str | None = None) -> ft.Container:
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

    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(I18n.get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 本地 UI 状态 ---
    selected_strategy, set_selected_strategy = ft.use_state(None)
    strategy_desc, set_strategy_desc = ft.use_state("")
    strategy_desc_color, set_strategy_desc_color = ft.use_state(AppColors.TEXT_PRIMARY)
    tier_hint, set_tier_hint = ft.use_state(None)
    status_msg, set_status_msg = ft.use_state("")
    status_color, set_status_color = ft.use_state(AppColors.TEXT_SECONDARY)
    progress_visible, set_progress_visible = ft.use_state(False)
    run_disabled, set_run_disabled = ft.use_state(True)
    export_disabled, set_export_disabled = ft.use_state(True)
    mode, set_mode = ft.use_state("REALTIME")
    strategies_loaded, set_strategies_loaded = ft.use_state(False)
    strategy_options, set_strategy_options = ft.use_state(())
    page_size, set_page_size = ft.use_state(50)
    history_tree_offset, set_history_tree_offset = ft.use_state(0)
    history_tree_items, set_history_tree_items = ft.use_state(())
    history_load_more_visible, set_history_load_more_visible = ft.use_state(False)
    detail_dialog_data, set_detail_dialog_data = ft.use_state(None)
    pending_strategy, set_pending_strategy = ft.use_state(initial_strategy)
    # params_version 触发重渲染; params_ref 持久化参数值 (避免 stale closure)
    params_ref = ft.use_ref(lambda: {})
    _params_version, bump_params = ft.use_state(0)

    # --- FilePicker 生命周期 (use_ref 持有 + use_effect 注册/移除) ---
    file_picker = ft.use_ref(lambda: ft.FilePicker()).current

    def _setup_file_picker() -> None:
        page = _get_page()
        if page is not None and file_picker not in page.services:
            page.services.append(file_picker)

    def _cleanup_file_picker() -> None:
        page = _get_page()
        if page is not None and file_picker in page.services:
            page.services.remove(file_picker)

    ft.use_effect(_setup_file_picker, dependencies=[], cleanup=_cleanup_file_picker)

    # --- PubSub (TaskManager) 订阅/退订 ---

    def _setup_task_manager() -> None:
        vm.subscribe_task_manager()

    def _cleanup_task_manager() -> None:
        vm.unsubscribe_task_manager()

    ft.use_effect(_setup_task_manager, dependencies=[], cleanup=_cleanup_task_manager)

    # --- 策略加载 (mount 时执行一次) ---

    async def _load_strategies_async() -> None:
        try:
            strategies_with_dep = vm.strategy_mgr.get_all_with_dependencies()
            options = _build_strategy_options(strategies_with_dep, vm.strategy_mgr)
            set_strategy_options(tuple(options))
            set_strategies_loaded(True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ScreenerView] Failed to load strategies: %s", e, exc_info=True)
            set_status_msg(I18n.get("screener_load_failed"))
            set_status_color(AppColors.ERROR)

    ft.use_effect(_load_strategies_async, dependencies=[])

    # --- task_unlocked 响应 (VM state.task_unlocked 变化触发) ---

    def _on_task_unlocked() -> None:
        if state.task_unlocked:
            set_progress_visible(False)
            set_run_disabled(False)

    ft.use_effect(_on_task_unlocked, dependencies=[state.task_unlocked])

    # --- 深度链接 (策略加载后执行 pending_strategy) ---

    async def _execute_pending_strategy() -> None:
        if not strategies_loaded or not pending_strategy:
            return
        key = pending_strategy
        set_pending_strategy(None)
        # 验证策略存在
        if not any(opt.key == key for opt in strategy_options):
            logger.warning("[ScreenerView] Pending strategy %s not found.", key)
            return
        # 选中策略
        set_selected_strategy(key)
        desc, color = _build_strategy_desc(key, vm)
        set_strategy_desc(desc)
        set_strategy_desc_color(color)
        set_tier_hint(_compute_tier_hint(key))
        set_run_disabled(False)
        # 默认参数
        params_def = vm.get_strategy_params(key)
        for p in params_def:
            if p.get("name") == "ai_system_prompt":
                from strategies.strategy_prompts import get_base_prompt

                params_ref.current[p["name"]] = get_base_prompt(key) or p.get("default", "")
            else:
                params_ref.current[p["name"]] = p.get("default")
        bump_params(_params_version + 1)
        # 执行 (VM 在 run_strategy 开始时自动清空 stream_cards)
        try:
            await vm.run_strategy(key, params=dict(params_ref.current or {}))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ScreenerView] Pending strategy execution failed: %s", e, exc_info=True)

    ft.use_effect(_execute_pending_strategy, dependencies=[strategies_loaded, pending_strategy])

    # --- 事件 handler ---

    def _on_strategy_change(e: ft.ControlEvent) -> None:
        new_val = e.control.value if e and e.control else None
        UILogger.log_action("ScreenerView", "Select", f"strategy={new_val}")
        set_selected_strategy(new_val)
        set_run_disabled(not new_val)
        desc, color = _build_strategy_desc(new_val, vm)
        set_strategy_desc(desc)
        set_strategy_desc_color(color)
        set_tier_hint(_compute_tier_hint(new_val))
        # 初始化参数默认值
        if new_val:
            params_def = vm.get_strategy_params(new_val)
            for p in params_def:
                if p.get("name") == "ai_system_prompt":
                    from strategies.strategy_prompts import get_base_prompt

                    params_ref.current[p["name"]] = get_base_prompt(new_val) or p.get("default", "")
                else:
                    params_ref.current[p["name"]] = p.get("default")
            bump_params(_params_version + 1)

    async def _on_run_click(e: ft.ControlEvent) -> None:
        UILogger.log_action("ScreenerView", "Click", f"btn_run | strategy={selected_strategy}")
        if not selected_strategy:
            return
        set_run_disabled(True)
        try:
            await vm.run_strategy(selected_strategy, params=dict(params_ref.current or {}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[ScreenerView] Run strategy failed: %s", exc, exc_info=True)

    def _on_run_click_sync(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_run_click, e)

    async def _on_sort(col_id: str, new_asc: bool) -> None:
        try:
            await vm.sort_data(col_id, new_asc)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ScreenerView] Sort failed: %s", e, exc_info=True)

    def _on_virtual_sort(col_id: str, new_asc: bool) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_sort, col_id, new_asc)

    async def _on_export_click(e: ft.ControlEvent) -> None:
        UILogger.log_action("ScreenerView", "Click", "btn_export")
        df = vm.get_export_data()
        if df is None:
            page = _get_page()
            if page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("data_export_no_data"), "error")
            return
        timestamp = get_now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"screener_results_{timestamp}.csv"
        filepath = await file_picker.save_file(
            dialog_title=I18n.get("data_export_save_title"),
            file_name=default_filename,
            allowed_extensions=["csv"],
        )
        if not filepath:
            return
        set_export_disabled(True)
        try:
            path, error = await vm.export_results(filepath)
            page = _get_page()
            if path:
                filename = os.path.basename(filepath)
                if page is not None and hasattr(page, "show_toast"):
                    page.show_toast(I18n.get("data_export_success", file=filename), "success")
            elif page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("data_export_fail"), "error")
        except Exception as ex:
            logger.error("[ScreenerView] Export | Failed: %s", DataSanitizer.sanitize_error(ex))
            page = _get_page()
            if page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("data_export_fail"), "error")
        finally:
            set_export_disabled(False)

    def _on_export_click_sync(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_on_export_click, e)

    def _on_page_size_change(e: ft.ControlEvent) -> None:
        try:
            new_size = int(e.control.value if e and e.control else 50)
            vm.change_page_size(new_size)
            set_page_size(new_size)
        except (ValueError, TypeError):
            pass

    def _on_prev_page(e: ft.ControlEvent) -> None:
        vm.change_page(-1)

    def _on_next_page(e: ft.ControlEvent) -> None:
        vm.change_page(1)

    def _on_mode_change(e: ft.ControlEvent) -> None:
        selected = e.control.selected if e and e.control else []
        if not selected:
            return
        new_mode = list(selected)[0]
        UILogger.log_action("ScreenerView", "Toggle", f"mode={new_mode}")
        if new_mode == mode:
            return
        set_mode(new_mode)
        if new_mode == "HISTORY":
            vm.switch_to_history()
            # 清空表格
            set_history_tree_offset(0)
            set_history_tree_items(())
            page = _get_page()
            if page is not None:
                page.run_task(_load_history_tree, False)
        else:
            vm.switch_to_realtime()

    async def _load_history_tree(append: bool) -> None:
        """加载历史树数据。"""
        try:
            tree_data = await vm.load_history_tree(offset=history_tree_offset)
            if not tree_data:
                if not append:
                    set_history_tree_items(())
                set_history_load_more_visible(False)
                return
            items = []
            for date_str, strategies in tree_data.items():
                display_date, d_key = _format_history_date(date_str)
                total_cnt = sum(s["cnt"] for s in strategies)
                items.append(
                    {
                        "display_date": display_date,
                        "d_key": d_key,
                        "total_cnt": total_cnt,
                        "strategies": strategies,
                    }
                )
            if append:
                set_history_tree_items(history_tree_items + tuple(items))
            else:
                set_history_tree_items(tuple(items))
            set_history_load_more_visible(len(tree_data) >= 5)
            set_history_tree_offset(history_tree_offset + len(tree_data) * 5)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] History tree load failed: %s", ex, exc_info=True)
            page = _get_page()
            if page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("screener_load_failed"), "error")

    def _on_load_more_history(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_load_history_tree, True)

    async def _load_history_for_date(trade_date: str, strategy_name: str | None, run_id: str | None) -> None:
        set_progress_visible(True)
        if isinstance(trade_date, (datetime.date, datetime.datetime)):
            display = trade_date.strftime("%Y-%m-%d")
            trade_date = display
        else:
            ts = str(trade_date)
            display = f"{ts[:4]}-{ts[4:6]}-{ts[6:]}" if len(ts) == 8 and ts.isdigit() else ts
        if run_id:
            label = f"#{run_id[:8]}"
        else:
            label = translate_strategy_name(strategy_name) if strategy_name else I18n.get("screener_all_strategies")
        set_status_msg(f"{display} / {label}")
        set_status_color("blue")
        try:
            await vm.load_history_data(trade_date, strategy_name, run_id)
        except asyncio.CancelledError:
            raise
        finally:
            set_progress_visible(False)

    def _on_tree_item_click(trade_date: str, strategy_name: str | None = None, run_id: str | None = None) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_load_history_for_date, trade_date, strategy_name, run_id)

    def _on_row_click(row_data: dict) -> None:
        """行点击 → 打开详情对话框。"""
        ts_code = row_data.get("ts_code", "")
        raw_data = _raw_row_lookup.get(ts_code, row_data)
        set_detail_dialog_data(raw_data)

    def _on_detail_close() -> None:
        set_detail_dialog_data(None)

    # --- 参数面板 helper ---

    def _update_param(name: str, value) -> None:
        params_ref.current = {**(params_ref.current or {}), name: value}
        bump_params(_params_version + 1)

    def _on_slider_change(name: str, e: ft.ControlEvent) -> None:
        val = e.control.value if e and e.control else 0
        _update_param(name, val)
        # 动态更新策略描述
        if selected_strategy:
            strategy_obj = vm.strategy_mgr.get_strategy(selected_strategy)
            if strategy_obj and hasattr(strategy_obj, "get_dynamic_description"):
                set_strategy_desc(strategy_obj.get_dynamic_description(dict(params_ref.current or {})))

    async def _do_restore_default_async(strat: str, ctrl_field: ft.TextField) -> None:
        try:
            from strategies.strategy_prompts import get_base_prompt
            from utils.config_handler import ConfigHandler

            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_strategy_prompt, strat, None)
            new_val = str(await ThreadPoolManager().run_async(TaskType.IO, get_base_prompt, strat))
            _update_param("ai_system_prompt", new_val)
            page = _get_page()
            if page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("ai_settings_restored"), "info")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] Restore default prompt failed: %s", ex, exc_info=True)
            page = _get_page()
            if page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("sys_snack_save_err"), "error")

    async def _do_save_prompt_async(strat: str) -> None:
        try:
            from utils.config_handler import ConfigHandler
            from utils.prompt_guard import MAX_PROMPT_LENGTH, validate_prompt

            prompt_val = params_ref.current.get("ai_system_prompt", "") or ""
            is_valid, warning = validate_prompt(prompt_val)
            if not is_valid:
                page = _get_page()
                if page is not None and hasattr(page, "show_toast"):
                    msg = I18n.get(warning, warning)
                    if warning == "prompt_err_length":
                        msg = I18n.get("prompt_err_length").format(max=MAX_PROMPT_LENGTH)
                    page.show_toast(f"⚠ {msg}", "warning")
                return
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_strategy_prompt, strat, prompt_val)
            UILogger.log_action("ScreenerView", "SavePrompt", f"strategy={strat}")
            page = _get_page()
            if page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("ai_settings_saved"), "success")
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logger.error("[ScreenerView] Save prompt failed: %s", ex, exc_info=True)
            page = _get_page()
            if page is not None and hasattr(page, "show_toast"):
                page.show_toast(I18n.get("sys_snack_save_err"), "error")

    def _on_restore_prompt(strat: str) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_restore_default_async, strat, None)

    def _on_save_prompt(strat: str) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_prompt_async, strat)

    # --- 派生渲染数据 ---

    # 状态栏: 从 VM state.status_message 渲染
    if state.status_message:
        status_text_value = I18n.get(state.status_message.key, **state.status_message.params)
        status_text_color = state.status_color or AppColors.TEXT_SECONDARY
    else:
        status_text_value = status_msg
        status_text_color = status_color

    # 表格数据: 从 VM 读取当前页
    df = vm.get_current_page_data()
    if df is not None and not df.empty:
        _raw_row_lookup = {str(r.get("ts_code", "")): r for r in df.to_dict("records")}
        vt_columns, formatted_rows = _build_table_data(df)
    else:
        _raw_row_lookup = {}
        vt_columns = []
        formatted_rows = []

    # 分页信息
    page_no = state.page_no
    total_pages = state.total_pages
    total_items = state.total_items

    # 导出按钮: 有数据时启用
    export_btn_disabled = export_disabled or (total_items == 0)

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
            divisions = int((max_val - min_val) / step) if step > 0 else 10
            current_val = params_ref.current.get(p_name, default)
            init_display = int(current_val) if current_val == int(current_val) else round(current_val, 1)
            return ft.Column(
                [
                    ft.Text(f"{label}: {init_display}", size=12, color=AppColors.TEXT_SECONDARY),
                    ft.Slider(
                        min=min_val,
                        max=max_val,
                        value=current_val,
                        divisions=divisions,
                        label="{value}",
                        active_color=AppColors.PRIMARY,
                        tooltip=str(init_display),
                        on_change=lambda e, n=p_name: _on_slider_change(n, e),
                    ),
                ],
                spacing=2,
                width=200,
            )

        if p_type == "number":
            current_val = params_ref.current.get(p_name, p.get("default", ""))
            return ft.TextField(
                label=label,
                value=str(current_val),
                keyboard_type=ft.KeyboardType.NUMBER,
                dense=True,
                border_color=AppColors.DIVIDER,
                focused_border_color=AppColors.PRIMARY,
                text_size=13,
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                width=200,
                on_change=lambda e, n=p_name: _update_param(n, _parse_num(e.control.value if e and e.control else "")),
            )

        if p_type == "dropdown":
            options = p.get("options", [])
            current_val = params_ref.current.get(p_name, p.get("default", ""))
            return ft.Dropdown(
                label=label,
                value=str(current_val),
                options=[ft.dropdown.Option(str(o)) for o in options],
                dense=True,
                border_color=AppColors.DIVIDER,
                focused_border_color=AppColors.PRIMARY,
                text_size=13,
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                width=200,
                on_select=lambda e, n=p_name: _update_param(n, e.control.value if e and e.control else ""),
            )

        if p_type == "textarea":
            if p_name == "ai_system_prompt" and selected_strategy:
                from strategies.strategy_prompts import get_base_prompt

                current_val = (
                    params_ref.current.get(p_name) or get_base_prompt(selected_strategy) or p.get("default", "")
                )
            else:
                current_val = params_ref.current.get(p_name, p.get("default", ""))
            ctrl = ft.TextField(
                label=label,
                value=str(current_val),
                multiline=True,
                min_lines=6,
                max_lines=15,
                border_color=AppColors.DIVIDER,
                focused_border_color=AppColors.PRIMARY,
                text_size=12,
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
                                    ft.Text(label, size=12, color=AppColors.TEXT_SECONDARY),
                                    ft.Container(expand=True),
                                    ft.TextButton(
                                        content=I18n.get("ai_save_prompt"),
                                        icon=ft.Icons.SAVE,
                                        style=ft.ButtonStyle(color=AppColors.PRIMARY),
                                        height=30,
                                        on_click=lambda e, s=selected_strategy: _on_save_prompt(s),
                                    ),
                                    ft.TextButton(
                                        content=I18n.get("ai_reset_default"),
                                        icon=ft.Icons.RESTORE,
                                        style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
                                        height=30,
                                        on_click=lambda e, s=selected_strategy: _on_restore_prompt(s),
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

        if not selected_strategy:
            return []

        params_def = vm.get_strategy_params(selected_strategy)
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

        rendered_groups: list[tuple[str, str, list[ft.Control]]] = []

        for group_name in PARAM_GROUP_ORDER:
            if group_name == "default":
                continue
            if groups[group_name]:
                controls = [c for c in (_build_param_control(p) for p in groups[group_name]) if c is not None]
                if controls:
                    title = _resolve_group_title(group_name, group_labels.get(group_name))
                    rendered_groups.append((group_name, title, controls))

        if groups["default"]:
            controls = [c for c in (_build_param_control(p) for p in groups["default"]) if c is not None]
            if controls:
                title = _resolve_group_title("default", group_labels.get("default"))
                rendered_groups.append(("default", title, controls))

        for group_name in custom_groups:
            if groups[group_name]:
                controls = [c for c in (_build_param_control(p) for p in groups[group_name]) if c is not None]
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
                            ft.Text(title, size=13, weight=ft.FontWeight.W_500, color=AppColors.TEXT_PRIMARY),
                            ft.Divider(height=1, color=AppColors.DIVIDER),
                            ft.Row(controls, wrap=True, spacing=15),
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
            controls = [c for c in (_build_param_control(p) for p in groups["advanced"]) if c is not None]
            if controls:
                result.append(
                    ft.ExpansionTile(
                        title=ft.Text(I18n.get("ai_advanced_settings"), size=14, weight=ft.FontWeight.W_500),
                        subtitle=ft.Text(
                            I18n.get("ai_advanced_settings_desc"), size=12, color=AppColors.TEXT_SECONDARY
                        ),
                        controls=controls,
                        collapsed_text_color=AppColors.TEXT_PRIMARY,
                        text_color=AppColors.PRIMARY,
                        expanded=False,
                    )
                )

        return result

    # --- 构建流式卡片控件 ---

    def _build_log_card(card: StreamCard) -> ft.Container:
        """构建单张流式/AI 占位卡 (state-driven, 从 vm.state.stream_cards 渲染)。"""
        name = card.name
        if card.is_analyzing:
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(f"📈 {name}", weight=ft.FontWeight.W_600, size=16),
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
                padding=15,
                bgcolor=AppColors.SURFACE,
                margin=ft.Margin.only(bottom=10),
            )

        reasoning = card.reasoning
        content = card.content
        reasoning_visible = bool(reasoning)
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(f"📈 {name}", weight=ft.FontWeight.W_600, size=16),
                    ft.ExpansionTile(
                        title=ft.Text(f"💡 {I18n.get('ai_thinking')}..."),
                        subtitle=ft.Text(I18n.get("ai_expand_reasoning"), size=10, color=AppColors.TEXT_SECONDARY),
                        controls=[
                            ft.Container(
                                content=ft.Markdown(
                                    reasoning,
                                    selectable=True,
                                    extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                                    code_theme="atom-one-dark",  # type: ignore[arg-type]
                                    on_tap_link=safe_open_url,
                                ),
                                padding=10,
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
            padding=15,
            bgcolor=AppColors.SURFACE,
            margin=ft.Margin.only(bottom=10),
        )

    # --- 构建历史树控件 ---

    def _build_history_tree() -> ft.Control:
        """构建历史树侧栏。"""
        tree_controls: list[ft.Control] = []
        if not history_tree_items:
            tree_controls.append(
                ft.Container(
                    content=ft.Text(I18n.get("screener_no_results"), color=AppColors.TEXT_SECONDARY, size=13),
                    padding=20,
                )
            )
        else:
            first_expand = history_tree_offset <= 5 and len(history_tree_items) <= 5
            for idx, item in enumerate(history_tree_items):
                display_date = item["display_date"]
                d_key = item["d_key"]
                total_cnt = item["total_cnt"]
                strategies = item["strategies"]

                subtiles: list[ft.Control] = [
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.SELECT_ALL, size=18, color=AppColors.ACCENT),
                        title=ft.Text(f"{I18n.get('screener_all_strategies')} ({total_cnt})", size=13),
                        on_click=lambda e, d=d_key: _on_tree_item_click(d, run_id=None),
                        dense=True,
                    )
                ]
                for s in strategies:
                    strategy_display = translate_strategy_name(s["strategy_name"])
                    run_suffix = f" [{s['run_id'][:8]}]" if len(strategies) > 1 else ""
                    subtiles.append(
                        ft.ListTile(
                            leading=ft.Icon(ft.Icons.TRENDING_UP, size=16, color=AppColors.TEXT_SECONDARY),
                            title=ft.Text(f"{strategy_display}{run_suffix} ({s['cnt']})", size=13),
                            on_click=lambda e, d=d_key, rid=s["run_id"]: _on_tree_item_click(d, run_id=rid),
                            dense=True,
                        )
                    )

                tree_controls.append(
                    ft.ExpansionTile(
                        title=ft.Text(f"📅 {display_date}", size=14, weight=ft.FontWeight.W_500),
                        subtitle=ft.Text(
                            I18n.get("history_total").format(count=total_cnt), size=11, color=AppColors.TEXT_SECONDARY
                        ),
                        controls=subtiles,
                        expanded=(first_expand and idx == 0),
                        collapsed_icon_color=AppColors.TEXT_SECONDARY,
                    )
                )

        load_more_btn = ft.TextButton(
            content=I18n.get("history_load_more"),
            icon=ft.Icons.EXPAND_MORE,
            on_click=_on_load_more_history,
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
                            size=14,
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

    is_realtime = mode == "REALTIME"

    # 1. 顶部控制区
    title_row = ft.Row(
        [
            ft.Icon(ft.Icons.ELECTRIC_BOLT, color=AppColors.PRIMARY, size=24),
            ft.Text(I18n.get("screener_title"), size=20, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
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
                selected=[mode],
                on_change=_on_mode_change,
            ),
        ],
        alignment=ft.MainAxisAlignment.START,
        spacing=10,
    )

    strategy_dropdown = ft.Dropdown(
        label=I18n.get("select_strategy"),
        options=list(strategy_options),
        value=selected_strategy,
        on_select=_on_strategy_change,
        width=AppStyles.CONTROL_WIDTH_MD,
        text_size=14,
        bgcolor=AppColors.INPUT_BG,
        border_color=AppColors.INPUT_BORDER,
        color=AppColors.INPUT_TEXT,
        focused_border_color=AppColors.PRIMARY,
    )

    realtime_controls = ft.Column(
        [
            ft.Row([strategy_dropdown], spacing=10),
            ft.Text(
                strategy_desc or I18n.get("screener_no_strategy_hint"),
                size=13,
                color=strategy_desc_color,
                no_wrap=False,
            ),
            ft.Text(tier_hint or "", size=12, color=AppColors.WARNING, visible=tier_hint is not None, no_wrap=False),
            *_build_params_panel(),
        ],
        spacing=10,
        visible=is_realtime,
    )

    left_controls = ft.Column([title_row, realtime_controls], spacing=10, expand=True)

    status_row = ft.Row(
        [
            ft.ProgressRing(visible=progress_visible, width=20, height=20, color=AppColors.ACCENT),
            ft.Text(status_text_value, color=status_text_color),
        ],
        alignment=ft.MainAxisAlignment.END,
        spacing=10,
    )

    run_btn = ft.Button(
        content=I18n.get("run_screening"),
        icon=ft.Icons.PLAY_ARROW,
        on_click=_on_run_click_sync,
        disabled=run_disabled,
        style=AppStyles.primary_button(),
        height=45,
        visible=is_realtime,
    )
    export_btn = ft.Button(
        content=I18n.get("screener_export"),
        icon=ft.Icons.DOWNLOAD,
        on_click=_on_export_click_sync,
        disabled=export_btn_disabled,
        style=AppStyles.outline_button(),
        height=45,
    )

    right_controls = ft.Column(
        [
            status_row,
            ft.Row([export_btn, run_btn], spacing=15, alignment=ft.MainAxisAlignment.END),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        horizontal_alignment=ft.CrossAxisAlignment.END,
    )

    control_card = ft.Container(
        content=ft.Row(
            [left_controls, right_controls],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        **AppStyles.dashboard_card(padding=20),
    )

    # 2. 表格区
    pagination_row = ft.Row(
        [
            ft.IconButton(
                ft.Icons.CHEVRON_LEFT, on_click=_on_prev_page, icon_color=AppColors.PRIMARY, disabled=page_no <= 1
            ),
            ft.Text(
                I18n.get("screener_page_info").format(current=page_no, total=total_pages), color=AppColors.TEXT_PRIMARY
            ),
            ft.IconButton(
                ft.Icons.CHEVRON_RIGHT,
                on_click=_on_next_page,
                icon_color=AppColors.PRIMARY,
                disabled=page_no >= total_pages,
            ),
            ft.Container(width=20),
            ft.Dropdown(
                label=I18n.get("screener_page_size"),
                options=_build_page_size_options(),
                value=str(page_size),
                width=120,
                dense=True,
                text_size=13,
                on_select=_on_page_size_change,
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
    )

    table_card = ft.Container(
        content=ft.Column(
            [
                PaginatedTable(
                    rows=formatted_rows,
                    columns=vt_columns,
                    sort_col=state.sort_column,
                    sort_asc=state.sort_ascending,
                    on_sort=_on_virtual_sort,
                    on_row_click=_on_row_click,
                ),
                ft.Divider(height=1, color=AppColors.DIVIDER),
                pagination_row,
            ],
            spacing=0,
            expand=True,
        ),
        **AppStyles.dashboard_card(padding=0),
        expand=True,
    )

    # 3. AI 分析报告区 (仅 REALTIME 模式)
    log_card = ft.Container(
        content=ft.Column(
            [
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
            ],
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
        )

    content_controls = [control_card, main_body]
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
