"""data_view — 声明式组件 (Phase F.2).

从命令式容器子类重写为 ft.component 装饰器 + use_viewmodel 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 三个命令式 class (TableViewerTab/SQLConsoleTab/DataExplorerView) → ft.component 函数组件
- DataExplorerView 通过 ``use_viewmodel(factory=)`` 内部模式实例化 DataExplorerViewModel
- TableViewerTab/SQLConsoleTab 通过 ``use_viewmodel(vm=)`` 外部模式订阅 VM state
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

import flet as ft
import pandas as pd

from ui.components.virtual_table import PaginatedTable
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import CACHE_CLEARED_TOPIC
from ui.theme import AppColors, AppStyles
from ui.viewmodels.data_explorer_view_model import DataExplorerViewModel, SqlResultRow, TableRow
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level pure helpers
# ============================================================================


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


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
def TableViewerTab(vm: DataExplorerViewModel, active: bool = True) -> ft.Column:
    """Tab 1: 可视化表浏览器 (声明式).

    通过 ``use_viewmodel(vm=)`` 外部模式订阅 VM state 变化触发重渲染。
    FilePicker 通过 ``use_ref`` + ``use_effect`` 注册到 ``page.services``。
    """
    # --- 订阅 VM state (外部模式) ---
    state, _ = use_viewmodel(vm=vm)

    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 本地 UI 状态 (输入框值, 用户覆盖) ---
    filter_col_override, set_filter_col_override = ft.use_state(None)
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
        if page is not None and file_picker not in page.services:
            page.services.append(file_picker)

    def _cleanup_file_picker() -> None:
        page = _get_page()
        if page is not None and file_picker in page.services:
            page.services.remove(file_picker)

    ft.use_effect(_setup_file_picker, dependencies=[active], cleanup=_cleanup_file_picker)

    # --- 异步加载逻辑 (R2: except Exception 不捕获 CancelledError) ---
    async def _load_schema_and_data() -> None:
        if state.is_loading:
            return
        try:
            await vm.load_table_schema(state.current_table)
            await vm.query_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] load_schema error: %s", e, exc_info=True)
            page = _get_page()
            if page is not None:
                page.show_toast(I18n.get("data_err_load_schema"), "error")

    async def _init_tables() -> None:
        if not active:
            return
        if state.tables_loaded:
            return
        try:
            tables = await vm.init_tables()
            if tables:
                await _load_schema_and_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] init_tables error: %s", e, exc_info=True)
            page = _get_page()
            if page is not None:
                page.show_toast(I18n.get("data_err_load_schema"), "error")

    # tables_loaded 变化时触发 (mount + cache_cleared stale 重载)
    ft.use_effect(_init_tables, dependencies=[state.tables_loaded, active])

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
            logger.error("[TableViewerTab] query error: %s", e, exc_info=True)

    async def _do_refresh() -> None:
        ensure_correlation_id()
        UILogger.log_action("TableViewerTab", "Click", "btn_refresh")
        try:
            await vm.query_data()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] refresh error: %s", e, exc_info=True)

    async def _do_sort_query() -> None:
        try:
            await vm.query_data(page=1)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("[TableViewerTab] sort query error: %s", e, exc_info=True)

    async def _do_prev_page() -> None:
        UILogger.log_action("TableViewerTab", "Click", "btn_prev_page")
        if state.current_page > 1:
            try:
                await vm.query_data(page=state.current_page - 1)
            except asyncio.CancelledError:
                raise  # R2: 必须传播
            except Exception as e:
                logger.error("[TableViewerTab] prev page error: %s", e, exc_info=True)

    async def _do_next_page() -> None:
        UILogger.log_action("TableViewerTab", "Click", "btn_next_page")
        total_pages = _ceil_div(state.total_rows, state.page_size)
        if state.current_page < total_pages:
            try:
                await vm.query_data(page=state.current_page + 1)
            except asyncio.CancelledError:
                raise  # R2: 必须传播
            except Exception as e:
                logger.error("[TableViewerTab] next page error: %s", e, exc_info=True)

    async def _export_csv(current_page: bool = True) -> None:
        scope = "current_page" if current_page else "all"
        UILogger.log_action("TableViewerTab", "Click", f"export_csv={scope}")
        try:
            df = await vm.export_data(current_page_only=current_page)
            if df.empty:
                page = _get_page()
                if page is not None:
                    page.show_toast(I18n.get("data_export_no_data"), "error")
                return
            suffix = f"_p{state.current_page}" if current_page else "_all"
            timestamp = get_now().strftime("%Y%m%d_%H%M%S")
            default_filename = f"{state.current_table}{suffix}_{timestamp}.csv"
            filepath = await file_picker.save_file(
                dialog_title=I18n.get("data_export_save_title"),
                file_name=default_filename,
                allowed_extensions=["csv"],
            )
            if filepath:
                try:
                    await vm.write_csv(df, filepath)
                    filename = os.path.basename(filepath)
                    msg = I18n.get("data_export_success", file=filename)
                    page = _get_page()
                    if page is not None:
                        page.show_toast(msg, "success")
                except Exception as ex:
                    logger.error("Export write failed: %s", ex, exc_info=True)
                    page = _get_page()
                    if page is not None:
                        page.show_toast(I18n.get("data_export_fail"), "error")
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            logger.error("Export failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export failed traceback", exc_info=True)
            page = _get_page()
            if page is not None:
                page.show_toast(I18n.get("data_export_fail"), "error")

    # --- 同步事件 handler (调度 page.run_task) ---
    def _on_table_changed(e: ft.ControlEvent) -> None:
        new_table = e.control.value if e and e.control else None
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
            page.run_task(_export_csv, True)

    def _on_export_all(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_export_csv, False)

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
    table_selector = ft.Dropdown(
        width=250,
        label=I18n.get("data_select_table"),
        value=state.current_table or None,
        on_select=_on_table_changed,
        disabled=is_loading or not state.tables_loaded,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
        text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
        options=_build_table_selector_options(state.tables_list, vm),
        height=36,
        text_size=13,
        content_padding=10,
    )

    filter_col = ft.Dropdown(
        label=I18n.get("data_filter_col"),
        width=150,
        value=effective_filter_col,
        on_select=lambda e: set_filter_col_override(e.control.value if e and e.control else None),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
        text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
        options=_build_filter_col_options(state.current_table, state.table_columns, vm),
        height=36,
        text_size=13,
        content_padding=10,
    )

    filter_op = ft.Dropdown(
        label=I18n.get("data_filter_op"),
        width=100,
        value=filter_op_value,
        on_select=lambda e: set_filter_op_value(e.control.value if e and e.control else "="),
        options=_build_filter_op_options(),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
        text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
        height=36,
        text_size=13,
        content_padding=5,
    )

    filter_val = ft.TextField(
        label=I18n.get("data_filter_val"),
        width=200,
        value=filter_val_text,
        on_change=lambda e: set_filter_val_text(e.control.value if e and e.control else ""),
        on_submit=_on_query_click,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
        text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),
        height=36,
        text_size=13,
        content_padding=10,
    )

    btn_query = ft.IconButton(
        ft.Icons.SEARCH,
        tooltip=I18n.get("common_query"),
        on_click=_on_query_click,
        icon_color=AppColors.PRIMARY,
        icon_size=20,
        disabled=is_loading,
    )
    btn_refresh = ft.IconButton(
        ft.Icons.REFRESH,
        tooltip=I18n.get("common_refresh"),
        on_click=_on_refresh_click,
        icon_size=20,
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
                    padding=20,
                    border_radius=50,
                    bgcolor=ft.Colors.with_opacity(0.08, AppColors.PRIMARY),
                ),
                ft.Container(height=16),
                ft.Text(
                    I18n.get("data_loading"),
                    size=16,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Text(
                    I18n.get("data_loading_hint"),
                    size=13,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=40,
        bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.BLACK),
        border_radius=12,
        border=ft.Border.all(1, ft.Colors.with_opacity(0.1, AppColors.BORDER)),
    )

    # 表格区域: 加载中显示 loading widget, 否则显示 PaginatedTable
    if is_loading:
        grid_content = loading_widget
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
            ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT,
                tooltip=I18n.get("common_more_actions"),
                items=[
                    ft.PopupMenuItem(
                        content=I18n.get("data_export_current"),
                        icon=ft.Icons.DOWNLOAD,
                        on_click=_on_export_current,
                    ),
                    ft.PopupMenuItem(
                        content=I18n.get("data_export_all"),
                        icon=ft.Icons.DRIVE_FILE_MOVE,
                        on_click=_on_export_all,
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
            ft.Container(content=toolbar_content, padding=10, bgcolor=AppColors.SURFACE),
            ft.ProgressBar(visible=is_loading, color=AppColors.PRIMARY),
        ],
        spacing=0,
    )

    # 分页栏
    pagination_bar = ft.Container(
        content=ft.Row(
            [
                ft.Text(
                    I18n.get("data_total_rows").format(count=state.total_rows),
                    size=12,
                    color=ft.Colors.GREY,
                ),
                ft.Container(expand=True),
                ft.IconButton(
                    ft.Icons.CHEVRON_LEFT,
                    on_click=_on_prev_page,
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
                    on_click=_on_next_page,
                    disabled=is_loading or state.current_page >= total_pages,
                    tooltip=I18n.get("common_next_page"),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.Padding.symmetric(horizontal=20, vertical=5),
        bgcolor=AppColors.SURFACE,
        border=ft.Border.only(top=ft.BorderSide(1, AppColors.BORDER)),
    )

    return ft.Column(
        [toolbar_container, ft.Container(content=grid_content, expand=True), pagination_bar],
        expand=True,
        spacing=0,
    )


# ============================================================================
# SQLConsoleTab
# ============================================================================


@ft.component
def SQLConsoleTab(vm: DataExplorerViewModel) -> ft.Column:
    """Tab 2: SQL 控制台 (声明式).

    通过 ``use_viewmodel(vm=)`` 外部模式订阅 VM state 变化触发重渲染。
    SQL 结果从 ``state.sql_success``/``sql_result_columns``/``sql_result_rows`` 读取 (L771 合规).
    """
    # --- 订阅 VM state (外部模式) ---
    state, _ = use_viewmodel(vm=vm)

    # --- i18n / theme 订阅 (自动重渲染) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 本地 UI 状态 ---
    sql_text, set_sql_text = ft.use_state("")
    status_text, set_status_text = ft.use_state(I18n.get("data_sql_ready"))
    status_color, set_status_color = ft.use_state(AppColors.TEXT_SECONDARY)

    # --- 异步 handler (R2: except Exception 不捕获 CancelledError) ---
    async def _run_query(e: ft.ControlEvent) -> None:
        if not sql_text:
            return
        UILogger.log_action("SQLConsoleTab", "Click", "btn_run_query")
        set_status_text(I18n.get("data_status_executing"))
        set_status_color(ft.Colors.BLUE)
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
                    set_status_color(ft.Colors.GREEN)
                else:
                    set_status_text(I18n.get("data_sql_error"))
                    set_status_color(AppColors.ERROR)
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
    sql_editor = ft.TextField(
        multiline=True,
        min_lines=5,
        max_lines=10,
        text_size=14,
        label=I18n.get("data_sql_label"),
        hint_text=I18n.get("data_sql_hint"),
        value=sql_text,
        on_change=lambda e: set_sql_text(e.control.value if e and e.control else ""),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
        cursor_color=AppColors.PRIMARY,
        hint_style=ft.TextStyle(color=AppColors.TEXT_HINT),
        text_style=ft.TextStyle(
            font_family="Consolas, monospace",
            color=AppColors.INPUT_TEXT,
        ),
    )

    btn_run = ft.Button(
        I18n.get("data_sql_execute"),
        icon=ft.Icons.PLAY_ARROW,
        style=AppStyles.primary_button(),
        on_click=_run_query,
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
        size=14,
    )
    empty_state = ft.Container(
        content=ft.Column(
            [
                ft.Container(height=40),
                ft.Icon(ft.Icons.TERMINAL, size=48, color=AppColors.TEXT_HINT),
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
                        sql_editor,
                        ft.Row(
                            [
                                btn_run,
                                progress_ring,
                                ft.Container(expand=True),
                                ft.Text(
                                    I18n.get("data_date_fmt_hint"),
                                    size=11,
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
                ),
                padding=10,
                bgcolor=AppColors.SURFACE,
                border=ft.Border.only(bottom=ft.BorderSide(1, AppColors.BORDER)),
            ),
            ft.Container(
                content=ft.Column(
                    [empty_state, result_table],
                    scroll=ft.ScrollMode.AUTO,
                ),
                expand=True,
                padding=10,
            ),
            ft.Container(
                content=ft.Text(status_text, size=12, color=status_color),
                padding=5,
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
    - 子 Tab 通过 ``use_viewmodel(vm=)`` 外部模式订阅同一 VM
    """
    # --- VM (内部模式: hook 实例化 + 卸载时 dispose) ---
    _state, vm = use_viewmodel(factory=lambda: DataExplorerViewModel())

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
        new_index = e.control.selected_index if e and e.control else 0
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
        on_change=_on_tab_changed,
        content=ft.Column(
            expand=True,
            controls=[
                tab_bar,
                ft.TabBarView(
                    expand=True,
                    controls=[TableViewerTab(vm=vm, active=active), SQLConsoleTab(vm=vm)],
                ),
            ],
        ),
    )

    return ft.Container(content=tabs, expand=True)
