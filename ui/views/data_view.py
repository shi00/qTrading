"""data_view — 声明式组件 (Phase F.2).

从命令式容器子类重写为 ft.component 装饰器 + use_viewmodel 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 三个命令式 class (TableViewerTab/SQLConsoleTab/DataExplorerView) → ft.component 函数组件
- DataExplorerView 通过 ``use_viewmodel(factory=)`` 内部模式实例化 DataExplorerViewModel
- 子 Tab (TableViewerTab/SQLConsoleTab) 为纯 props 组件: state 由父组件唯一订阅后传入
  (D13: 消除同一 VM 三重订阅导致的三子树重复渲染), 不再 ``use_viewmodel(vm=)``
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- FilePicker 通过 ``use_ref`` + ``use_effect`` 注册到 ``page.services``, cleanup 时移除
- PubSub 通过 ``use_effect(setup, [], cleanup=cleanup)`` 订阅/退订
- page 访问用 ``ft.context.page`` (try/except 守卫 RuntimeError)
- 异步任务用 ``page.run_task``, R2 CancelledError 必须 raise
- 消费声明式 PaginatedTable (函数调用, props 推送)
- 移除全部命令式 API (did_mount/will_unmount/refresh_locale/update_theme/handle_resize/.update())
"""

import asyncio
import datetime
import logging
import os
import time
import typing

import flet as ft
import flet_code_editor as fce
import pandas as pd

from ui.components.flet_type_helpers import (
    get_control_attr,
    get_control_value,
    safe_controls,
    safe_on_change,
    safe_on_click,
    safe_on_select,
)
from ui.components.state_views import EmptyState, ErrorState
from ui.components.toast_manager import open_export_folder
from ui.components.virtual_table import PaginatedTable
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import CACHE_CLEARED_TOPIC
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles
from ui.viewmodels.data_explorer_view_model import (
    DataExplorerState,
    DataExplorerViewModel,
    MAX_EXPORT_ROWS,
    SqlResultRow,
    TableRow,
)
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level pure helpers
# ============================================================================


# SQL 控制台 CodeEditor 自动补全词表（SQL 常用关键字，覆盖 SELECT/WHERE/JOIN 等高频语法）。
_SQL_KEYWORDS: list[str] = [
    "SELECT",
    "FROM",
    "WHERE",
    "AND",
    "OR",
    "NOT",
    "NULL",
    "IN",
    "EXISTS",
    "BETWEEN",
    "LIKE",
    "ILIKE",
    "IS",
    "AS",
    "DISTINCT",
    "ORDER",
    "BY",
    "GROUP",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "JOIN",
    "INNER",
    "LEFT",
    "RIGHT",
    "FULL",
    "OUTER",
    "CROSS",
    "ON",
    "UNION",
    "ALL",
    "INSERT",
    "INTO",
    "VALUES",
    "UPDATE",
    "SET",
    "DELETE",
    "CREATE",
    "TABLE",
    "INDEX",
    "DROP",
    "ALTER",
    "ADD",
    "COLUMN",
    "PRIMARY",
    "KEY",
    "FOREIGN",
    "REFERENCES",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "CAST",
    "COALESCE",
    "NULLIF",
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "ASC",
    "DESC",
    "DEFAULT",
    "UNIQUE",
    "CHECK",
    "CONSTRAINT",
    "TRUNCATE",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
]


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
    show_toast = typing.cast(typing.Any, page).show_toast
    if show_toast is not None:
        show_toast(msg, msg_type, action_text=action_text, on_action=on_action)


def _format_cell_value(val: object, col_name: str) -> str:
    """格式化单元格值 (None/NaN → '-', 日期格式化)。"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return "-"
    if "date" in col_name.lower():
        if isinstance(val, (datetime.date, datetime.datetime)):
            return val.strftime("%Y-%m-%d")
        if isinstance(val, str) and len(val) == 8 and val.isdigit():
            return f"{val[:4]}-{val[4:6]}-{val[6:8]}"
    return str(val)


def _build_filter_op_options() -> list[ft.dropdown.Option]:
    """构建过滤操作符选项。"""
    return [
        ft.dropdown.Option("="),
        ft.dropdown.Option("LIKE"),
        ft.dropdown.Option(">"),
        ft.dropdown.Option("<"),
        ft.dropdown.Option(">="),
        ft.dropdown.Option("<="),
        ft.dropdown.Option("!="),
    ]


def _build_table_selector_options(tables: tuple[str, ...], vm: DataExplorerViewModel) -> list[ft.dropdown.Option]:
    """构建表选择器选项 (locale 变更时由组件重渲染自动刷新)。"""
    return [ft.dropdown.Option(key=t, text=vm.get_table_alias(t)) for t in tables]


def _build_filter_col_options(
    current_table: str, columns: tuple[str, ...], vm: DataExplorerViewModel
) -> list[ft.dropdown.Option]:
    """构建过滤列选项。"""
    return [
        ft.dropdown.Option(
            key=col,
            text=vm.get_column_alias(current_table, col),
        )
        for col in columns
    ]


def _build_table_columns_spec(
    current_table: str, columns: tuple[str, ...], vm: DataExplorerViewModel
) -> list[dict[str, object]]:
    """构建 PaginatedTable columns spec (id/label/width)。"""
    return [
        {
            "id": col,
            "label": vm.get_column_alias(current_table, col),
            "width": 140,
        }
        for col in columns
    ]


def _table_rows_to_paginated_rows(
    rows: tuple[TableRow, ...],
    columns: tuple[str, ...],
) -> list[dict[str, str]]:
    """tuple[TableRow, ...] → PaginatedTable rows (dict 列表), 格式化日期/None.

    values 与 columns 按索引对齐 (L771 合规).
    """
    if not rows or not columns:
        return []
    return [
        {col: _format_cell_value(value, col) for col, value in zip(columns, row.values, strict=False)} for row in rows
    ]


def _build_sql_columns_spec(columns: tuple[str, ...], vm: DataExplorerViewModel) -> list[dict[str, object]]:
    """构建 SQL 结果表的 columns spec (从 state.sql_result_columns)."""
    return [
        {
            "id": col,
            "label": vm.get_column_alias(None, col),
            "width": 140,
        }
        for col in columns
    ]


def _sql_rows_to_paginated_rows(
    rows: tuple[SqlResultRow, ...],
    columns: tuple[str, ...],
) -> list[dict[str, str]]:
    """tuple[SqlResultRow, ...] → PaginatedTable rows (dict 列表).

    values 与 columns 按索引对齐 (L771 合规).
    """
    if not rows or not columns:
        return []
    return [
        {str(col): _format_cell_value(value, str(col)) for col, value in zip(columns, row.values, strict=False)}
        for row in rows
    ]


def _ceil_div(n: int, d: int) -> int:
    """向上取整除法 (d > 0)。"""
    return -(-n // d) if d > 0 else 1


# ============================================================================
# TableViewerTab
# ============================================================================


@ft.component
def TableViewerTab(
    state: DataExplorerState,
    vm: DataExplorerViewModel,
    active: bool = True,
) -> ft.Column:
    """Tab 1: 可视化表浏览器 (声明式).

    D13: 不再 ``use_viewmodel(vm=)`` 自订阅 — state 由父组件唯一订阅后经 props 传入
    (纯 props 子组件, 消除同一 VM 三重订阅导致的三子树重复渲染).

    FilePicker 通过 ``use_ref`` + ``use_effect`` 注册到 ``page.services``。

    Args:
        state: 父组件传入的 VM state snapshot (DataExplorerView 唯一订阅).
        vm: 外部传入的 DataExplorerViewModel (由 DataExplorerView 实例化并共享)。
        active: 当前 tab 是否激活 (控制副作用执行)。
    """
    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 本地 UI 状态 (输入框值, 用户覆盖) ---
    filter_col_override, set_filter_col_override = typing.cast(
        tuple[str | None, typing.Callable[[str | None], None]], ft.use_state(None)
    )
    filter_op_value, set_filter_op_value = ft.use_state("=")
    filter_val_text, set_filter_val_text = ft.use_state("")

    effective_filter_col = (
        filter_col_override
        if filter_col_override is not None
        else (state.table_columns[0] if state.table_columns else None)
    )

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

    # --- 异步加载逻辑 (R2: except Exception 不捕获 CancelledError) ---
    # PR-478 修复: 拆分为两个独立 effect, 避免 _init_tables 自取消.
    # 原实现: _init_tables 调用 vm.init_tables() 触发 tables_loaded=True → Flet 重渲染
    #   → 依赖变化重新调度同一 EffectHook → hook.cancel() 取消旧 _setup_task
    #   → load_table_schema() 收到 CancelledError → table_columns/rows/total_rows 保持空.
    # 拆分后:
    #   - _init_tables 只修改 tables_loaded (effect 依赖), 自身完成即 return,
    #     重渲染时再调度同一 effect (tables_loaded 已 True) 直接返回, 不取消自身.
    #   - _load_initial_table 消费 tables_loaded=True, 调用 load_table_schema/query_data
    #     修改的 table_columns/rows/total_rows 不在依赖中, 不会触发自身重调度.
    async def _load_schema_and_data() -> None:
        if state.is_loading:
            return
        try:
            await vm.load_table_schema(state.current_table)
            await vm.query_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] load_schema error: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("data_err_load_schema"), "error")

    async def _init_tables() -> None:
        if not active or state.tables_loaded:
            return
        try:
            await vm.init_tables()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] init_tables error: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("data_err_load_schema"), "error")

    async def _load_initial_table() -> None:
        if not active or not state.tables_loaded:
            return
        try:
            await _load_schema_and_data()
            # Phase 6.4 (FR-UX-006): 加载数据新鲜度 (非关键, 失败不阻塞)
            await vm.load_data_freshness()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] initial load error: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("data_err_load_schema"), "error")

    # tables_loaded 变化时触发 (mount + cache_cleared stale 重载)
    ft.use_effect(_init_tables, dependencies=[state.tables_loaded, active])
    ft.use_effect(_load_initial_table, dependencies=[state.tables_loaded, active])

    # --- 异步 handler (供 page.run_task 调度) ---
    async def _do_table_change(new_table: str) -> None:
        try:
            vm.set_table(new_table)
            UILogger.log_action("TableViewerTab", "Select", f"table={new_table}")
            vm.reset_table_state()
            set_filter_col_override(None)
            set_filter_val_text("")
            await _load_schema_and_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播

    async def _do_query() -> None:
        ensure_correlation_id()
        UILogger.log_action("TableViewerTab", "Click", "btn_query")
        vm.set_filter(effective_filter_col or "", filter_op_value, filter_val_text)
        try:
            await vm.query_data(page=1)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] query error: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    async def _do_refresh() -> None:
        ensure_correlation_id()
        UILogger.log_action("TableViewerTab", "Click", "btn_refresh")
        try:
            await vm.query_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] refresh error: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    async def _do_sort_query() -> None:
        try:
            await vm.query_data(page=1)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] sort query error: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    async def _do_prev_page() -> None:
        UILogger.log_action("TableViewerTab", "Click", "btn_prev_page")
        if state.current_page > 1:
            try:
                await vm.query_data(page=state.current_page - 1)
            except asyncio.CancelledError:
                raise  # R2: 必须传播
            except Exception as e:
                logger.error("[TableViewerTab] prev page error: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    async def _do_next_page() -> None:
        UILogger.log_action("TableViewerTab", "Click", "btn_next_page")
        total_pages = _ceil_div(state.total_rows, state.page_size)
        if state.current_page < total_pages:
            try:
                await vm.query_data(page=state.current_page + 1)
            except asyncio.CancelledError:
                raise  # R2: 必须传播
            except Exception as e:
                logger.error("[TableViewerTab] next page error: %s", DataSanitizer.sanitize_error(e), exc_info=True)

    async def _export_data(format_: str, current_page: bool = True) -> None:
        scope = "current_page" if current_page else "all"
        UILogger.log_action("TableViewerTab", "Click", f"export_{format_}={scope}")
        try:
            df = await vm.export_data(current_page_only=current_page)
            if df.empty:
                page = _get_page()
                if page is not None:
                    _safe_show_toast(page, I18n.get("data_export_no_data"), "error")
                return
            suffix = f"_p{state.current_page}" if current_page else "_all"
            timestamp = get_now().strftime("%Y%m%d_%H%M%S")
            ext = "csv" if format_ == "csv" else "xlsx"
            default_filename = f"{state.current_table}{suffix}_{timestamp}.{ext}"
            if file_picker is None:
                return
            filepath = await file_picker.save_file(
                dialog_title=I18n.get("data_export_save_title"),
                file_name=default_filename,
                allowed_extensions=[ext],
            )
            if filepath:
                try:
                    if format_ == "csv":
                        await vm.write_csv(df, filepath)
                    else:
                        await vm.write_excel(df, filepath)
                    filename = os.path.basename(filepath)
                    msg = I18n.get("data_export_success", file=filename)
                    page = _get_page()
                    if page is not None:
                        # P2-10: 导出成功 toast 附"打开文件夹" action
                        _safe_show_toast(
                            page,
                            msg,
                            "success",
                            action_text=I18n.get("data_export_open_folder"),
                            on_action=lambda: page.run_task(open_export_folder, filepath),
                        )
                        # Phase 6.3 (FR-UX-006): 截断警告 toast (导出全部且达到上限)
                        if not current_page and len(df) >= MAX_EXPORT_ROWS:
                            _safe_show_toast(
                                page, I18n.get("data_export_truncated_warning"), "warning"
                            )  # pragma: no cover
                except Exception as ex:
                    logger.error("Export write failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
                    page = _get_page()
                    if page is not None:
                        _safe_show_toast(page, I18n.get("data_export_fail"), "error")
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("Export failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export failed traceback", exc_info=True)
            page = _get_page()
            if page is not None:
                _safe_show_toast(page, I18n.get("data_export_fail"), "error")

    # --- 同步事件 handler (调度 page.run_task) ---
    def _on_table_changed(e: ft.ControlEvent) -> None:
        new_table = get_control_value(e.control, ft.Dropdown) if e and e.control else None
        if not new_table:
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_table_change, new_table)

    def _on_query_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_query)

    def _on_refresh_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_refresh)

    def _on_retry_load() -> None:
        """ErrorState on_retry: 清除错误 + 重新加载 schema (P1-3 批次 2)."""
        vm.clear_error()
        page = _get_page()
        if page is not None:
            page.run_task(_load_schema_and_data)

    def _on_sort(col_id: str, new_asc: bool) -> None:
        try:
            col_index = state.table_columns.index(col_id)
        except ValueError:
            return
        vm.set_sort(col_index, new_asc)
        vm.clear_error()
        page = _get_page()
        if page is not None:
            page.run_task(_do_sort_query)

    def _on_prev_page(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_prev_page)

    def _on_next_page(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_next_page)

    def _on_export_current(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_export_data, "csv", True)

    def _on_export_all(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_export_data, "csv", False)

    def _on_export_excel(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_export_data, "excel", True)

    # --- 派生渲染数据 ---
    is_loading = state.is_loading
    total_pages = _ceil_div(state.total_rows, state.page_size)
    sort_col_id = (
        state.table_columns[state.sort_col_index]
        if state.sort_col_index is not None and 0 <= state.sort_col_index < len(state.table_columns)
        else None
    )
    columns_spec = _build_table_columns_spec(state.current_table, state.table_columns, vm)
    rows_data = _table_rows_to_paginated_rows(state.table_rows, state.table_columns)

    # --- 构建 UI ---
    table_label = I18n.get("data_select_table")
    table_options = _build_table_selector_options(state.tables_list, vm)
    table_selector = anchored(
        EIDS.DATA.TABLE_DROPDOWN,
        ft.Dropdown(
            width=AppStyles.calc_dropdown_width(table_options, label=table_label, min_width=250.0),
            label=table_label,
            value=state.current_table or None,
            on_select=safe_on_select(_on_table_changed),
            disabled=is_loading or not state.tables_loaded,
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border_color=AppColors.INPUT_BORDER,
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
            options=table_options,
            height=36,
            text_size=AppStyles.FONT_SIZE_BODY,
            content_padding=AppStyles.SPACING_SM,
        ),
    )

    filter_col_label = I18n.get("data_filter_col")
    filter_col_options = _build_filter_col_options(state.current_table, state.table_columns, vm)
    filter_col = anchored(
        EIDS.DATA.FILTER_COL_DROPDOWN,
        ft.Dropdown(
            label=filter_col_label,
            width=AppStyles.calc_dropdown_width(
                filter_col_options, label=filter_col_label, min_width=150.0, max_width=360.0
            ),
            value=effective_filter_col,
            on_select=lambda e: set_filter_col_override(e.control.value if e and e.control else None),
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border_color=AppColors.INPUT_BORDER,
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
            options=filter_col_options,
            height=36,
            text_size=AppStyles.FONT_SIZE_BODY,
            content_padding=AppStyles.SPACING_SM,
        ),
    )

    filter_op = anchored(
        EIDS.DATA.FILTER_OP_DROPDOWN,
        ft.Dropdown(
            label=I18n.get("data_filter_op"),
            width=100,
            value=filter_op_value,
            on_select=lambda e: set_filter_op_value((e.control.value if e and e.control else None) or "="),
            options=_build_filter_op_options(),
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border_color=AppColors.INPUT_BORDER,
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
            height=36,
            text_size=AppStyles.FONT_SIZE_BODY,
            content_padding=5,
        ),
    )

    filter_val = anchored(
        EIDS.DATA.FILTER_VALUE_INPUT,
        ft.TextField(
            label=I18n.get("data_filter_val"),
            width=AppStyles.CONTROL_WIDTH_MD,
            value=filter_val_text,
            on_change=lambda e: set_filter_val_text(e.control.value if e and e.control else ""),
            on_submit=safe_on_change(_on_query_click),
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border_color=AppColors.INPUT_BORDER,
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
            height=36,
            text_size=AppStyles.FONT_SIZE_BODY,
            content_padding=AppStyles.SPACING_SM,
        ),
    )

    btn_query = anchored(
        EIDS.DATA.QUERY_BUTTON,
        ft.IconButton(
            ft.Icons.SEARCH,
            tooltip=I18n.get("common_query"),
            on_click=safe_on_click(_on_query_click),
            icon_color=AppColors.PRIMARY,
            icon_size=AppStyles.FONT_SIZE_HEADLINE,
            disabled=is_loading,
        ),
    )
    btn_refresh = ft.IconButton(
        ft.Icons.REFRESH,
        tooltip=I18n.get("common_refresh"),
        on_click=safe_on_click(_on_refresh_click),
        icon_size=AppStyles.FONT_SIZE_HEADLINE,
        disabled=is_loading,
    )

    # 加载/空态 widget
    loading_widget = ft.Container(
        content=ft.Column(
            [
                ft.Container(
                    content=ft.ProgressRing(
                        width=48,
                        height=48,
                        stroke_width=4,
                        color=AppColors.PRIMARY,
                    ),
                    padding=AppStyles.SPACING_XL,
                    border_radius=50,
                    bgcolor=ft.Colors.with_opacity(0.08, AppColors.PRIMARY),
                ),
                ft.Container(height=16),
                ft.Text(
                    I18n.get("data_loading"),
                    size=AppStyles.FONT_SIZE_TITLE,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Text(
                    I18n.get("data_loading_hint"),
                    size=AppStyles.FONT_SIZE_BODY,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=AppStyles.EMPTY_STATE_PADDING,
        bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.SHADOW),
        border_radius=12,
        border=ft.Border.all(1, ft.Colors.with_opacity(0.1, AppColors.BORDER)),
    )

    # 表格区域: P1-3 批次 2 三态 (error > loading > table > loading placeholder)
    if state.error_message is not None:
        grid_content = ErrorState(
            icon=ft.Icons.ERROR_OUTLINE,
            title=I18n.get("error_state_load_failed_title"),
            message=I18n.get("error_state_load_failed_message"),
            on_retry=_on_retry_load,
            retry_text=I18n.get("common_retry"),
        )
    elif is_loading:
        grid_content = loading_widget
    elif state.table_columns and state.total_rows == 0:
        # Task 8.5: 数据页空态引导 — 区分「表无数据」与「筛选无结果」
        filter_applied = bool(state.filter_col and state.filter_val)
        if filter_applied:
            grid_content = EmptyState(
                icon=ft.Icons.FILTER_ALT_OFF,
                title=I18n.get("empty_filter_result"),
            )
        else:
            grid_content = EmptyState(
                icon=ft.Icons.INBOX,
                title=I18n.get("empty_table_hint"),
            )
    elif state.table_columns:
        grid_content = PaginatedTable(
            rows=rows_data,
            columns=columns_spec,
            sort_col=sort_col_id,
            sort_asc=state.sort_asc,
            on_sort=_on_sort,
        )
    else:
        grid_content = loading_widget

    # Phase 6.4 (FR-UX-006): 数据新鲜度标签 (滞后 >3 日显示警告色)
    if state.data_latest_date:
        freshness_color = AppColors.ERROR if state.data_lag_days > 3 else AppColors.TEXT_SECONDARY  # pragma: no cover
        freshness_label = ft.Text(  # pragma: no cover
            I18n.get("data_freshness_label", date=state.data_latest_date, days=state.data_lag_days),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=freshness_color,
            weight=ft.FontWeight.W_500 if state.data_lag_days > 3 else ft.FontWeight.NORMAL,
        )
    else:
        freshness_label = ft.Text(
            I18n.get("data_freshness_no_data"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_HINT,
        )

    # 工具栏
    toolbar_content = ft.Row(
        [
            table_selector,
            ft.VerticalDivider(width=10, color=ft.Colors.TRANSPARENT),
            ft.Container(
                content=ft.Row(
                    [filter_col, filter_op, filter_val, btn_query, btn_refresh],
                    spacing=5,
                ),
                padding=5,
                border=ft.Border.all(1, AppColors.BORDER),
                border_radius=8,
                bgcolor=AppColors.SURFACE,
            ),
            ft.Container(expand=True),
            freshness_label,
            ft.Container(width=8),
            ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT,
                tooltip=I18n.get("common_more_actions"),
                items=[
                    ft.PopupMenuItem(
                        content=I18n.get("data_export_current"),
                        icon=ft.Icons.DOWNLOAD,
                        on_click=safe_on_click(_on_export_current),
                    ),
                    ft.PopupMenuItem(
                        content=I18n.get("data_export_all"),
                        icon=ft.Icons.DRIVE_FILE_MOVE,
                        on_click=safe_on_click(_on_export_all),
                    ),
                    ft.PopupMenuItem(
                        content=I18n.get("data_export_excel"),
                        icon=ft.Icons.TABLE_VIEW,
                        on_click=safe_on_click(_on_export_excel),
                    ),
                ],
            ),
            # 右侧留白: Row 不支持 padding, 用 Container 间隔器替代
            ft.Container(width=8),
        ],
        alignment=ft.MainAxisAlignment.START,
        spacing=10,
        scroll=ft.ScrollMode.AUTO,
    )

    toolbar_container = ft.Column(
        [
            ft.Container(content=toolbar_content, padding=AppStyles.SPACING_SM, bgcolor=AppColors.SURFACE),
            ft.ProgressBar(visible=is_loading, color=AppColors.PRIMARY),
        ],
        spacing=0,
    )

    # 分页栏
    pagination_bar = ft.Container(
        content=ft.Row(
            safe_controls(
                [
                    ft.Text(
                        I18n.get("data_total_rows").format(count=state.total_rows),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    ft.Container(expand=True),
                    ft.IconButton(
                        ft.Icons.CHEVRON_LEFT,
                        on_click=safe_on_click(_on_prev_page),
                        disabled=is_loading or state.current_page <= 1,
                        tooltip=I18n.get("common_prev_page"),
                    ),
                    ft.Text(
                        I18n.get("data_page_num").format(
                            current=state.current_page,
                            total=total_pages,
                        )
                    ),
                    ft.IconButton(
                        ft.Icons.CHEVRON_RIGHT,
                        on_click=safe_on_click(_on_next_page),
                        disabled=is_loading or state.current_page >= total_pages,
                        tooltip=I18n.get("common_next_page"),
                    ),
                ]
            ),
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.Padding.symmetric(horizontal=20, vertical=5),
        bgcolor=AppColors.SURFACE,
        border=ft.Border.only(top=ft.BorderSide(1, AppColors.BORDER)),
    )

    # PR-478 修复: TABLE_READY 信号 (LABEL kind, 仅做存在性探测).
    # 仅在 tables_loaded + table_columns 非空 + is_loading=False 时渲染.
    # 切表时 reset_table_state 清空 table_columns → 信号从 DOM 移除 (expect_hidden 通过);
    # load_table_schema 完成后 table_columns 非空 → 信号重新挂载 (expect_visible 通过).
    # 生产模式下 anchored() 返回原 Text (1px 透明空格, 用户不可见).
    table_ready = state.tables_loaded and bool(state.table_columns) and not is_loading
    controls: list[ft.Control] = [
        toolbar_container,
        ft.Container(content=grid_content, expand=True),
        pagination_bar,
    ]
    if table_ready:
        controls.append(
            anchored(
                EIDS.DATA.TABLE_READY,
                ft.Text(" ", size=AppStyles.FONT_SIZE_CAPTION, color=ft.Colors.TRANSPARENT),
            )
        )
    return ft.Column(
        controls,
        expand=True,
        spacing=0,
    )


# ============================================================================
# SQLConsoleTab
# ============================================================================


@ft.component
def SQLConsoleTab(state: DataExplorerState, vm: DataExplorerViewModel) -> ft.Column:
    """Tab 2: SQL 控制台 (声明式).

    D13: 不再 ``use_viewmodel(vm=)`` 自订阅 — state 由父组件唯一订阅后经 props 传入
    (纯 props 子组件).
    SQL 结果从 ``state.sql_success``/``sql_result_columns``/``sql_result_rows`` 读取 (L771 合规).
    """
    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 本地 UI 状态 ---
    sql_text, set_sql_text = ft.use_state("")
    status_text, set_status_text = ft.use_state(I18n.get("data_sql_ready"))
    status_color, set_status_color = ft.use_state(AppColors.TEXT_SECONDARY)
    # D6: SQL 错误原文折叠展示 (仅失败且有 raw_detail 时可见)
    detail_text, set_detail_text = ft.use_state("")

    # --- 异步 handler (R2: except Exception 不捕获 CancelledError) ---
    async def _run_query(e: ft.ControlEvent) -> None:
        if not sql_text:
            return
        UILogger.log_action("SQLConsoleTab", "Click", "btn_run_query")
        set_status_text(I18n.get("data_status_executing"))
        set_status_color(AppColors.INFO)
        set_detail_text("")
        try:
            start_time = time.time()
            await vm.execute_sql(sql_text)
            elapsed = time.time() - start_time
            # 重读 state 拿最新 snapshot (race safety)
            s = vm.state
            if s.sql_success:
                row_count = len(s.sql_result_rows)
                if row_count > 0:
                    MAX_ROWS_UI = 100
                    if row_count > MAX_ROWS_UI:
                        set_status_text(
                            I18n.get("data_sql_success_truncated").format(
                                time=elapsed, limit=MAX_ROWS_UI, rows=row_count
                            )
                        )
                    else:
                        set_status_text(I18n.get("data_sql_success").format(time=elapsed, rows=row_count))
                    set_status_color(AppColors.SUCCESS)
                else:
                    set_status_text(I18n.get("data_sql_error"))
                    set_status_color(AppColors.ERROR)
            else:
                # D6: 渲染 VM 产出的 SqlErrorInfo(key, params) + 脱敏原文折叠展示
                err = s.sql_error
                if err is not None:
                    set_status_text(I18n.get(err.message_key, **err.format_args))
                    set_detail_text(err.raw_detail or "")
                else:
                    set_status_text(I18n.get("data_sql_error"))
                set_status_color(AppColors.ERROR)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as exc:
            set_status_text(I18n.get("data_sys_error"))
            set_status_color(AppColors.ERROR)
            logger.error("SQL Execution error: %s", DataSanitizer.sanitize_error(exc))
            logger.debug("SQL Execution error traceback", exc_info=True)

    def _set_sql(sql: str) -> None:
        set_sql_text(sql)

    # --- 派生渲染数据 (声明式: 从 state 读取, L771 合规) ---
    MAX_ROWS_UI = 100
    all_sql_rows = state.sql_result_rows
    has_data = state.sql_success and bool(all_sql_rows)
    if has_data:
        display_rows = all_sql_rows[:MAX_ROWS_UI] if len(all_sql_rows) > MAX_ROWS_UI else all_sql_rows
        result_cols = _build_sql_columns_spec(state.sql_result_columns, vm)
        result_rows = _sql_rows_to_paginated_rows(display_rows, state.sql_result_columns)
    else:
        result_cols = []
        result_rows = []

    is_executing = state.sql_is_executing

    # --- 构建 UI ---
    # 安全提示：SQL 控制台仅允许 SELECT 查询（数据层有三层纵深防御：sqlparse 非 SELECT 拒绝 +
    # 危险关键词正则 + SET TRANSACTION READ ONLY 只读事务）。
    # 代码编辑器主题跟随应用主题（浅色→ATOM_ONE_LIGHT，深色→ATOM_ONE_DARK），
    # 使 text_style/gutter 颜色（AppColors.INPUT_TEXT/TEXT_HINT 随主题切换）与编辑器背景对比度始终可读。
    _is_light_theme = AppColors.get_current_theme_mode() == ft.ThemeMode.LIGHT
    sql_editor = fce.CodeEditor(
        language=fce.CodeLanguage.SQL,
        code_theme=fce.CodeTheme.ATOM_ONE_LIGHT if _is_light_theme else fce.CodeTheme.ATOM_ONE_DARK,
        value=sql_text,
        height=200,
        autofocus=False,
        autocomplete=True,
        autocomplete_words=_SQL_KEYWORDS,
        text_style=ft.TextStyle(
            font_family="Consolas, monospace",
            color=AppColors.INPUT_TEXT,
            size=AppStyles.FONT_SIZE_LG,
        ),
        gutter_style=fce.GutterStyle(
            show_line_numbers=True,
            show_folding_handles=True,
            width=48,
            text_style=ft.TextStyle(
                font_family="Consolas, monospace",
                color=AppColors.TEXT_HINT,
                size=AppStyles.FONT_SIZE_BODY_SM,
            ),
        ),
        on_change=lambda e: set_sql_text(e.data if e.data is not None else ""),
    )

    # 只读安全提示标签（替代原 TextField label/hint_text，明确告知用户仅允许 SELECT 查询）。
    sql_security_label = ft.Text(
        I18n.get("data_sql_hint"),
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_HINT,
        italic=True,
    )

    btn_run = ft.Button(
        I18n.get("data_sql_execute"),
        icon=ft.Icons.PLAY_ARROW,
        style=AppStyles.primary_button(),
        on_click=typing.cast(ft.ControlEventHandler, _run_query),
        disabled=is_executing,
    )

    progress_ring = ft.ProgressRing(
        width=16,
        height=16,
        stroke_width=2,
        visible=is_executing,
    )

    empty_hint_text = ft.Text(
        I18n.get("data_sql_empty_hint"),
        color=AppColors.TEXT_HINT,
        size=AppStyles.FONT_SIZE_LG,
    )
    empty_state = ft.Container(
        content=ft.Column(
            [
                ft.Container(height=40),
                ft.Icon(ft.Icons.TERMINAL, size=AppStyles.ICON_SIZE_XL, color=AppColors.TEXT_HINT),
                empty_hint_text,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.Alignment.CENTER,
        visible=not has_data,
    )

    result_table = ft.Container(
        content=PaginatedTable(rows=result_rows, columns=result_cols),
        visible=has_data,
        expand=True,
    )

    return ft.Column(
        [
            ft.Container(
                content=ft.Column(
                    [
                        sql_security_label,
                        sql_editor,
                        ft.Row(
                            [
                                btn_run,
                                progress_ring,
                                ft.Container(expand=True),
                                ft.Text(
                                    I18n.get("data_date_fmt_hint"),
                                    size=AppStyles.FONT_SIZE_CAPTION,
                                    color=AppColors.TEXT_HINT,
                                ),
                                ft.OutlinedButton(
                                    "SELECT * LIMIT 10",
                                    style=AppStyles.outline_button(),
                                    on_click=lambda e: _set_sql("SELECT * FROM stock_basic LIMIT 10"),
                                ),
                                ft.OutlinedButton(
                                    I18n.get("data_btn_count"),
                                    style=AppStyles.outline_button(),
                                    on_click=lambda e: _set_sql("SELECT COUNT(*) FROM daily_quotes"),
                                ),
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    # 关键: 内层 Column 必须 STRETCH, 否则 sql_editor 只取固有宽度 (~300px),
                    # 导致 SQL 输入框与程序窗口不成比例 (长 SQL 频繁换行).
                    horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                ),
                padding=AppStyles.SPACING_SM,
                bgcolor=AppColors.SURFACE,
                border=ft.Border.only(bottom=ft.BorderSide(1, AppColors.BORDER)),
            ),
            ft.Container(
                content=ft.Column(
                    [empty_state, result_table],
                    scroll=ft.ScrollMode.AUTO,
                ),
                expand=True,
                padding=AppStyles.SPACING_SM,
            ),
            ft.Container(
                content=ft.Text(status_text, size=AppStyles.FONT_SIZE_BODY_SM, color=status_color),
                padding=5,
                bgcolor=AppColors.SURFACE_VARIANT,
            ),
            ft.Container(
                # D6: SQL 错误原文折叠展示（raw_detail 存在时可见）
                content=ft.ExpansionTile(
                    title=ft.Text(
                        I18n.get("data_sql_error_detail"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.ERROR,
                    ),
                    maintain_state=False,
                    tile_padding=ft.Padding.symmetric(horizontal=8),
                    controls=[
                        ft.Text(
                            detail_text,
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.TEXT_SECONDARY,
                            selectable=True,
                        ),
                    ],
                ),
                visible=bool(detail_text),
                bgcolor=AppColors.SURFACE_VARIANT,
            ),
        ],
        expand=True,
        spacing=0,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )


# ============================================================================
# DataExplorerView
# ============================================================================


@ft.component
def DataExplorerView(active: bool = True) -> ft.Container:
    """数据浏览器主视图 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - DataExplorerViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - PubSub 通过 ``use_effect(setup, [], cleanup=cleanup)`` 订阅/退订
    - page 访问用 ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 子 Tab 为纯 props 组件 (state 由本组件唯一订阅后传入, D13 消除三重订阅)

    Args:
        active: 当前 tab 是否激活 (控制副作用执行)。
    """
    # --- VM (内部模式: hook 实例化 + 卸载时 dispose) ---
    # D13: 此处为唯一订阅点 — state 经 props 传下给纯子组件, 消除三重订阅重复渲染
    state, vm = use_viewmodel(factory=lambda: DataExplorerViewModel())

    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Tab 选中状态 ---
    selected_index, set_selected_index = ft.use_state(0)

    # --- PubSub 订阅/退订 (topic 精准退订, 避免误伤其他视图订阅) ---
    def _on_broadcast_message(topic: str, message: str) -> None:
        if topic == CACHE_CLEARED_TOPIC and message == "cache_cleared":
            vm.mark_tables_stale()
            logger.debug("[DataExplorerView] Cache cleared - will reload data on next view")

    async def _setup_pubsub() -> None:
        if not active:
            return
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.subscribe_topic(CACHE_CLEARED_TOPIC, _on_broadcast_message)
        except RuntimeError:
            pass

    async def _cleanup_pubsub() -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.pubsub.unsubscribe_topic(CACHE_CLEARED_TOPIC)
        except RuntimeError:
            pass

    ft.use_effect(_setup_pubsub, dependencies=[active], cleanup=_cleanup_pubsub)

    # --- 事件 handler ---
    def _on_tab_changed(e: ft.ControlEvent) -> None:
        new_index = get_control_attr(e.control, ft.Tabs, "selected_index") if e and e.control else 0
        set_selected_index(new_index)
        tab_name = "table_viewer" if new_index == 0 else "sql_console"
        UILogger.log_action("DataExplorerView", "Navigate", f"tab={tab_name}")

    # --- 构建 UI (V1 Tabs 三件套: Tabs + TabBar + TabBarView) ---
    tab_bar = ft.TabBar(
        tabs=[
            ft.Tab(label=I18n.get("data_tab_explorer"), icon=ft.Icons.TABLE_CHART),
            ft.Tab(label=I18n.get("data_tab_sql"), icon=ft.Icons.CODE),
        ],
    )
    tabs = ft.Tabs(
        length=2,
        selected_index=selected_index,
        animation_duration=300,
        expand=True,
        on_change=safe_on_change(_on_tab_changed),
        content=ft.Column(
            expand=True,
            controls=[
                tab_bar,
                ft.TabBarView(
                    expand=True,
                    controls=[TableViewerTab(state=state, vm=vm, active=active), SQLConsoleTab(state=state, vm=vm)],
                ),
            ],
        ),
    )

    return ft.Container(content=tabs, expand=True)
