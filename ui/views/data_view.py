import asyncio
import datetime
import logging
import os
import time

import flet as ft
import pandas as pd

from data.persistence.metadata_manager import MetaDataManager
from ui.i18n import I18n, refresh_dropdown_options
from ui.theme import AppColors, AppStyles
from ui.viewmodels.data_explorer_view_model import DataExplorerViewModel
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


class TableViewerTab(ft.Container):
    """
    Tab 1: Visual Table Explorer with Filtering and Pagination
    Async implementation to prevent UI freezing.
    """

    def __init__(self, viewmodel: DataExplorerViewModel):
        super().__init__()
        self.vm = viewmodel
        self._pending_export_df = None  # Temp storage for export data

        self.save_file_picker = ft.FilePicker()  # pragma: no cover

        # UI Elements
        self.table_selector = ft.Dropdown(  # pragma: no cover
            width=250,  # pragma: no cover
            label=I18n.get("data_select_table"),  # pragma: no cover
            on_change=self._on_table_changed,  # pragma: no cover
            disabled=True,  # pragma: no cover
            bgcolor=AppColors.INPUT_BG,  # pragma: no cover
            color=AppColors.INPUT_TEXT,  # pragma: no cover
            border_color=AppColors.INPUT_BORDER,  # pragma: no cover
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),  # pragma: no cover
        )  # pragma: no cover

        # Loading Indicator
        self.progress_bar = ft.ProgressBar(  # pragma: no cover
            width=None,  # pragma: no cover
            visible=False,  # pragma: no cover
            color=AppColors.PRIMARY,  # pragma: no cover
        )  # pragma: no cover

        # Filtering
        self.filter_col = ft.Dropdown(  # pragma: no cover
            label=I18n.get("data_filter_col"),  # pragma: no cover
            width=150,  # pragma: no cover
            bgcolor=AppColors.INPUT_BG,  # pragma: no cover
            color=AppColors.INPUT_TEXT,  # pragma: no cover
            border_color=AppColors.INPUT_BORDER,  # pragma: no cover
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),  # pragma: no cover
        )  # pragma: no cover
        self.filter_op = ft.Dropdown(  # pragma: no cover
            label=I18n.get("data_filter_op"),  # pragma: no cover
            width=100,  # pragma: no cover
            options=[  # pragma: no cover
                ft.dropdown.Option("="),  # pragma: no cover
                ft.dropdown.Option("LIKE"),  # pragma: no cover
                ft.dropdown.Option(">"),  # pragma: no cover
                ft.dropdown.Option("<"),  # pragma: no cover
                ft.dropdown.Option(">="),  # pragma: no cover
                ft.dropdown.Option("<="),  # pragma: no cover
                ft.dropdown.Option("!="),  # pragma: no cover
            ],  # pragma: no cover
            value="=",  # pragma: no cover
            bgcolor=AppColors.INPUT_BG,  # pragma: no cover
            color=AppColors.INPUT_TEXT,  # pragma: no cover
            border_color=AppColors.INPUT_BORDER,  # pragma: no cover
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),  # pragma: no cover
        )  # pragma: no cover
        self.filter_val = ft.TextField(  # pragma: no cover
            label=I18n.get("data_filter_val"),  # pragma: no cover
            width=200,  # pragma: no cover
            on_submit=self._on_query_click,  # pragma: no cover
            bgcolor=AppColors.INPUT_BG,  # pragma: no cover
            color=AppColors.INPUT_TEXT,  # pragma: no cover
            border_color=AppColors.INPUT_BORDER,  # pragma: no cover
            text_style=ft.TextStyle(color=AppColors.INPUT_TEXT),  # pragma: no cover
        )  # pragma: no cover

        # Buttons
        self.btn_query = ft.IconButton(  # pragma: no cover
            ft.Icons.SEARCH,  # pragma: no cover
            tooltip=I18n.get("common_query"),  # pragma: no cover
            on_click=self._on_query_click,  # pragma: no cover
            icon_color=AppColors.PRIMARY,  # pragma: no cover
            icon_size=20,  # pragma: no cover
        )  # pragma: no cover
        self.btn_refresh = ft.IconButton(  # pragma: no cover
            ft.Icons.REFRESH,  # pragma: no cover
            tooltip=I18n.get("common_refresh"),  # pragma: no cover
            on_click=self._on_refresh_click,  # pragma: no cover
            icon_size=20,  # pragma: no cover
        )  # pragma: no cover

        # Professional Financial DataTable
        # Elegant Loading State - Modern centered card design
        # Store text references for dynamic i18n updates
        self._loading_text = ft.Text(  # pragma: no cover
            I18n.get("data_loading"),  # pragma: no cover
            size=16,  # pragma: no cover
            weight=ft.FontWeight.W_500,  # pragma: no cover
            color=AppColors.TEXT_PRIMARY,  # pragma: no cover
        )  # pragma: no cover
        self._loading_hint = ft.Text(  # pragma: no cover
            I18n.get("data_loading_hint"),  # pragma: no cover
            size=13,  # pragma: no cover
            color=AppColors.TEXT_SECONDARY,  # pragma: no cover
        )  # pragma: no cover
        self._loading_widget = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Container(  # pragma: no cover
                        content=ft.ProgressRing(  # pragma: no cover
                            width=48,  # pragma: no cover
                            height=48,  # pragma: no cover
                            stroke_width=4,  # pragma: no cover
                            color=AppColors.PRIMARY,  # pragma: no cover
                        ),  # pragma: no cover
                        padding=20,  # pragma: no cover
                        border_radius=50,  # pragma: no cover
                        bgcolor=ft.Colors.with_opacity(0.08, AppColors.PRIMARY),  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Container(height=16),  # pragma: no cover
                    self._loading_text,  # pragma: no cover
                    self._loading_hint,  # pragma: no cover
                ],  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                spacing=4,  # pragma: no cover
            ),  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
            expand=True,  # pragma: no cover
            padding=40,  # pragma: no cover
            bgcolor=ft.Colors.with_opacity(0.02, ft.Colors.BLACK),  # pragma: no cover
            border_radius=12,  # pragma: no cover
            border=ft.border.all(1, ft.Colors.with_opacity(0.1, AppColors.BORDER)),  # pragma: no cover
        )  # pragma: no cover

        self.data_table = ft.DataTable(  # pragma: no cover
            columns=[  # pragma: no cover
                ft.DataColumn(ft.Text(I18n.get("data_loading"))),  # pragma: no cover
            ],  # pragma: no cover
            rows=[],  # pragma: no cover
            vertical_lines=ft.BorderSide(1, AppColors.TABLE_GRID_V),  # pragma: no cover
            horizontal_lines=ft.BorderSide(1, AppColors.TABLE_GRID_H),  # pragma: no cover
            heading_row_color=AppColors.TABLE_HEADER_BG,  # pragma: no cover
            heading_row_height=42,  # pragma: no cover
            data_row_min_height=48,  # pragma: no cover
            data_row_max_height=float("inf"),  # pragma: no cover
            column_spacing=20,  # pragma: no cover
            horizontal_margin=16,  # pragma: no cover
            divider_thickness=0,  # pragma: no cover
            show_checkbox_column=False,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            border=ft.border.all(1, AppColors.TABLE_BORDER),  # pragma: no cover
        )  # pragma: no cover

        # Scrollable table wrapper
        self._table_scroll_wrapper = ft.Column(  # pragma: no cover
            [ft.Row([self.data_table], scroll=ft.ScrollMode.ALWAYS)],  # pragma: no cover
            expand=True,  # pragma: no cover
            scroll=ft.ScrollMode.AUTO,  # pragma: no cover
        )  # pragma: no cover

        # Conditional content container - swaps between loading and table
        self._grid_content = ft.Container(  # pragma: no cover
            content=self._loading_widget,  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

        # Pagination
        self.btn_prev = ft.IconButton(  # pragma: no cover
            ft.Icons.CHEVRON_LEFT,  # pragma: no cover
            on_click=self._on_prev_page,  # pragma: no cover
            disabled=True,  # pragma: no cover
        )  # pragma: no cover
        self.btn_next = ft.IconButton(  # pragma: no cover
            ft.Icons.CHEVRON_RIGHT,  # pragma: no cover
            on_click=self._on_next_page,  # pragma: no cover
            disabled=True,  # pragma: no cover
        )  # pragma: no cover
        self.txt_page = ft.Text(I18n.get("data_page_num").format(current=1, total=1))  # pragma: no cover
        self.txt_count_info = ft.Text("", size=12, color=ft.Colors.GREY)  # pragma: no cover

        self.content = self._build_layout()  # pragma: no cover

    def _build_layout(self):  # pragma: no cover
        # Toolbar
        toolbar_content = ft.Row(  # pragma: no cover
            [  # pragma: no cover
                self.table_selector,  # pragma: no cover
                ft.VerticalDivider(width=10, color=ft.Colors.TRANSPARENT),  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=ft.Row(  # pragma: no cover
                        [  # pragma: no cover
                            self.filter_col,  # pragma: no cover
                            self.filter_op,  # pragma: no cover
                            self.filter_val,  # pragma: no cover
                            self.btn_query,  # pragma: no cover
                            self.btn_refresh,  # pragma: no cover
                        ],  # pragma: no cover
                        spacing=5,  # pragma: no cover
                    ),  # pragma: no cover
                    padding=5,  # pragma: no cover
                    border=ft.border.all(1, AppColors.BORDER),  # pragma: no cover
                    border_radius=8,  # pragma: no cover
                    bgcolor=AppColors.SURFACE,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(expand=True),  # pragma: no cover
                ft.PopupMenuButton(  # pragma: no cover
                    icon=ft.Icons.MORE_VERT,  # pragma: no cover
                    tooltip=I18n.get("common_more_actions"),  # pragma: no cover
                    items=[  # pragma: no cover
                        ft.PopupMenuItem(  # pragma: no cover
                            text=I18n.get("data_export_current"),  # pragma: no cover
                            icon=ft.Icons.DOWNLOAD,  # pragma: no cover
                            on_click=lambda e: self.page.run_task(  # type: ignore[union-attr]  # pragma: no cover
                                self._export_csv,  # pragma: no cover
                                current_page=True,  # pragma: no cover
                            ),  # pragma: no cover
                        ),  # pragma: no cover
                        ft.PopupMenuItem(  # pragma: no cover
                            text=I18n.get("data_export_all"),  # pragma: no cover
                            icon=ft.Icons.DRIVE_FILE_MOVE,  # pragma: no cover
                            on_click=lambda e: self.page.run_task(  # pragma: no cover
                                self._export_csv,  # pragma: no cover
                                current_page=False,  # pragma: no cover
                            ),  # pragma: no cover
                        ),  # pragma: no cover
                    ],  # pragma: no cover
                ),  # pragma: no cover
                # 右侧留白：ft.Row 不支持 padding（Flet 0.28.3），用 Container 间隔器替代
                ft.Container(width=8),  # pragma: no cover
            ],  # pragma: no cover
            alignment=ft.MainAxisAlignment.START,  # pragma: no cover
            spacing=10,  # pragma: no cover
            scroll=ft.ScrollMode.AUTO,  # pragma: no cover
        )  # pragma: no cover

        # Update visuals for inputs to be 'Dense'
        for ctrl in [  # pragma: no cover
            self.table_selector,  # pragma: no cover
            self.filter_col,  # pragma: no cover
            self.filter_op,  # pragma: no cover
            self.filter_val,  # pragma: no cover
        ]:  # pragma: no cover
            ctrl.height = 36  # pragma: no cover
            ctrl.text_size = 13  # pragma: no cover
            ctrl.content_padding = 10  # pragma: no cover
            if hasattr(ctrl, "border"):  # pragma: no cover
                ctrl.border = "outline"  # pragma: no cover

        self.filter_op.content_padding = 5  # pragma: no cover

        toolbar_container = ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=toolbar_content,  # pragma: no cover
                    padding=10,  # pragma: no cover
                    bgcolor=AppColors.SURFACE,  # pragma: no cover
                ),  # pragma: no cover
                self.progress_bar,  # pragma: no cover
            ],  # pragma: no cover
            spacing=0,  # pragma: no cover
        )  # pragma: no cover

        # Data Grid Container - Uses conditional content rendering
        # Content is swapped between loading widget and table in _toggle_loading

        # Pagination Bar
        pagination_bar = ft.Container(  # pragma: no cover
            content=ft.Row(  # pragma: no cover
                [  # pragma: no cover
                    self.txt_count_info,  # pragma: no cover
                    ft.Container(expand=True),  # pragma: no cover
                    self.btn_prev,  # pragma: no cover
                    self.txt_page,  # pragma: no cover
                    self.btn_next,  # pragma: no cover
                ],  # pragma: no cover
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,  # pragma: no cover
            ),  # pragma: no cover
            padding=ft.padding.symmetric(horizontal=20, vertical=5),  # pragma: no cover
            bgcolor=AppColors.SURFACE,  # pragma: no cover
            border=ft.border.only(top=ft.border.BorderSide(1, AppColors.BORDER)),  # pragma: no cover
        )  # pragma: no cover

        return ft.Column(  # pragma: no cover
            [toolbar_container, self._grid_content, pagination_bar],  # pragma: no cover
            expand=True,  # pragma: no cover
            spacing=0,  # pragma: no cover
        )  # pragma: no cover

    def did_mount(self):  # pragma: no cover
        if getattr(self, "_mounted", False):
            return
        self._mounted = True
        if self.page:
            self.page.services.append(self.save_file_picker)
            self.page.update()

    def will_unmount(self):  # pragma: no cover
        self._mounted = False
        if self.page and getattr(self, "save_file_picker", None) in self.page.services:
            self.page.services.remove(self.save_file_picker)
            self.page.update()

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作）。

        由父视图 DataExplorerView.refresh_locale 级联调用，自身不订阅 I18n。
        """
        try:
            # 令 MetaDataManager 缓存失效，确保后续 get_table_alias/get_column_alias 用新 locale
            MetaDataManager.invalidate_cache()
            self.table_selector.label = I18n.get("data_select_table")
            refresh_dropdown_options(
                self.table_selector,
                [ft.dropdown.Option(key=t, text=MetaDataManager.get_table_alias(t)) for t in self.vm.tables_list],
            )

            self.filter_col.label = I18n.get("data_filter_col")
            self._populate_filter_columns()

            self.filter_op.label = I18n.get("data_filter_op")
            refresh_dropdown_options(
                self.filter_op,
                [
                    ft.dropdown.Option("="),
                    ft.dropdown.Option("LIKE"),
                    ft.dropdown.Option(">"),
                    ft.dropdown.Option("<"),
                    ft.dropdown.Option(">="),
                    ft.dropdown.Option("<="),
                    ft.dropdown.Option("!="),
                ],
            )

            self.filter_val.label = I18n.get("data_filter_val")
            self.btn_query.tooltip = I18n.get("common_query")
            self.btn_refresh.tooltip = I18n.get("common_refresh")
            self._loading_text.value = I18n.get("data_loading")
            self._loading_hint.value = I18n.get("data_loading_hint")

            # 重建表头以刷新列别名翻译
            if self.vm.table_columns:
                self._rebuild_table_columns()
                self._rebuild_table_rows()
                self._update_pagination_ui()
            else:
                # 加载占位文案
                self.data_table.columns = [ft.DataColumn(ft.Text(I18n.get("data_loading")))]
                self.data_table.rows = []

            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[TableViewerTab] refresh_locale error: %s", e, exc_info=True)

    async def did_mount_async(self):  # pragma: no cover
        # Skip re-loading if tables already loaded (switching back to this view)
        if self.vm.tables_loaded:
            logger.debug("[TableViewerTab] Skipping re-load - tables already loaded")
            return

        try:
            tables = await self.vm.init_tables()

            # Update UI on main thread
            self.table_selector.options = [
                ft.dropdown.Option(key=t, text=MetaDataManager.get_table_alias(t)) for t in tables
            ]
            self.table_selector.disabled = False

            if tables:
                self.table_selector.value = self.vm.current_table
                await self._load_schema_and_data()

            if self.page:
                self.update()

        except Exception as e:
            logger.error("Error loading tables: %s", e, exc_info=True)
            if self.page:
                self.page.show_toast(I18n.get("data_err_load_schema"), "error")  # type: ignore[untyped]

    async def _on_table_changed(self, e):  # pragma: no cover
        self.vm.current_table = self.table_selector.value
        UILogger.log_action("TableViewerTab", "Select", f"table={self.vm.current_table}")
        self.vm.reset_table_state()
        self.filter_val.value = ""  # Clear filters
        await self._load_schema_and_data()

    async def _toggle_loading(self, loading: bool):  # pragma: no cover
        self.progress_bar.visible = loading

        # Swap content: loading widget vs scrollable table
        if loading:
            # Update loading text with current locale (dynamic i18n)
            self._loading_text.value = I18n.get("data_loading")
            self._loading_hint.value = I18n.get("data_loading_hint")
            self._grid_content.content = self._loading_widget
        else:
            self._grid_content.content = self._table_scroll_wrapper

        self.btn_query.disabled = loading
        self.btn_refresh.disabled = loading
        self.btn_prev.disabled = loading or self.vm.current_page <= 1
        self.btn_next.disabled = loading  # Will be updated after load
        self.table_selector.disabled = loading
        # Guard: only call update() if control is mounted to page
        if self.page:
            self.update()

    async def _load_schema_and_data(self):  # pragma: no cover
        # Prevent concurrent loading (race condition guard)
        if self.vm.is_loading:
            logger.debug("[TableViewerTab] Skipped load - already loading")
            return

        try:
            await self._toggle_loading(True)

            # 1. Load schema via ViewModel
            await self.vm.load_table_schema(self.vm.current_table)

            # 2. Populate filter dropdown from vm state
            self._populate_filter_columns()

            # 3. Build DataTable columns from vm state
            self._rebuild_table_columns()

            # 4. Load data
            await self.vm.query_data()

            # 5. Render rows
            self._rebuild_table_rows()
            self._update_pagination_ui()

        except Exception as e:
            logger.error("Error loading schema: %s", e, exc_info=True)
            if self.page:
                self.page.show_toast(  # type: ignore[untyped]
                    I18n.get("data_err_load_schema"),
                    "error",
                )
        finally:
            try:
                await self._toggle_loading(False)
            except Exception as toggle_err:
                logger.debug("[_toggle_loading] finalization ignored: %s", toggle_err, exc_info=True)

    def _populate_filter_columns(self):
        """Fill filter column dropdown from vm.table_columns.

        经 refresh_locale 调用时 options 含 i18n 别名，需强制 dirty（§5.8 规范 4）。
        """
        self.filter_col.options = [
            ft.dropdown.Option(
                key=col,
                text=MetaDataManager.get_column_alias(self.vm.current_table, col),
            )
            for col in self.vm.table_columns
        ]
        if self.vm.table_columns:
            self.filter_col.value = None  # 强制触发 dirty（Flet 对相等值短路，§5.8 规范 4）
            self.filter_col.value = self.vm.table_columns[0]

    def _rebuild_table_columns(self):  # pragma: no cover
        """Rebuild DataTable columns from vm.table_columns and vm.numeric_cols."""
        self.data_table.columns = []
        for idx, col in enumerate(self.vm.table_columns):
            is_numeric = col in self.vm.numeric_cols
            header_text = MetaDataManager.get_column_alias(self.vm.current_table, col)

            self.data_table.columns.append(
                ft.DataColumn(
                    ft.Container(
                        content=ft.Text(
                            header_text,
                            weight=ft.FontWeight.W_600,
                            size=13,
                            color=AppColors.TABLE_HEADER_TEXT,
                            text_align=ft.TextAlign.CENTER,
                        ),
                        alignment=ft.alignment.center,
                        expand=True,
                        on_click=lambda e, i=idx: self.page.run_task(  # type: ignore[untyped]
                            self._on_sort,
                            i,
                        ),
                    ),
                    numeric=is_numeric,
                    on_sort=lambda e, i=idx: self.page.run_task(self._on_sort, i),  # type: ignore[untyped]
                ),
            )

        # Reset sort state display
        self.data_table.sort_column_index = self.vm.sort_col_index
        self.data_table.sort_ascending = self.vm.sort_asc

    def _rebuild_table_rows(self):  # pragma: no cover
        """Rebuild DataTable rows from vm.current_data."""
        df = self.vm.current_data
        current_columns = self.vm.table_columns
        self.data_table.rows = []
        for idx, (_, row) in enumerate(df.iterrows()):
            cells = []
            for col_name in current_columns:
                val = row.get(col_name)
                is_numeric = col_name in self.vm.numeric_cols

                # Formatting
                str_val = str(val)
                if val is None:
                    str_val = "-"
                elif "date" in col_name.lower():
                    if isinstance(val, (datetime.date, datetime.datetime)):
                        str_val = val.strftime("%Y-%m-%d")
                    elif isinstance(val, str) and len(val) == 8 and val.isdigit():
                        str_val = f"{val[:4]}-{val[4:6]}-{val[6:8]}"

                # 仅对 market_news 表的长文本字段使用左对齐和自动换行
                is_news_table = self.vm.current_table == "market_news"
                is_long_text = is_news_table and col_name.lower() in (
                    "content",
                    "tags",
                )

                cell_text = ft.Text(
                    str_val,
                    size=13,
                    max_lines=None if is_long_text else 1,  # 新闻内容不限制行数
                    overflow=ft.TextOverflow.VISIBLE if is_long_text else ft.TextOverflow.ELLIPSIS,
                    font_family="Roboto Mono"
                    if is_numeric or "code" in col_name.lower() or "date" in col_name.lower()
                    else None,
                    color=AppColors.TABLE_CELL_NUMERIC if is_numeric else AppColors.TABLE_CELL_TEXT,
                    text_align=ft.TextAlign.LEFT if is_long_text else ft.TextAlign.CENTER,  # 新闻内容左对齐
                )

                # 新闻内容使用左对齐容器，并设置固定宽度保证换行
                if is_long_text:
                    cell_container = ft.Container(
                        content=cell_text,
                        alignment=ft.alignment.top_left,
                        expand=True,  # 自适应宽度确保换行
                        padding=ft.padding.symmetric(vertical=5),
                    )
                else:
                    cell_container = ft.Container(
                        content=cell_text,
                        alignment=ft.alignment.center,
                        expand=True,
                    )

                cells.append(ft.DataCell(cell_container))

            row_color = AppColors.TABLE_ROW_ODD if idx % 2 == 0 else AppColors.TABLE_ROW_EVEN
            self.data_table.rows.append(ft.DataRow(cells=cells, color=row_color))

    def _update_pagination_ui(self):  # pragma: no cover
        """Update pagination controls from vm state."""
        total_pages = max(1, -(-self.vm.total_rows // self.vm.page_size))  # ceil division
        self.txt_count_info.value = I18n.get("data_total_rows").format(
            count=self.vm.total_rows,
        )
        self.txt_page.value = I18n.get("data_page_num").format(
            current=self.vm.current_page,
            total=total_pages,
        )
        self.btn_prev.disabled = self.vm.current_page <= 1
        self.btn_next.disabled = self.vm.current_page >= total_pages
        self.data_table.sort_column_index = self.vm.sort_col_index
        self.data_table.sort_ascending = self.vm.sort_asc

    async def _on_query_click(self, e):  # pragma: no cover
        ensure_correlation_id()
        UILogger.log_action("TableViewerTab", "Click", "btn_query")
        self.vm.set_filter(self.filter_col.value, self.filter_op.value, self.filter_val.value)
        try:
            await self._toggle_loading(True)
            await self.vm.query_data(page=1)
            self._rebuild_table_rows()
            self._update_pagination_ui()
        finally:
            await self._toggle_loading(False)

    async def _on_refresh_click(self, e):  # pragma: no cover
        ensure_correlation_id()
        UILogger.log_action("TableViewerTab", "Click", "btn_refresh")
        try:
            await self._toggle_loading(True)
            await self.vm.query_data()
            self._rebuild_table_rows()
            self._update_pagination_ui()
        finally:
            await self._toggle_loading(False)

    async def _on_sort(self, col_index):  # pragma: no cover
        # Type Guard: Ensure col_index is an integer
        if not isinstance(col_index, int):
            logger.warning(
                "[_on_sort] Invalid column index type: %s inside DataView. Expected int.",
                type(col_index),
            )
            return

        # Toggle sort direction
        if self.vm.sort_col_index == col_index:
            self.vm.set_sort(col_index, not self.vm.sort_asc)
        else:
            self.vm.set_sort(col_index, True)

        try:
            await self._toggle_loading(True)
            self.vm.clear_error()
            await self.vm.query_data(page=1)
            self._rebuild_table_rows()
            self._update_pagination_ui()
        finally:
            await self._toggle_loading(False)

    async def _on_prev_page(self, e):  # pragma: no cover
        UILogger.log_action("TableViewerTab", "Click", "btn_prev_page")
        if self.vm.current_page > 1:
            try:
                await self._toggle_loading(True)
                await self.vm.query_data(page=self.vm.current_page - 1)
                self._rebuild_table_rows()
                self._update_pagination_ui()
            finally:
                await self._toggle_loading(False)

    async def _on_next_page(self, e):  # pragma: no cover
        UILogger.log_action("TableViewerTab", "Click", "btn_next_page")
        total_pages = -(-self.vm.total_rows // self.vm.page_size)  # ceil division
        if self.vm.current_page < total_pages:
            try:
                await self._toggle_loading(True)
                await self.vm.query_data(page=self.vm.current_page + 1)
                self._rebuild_table_rows()
                self._update_pagination_ui()
            finally:
                await self._toggle_loading(False)

    async def _export_csv(self, current_page=True):  # pragma: no cover
        scope = "current_page" if current_page else "all"
        UILogger.log_action("TableViewerTab", "Click", f"export_csv={scope}")
        try:
            if self.progress_bar.visible:
                return
            await self._toggle_loading(True)

            df = await self.vm.export_data(current_page_only=current_page)

            if df.empty:
                self.page.show_toast(I18n.get("data_export_no_data"), "error")  # type: ignore[untyped]
                await self._toggle_loading(False)
                return

            suffix = f"_p{self.vm.current_page}" if current_page else "_all"
            timestamp = get_now().strftime("%Y%m%d_%H%M%S")
            default_filename = f"{self.vm.current_table}{suffix}_{timestamp}.csv"

            filepath = await self.save_file_picker.save_file(
                dialog_title=I18n.get("data_export_save_title"),
                file_name=default_filename,
                allowed_extensions=["csv"],
            )

            if filepath:
                try:
                    await ThreadPoolManager().run_async(
                        TaskType.CPU,
                        lambda: df.to_csv(filepath, index=False, encoding="utf-8-sig"),
                    )
                    filename = os.path.basename(filepath)
                    msg = I18n.get("data_export_success", file=filename)
                    self.page.show_toast(msg, "success")  # type: ignore[untyped]
                except Exception as ex:
                    logger.error("Export write failed: %s", ex, exc_info=True)
                    self.page.show_toast(  # type: ignore[untyped]
                        I18n.get("data_export_fail"),
                        "error",
                    )
            else:
                self._pending_export_df = None
        except Exception as e:
            logger.error("Export failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export failed traceback", exc_info=True)
            self.page.show_toast(  # type: ignore[untyped]
                I18n.get("data_export_fail"),
                "error",
            )
        finally:
            await self._toggle_loading(False)

    def update_theme(self):  # pragma: no cover
        """Update styles on theme change"""
        for ctrl in [
            self.table_selector,
            self.filter_col,
            self.filter_op,
            self.filter_val,
        ]:
            ctrl.bgcolor = AppColors.INPUT_BG
            ctrl.color = AppColors.INPUT_TEXT
            ctrl.border_color = AppColors.INPUT_BORDER
            ctrl.text_style = ft.TextStyle(color=AppColors.INPUT_TEXT)

        self.btn_query.icon_color = AppColors.PRIMARY

        self._loading_text.color = AppColors.TEXT_PRIMARY
        self._loading_hint.color = AppColors.TEXT_SECONDARY

        self.data_table.vertical_lines = ft.BorderSide(1, AppColors.TABLE_GRID_V)
        self.data_table.horizontal_lines = ft.BorderSide(1, AppColors.TABLE_GRID_H)
        self.data_table.heading_row_color = AppColors.TABLE_HEADER_BG
        self.data_table.border = ft.border.all(1, AppColors.TABLE_BORDER)

        for col in self.data_table.columns:
            if isinstance(col.label, ft.Container) and isinstance(
                col.label.content,
                ft.Text,
            ):
                col.label.content.color = AppColors.TABLE_HEADER_TEXT

        for i, row in enumerate(self.data_table.rows):  # type: ignore[arg-type]
            row.color = AppColors.TABLE_ROW_ODD if i % 2 == 0 else AppColors.TABLE_ROW_EVEN
            for cell in row.cells:
                content = cell.content
                if isinstance(content, ft.Container):
                    content = content.content
                if isinstance(content, ft.Text):
                    is_numeric = "Roboto" in (content.font_family or "")
                    content.color = AppColors.TABLE_CELL_NUMERIC if is_numeric else AppColors.TABLE_CELL_TEXT

        if self.page:
            self.update()


class SQLConsoleTab(ft.Container):
    """
    Tab 2: Advanced SQL Console
    Async implementation.
    """

    def __init__(self, viewmodel: DataExplorerViewModel):
        super().__init__()
        self.vm = viewmodel

        self.sql_editor = ft.TextField(  # pragma: no cover
            multiline=True,  # pragma: no cover
            min_lines=5,  # pragma: no cover
            max_lines=10,  # pragma: no cover
            text_size=14,  # pragma: no cover
            label=I18n.get("data_sql_label"),  # pragma: no cover
            hint_text=I18n.get("data_sql_hint"),  # pragma: no cover
            bgcolor=AppColors.INPUT_BG,  # pragma: no cover
            color=AppColors.INPUT_TEXT,  # pragma: no cover
            border_color=AppColors.INPUT_BORDER,  # pragma: no cover
            cursor_color=AppColors.PRIMARY,  # pragma: no cover
            hint_style=ft.TextStyle(color=AppColors.TEXT_HINT),  # pragma: no cover
            text_style=ft.TextStyle(  # pragma: no cover
                font_family="Consolas, monospace",  # pragma: no cover
                color=AppColors.INPUT_TEXT,  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.btn_run = ft.ElevatedButton(  # pragma: no cover
            I18n.get("data_sql_execute"),  # pragma: no cover
            icon=ft.Icons.PLAY_ARROW,  # pragma: no cover
            style=AppStyles.primary_button(),  # pragma: no cover
            on_click=self._run_query,  # pragma: no cover
        )  # pragma: no cover

        self.progress_ring = ft.ProgressRing(  # pragma: no cover
            width=16,  # pragma: no cover
            height=16,  # pragma: no cover
            stroke_width=2,  # pragma: no cover
            visible=False,  # pragma: no cover
        )  # pragma: no cover

        self.result_table = ft.DataTable(  # pragma: no cover
            columns=[ft.DataColumn(ft.Text(I18n.get("data_sql_result")))],  # pragma: no cover
            rows=[],  # pragma: no cover
            vertical_lines=ft.BorderSide(1, AppColors.TABLE_GRID_V),  # pragma: no cover
            horizontal_lines=ft.BorderSide(1, AppColors.TABLE_GRID_H),  # pragma: no cover
            heading_row_color=AppColors.TABLE_HEADER_BG,  # pragma: no cover
            border=ft.border.all(1, AppColors.TABLE_BORDER),  # pragma: no cover
            column_spacing=20,  # pragma: no cover
            visible=False,  # pragma: no cover
        )  # pragma: no cover

        self.empty_hint_text = ft.Text(  # pragma: no cover
            I18n.get("data_sql_empty_hint"),  # pragma: no cover
            color=AppColors.TEXT_HINT,  # pragma: no cover
            size=14,  # pragma: no cover
        )  # pragma: no cover
        self.empty_state = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.Container(height=40),  # pragma: no cover
                    ft.Icon(ft.Icons.TERMINAL, size=48, color=AppColors.TEXT_HINT),  # pragma: no cover
                    self.empty_hint_text,  # pragma: no cover
                ],  # pragma: no cover
                alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
            ),  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
            visible=True,  # pragma: no cover
        )  # pragma: no cover

        self.status_text = ft.Text(  # pragma: no cover
            I18n.get("data_sql_ready"),  # pragma: no cover
            size=12,  # pragma: no cover
            color=AppColors.TEXT_SECONDARY,  # pragma: no cover
        )  # pragma: no cover

        self.date_fmt_hint_text = ft.Text(  # pragma: no cover
            I18n.get("data_date_fmt_hint"),  # pragma: no cover
            size=11,  # pragma: no cover
            color=AppColors.TEXT_HINT,  # pragma: no cover
        )  # pragma: no cover
        self.btn_count = ft.OutlinedButton(  # pragma: no cover
            I18n.get("data_btn_count"),  # pragma: no cover
            style=AppStyles.outline_button(),  # pragma: no cover
            on_click=lambda e: self._set_sql(  # pragma: no cover
                "SELECT COUNT(*) FROM daily_quotes",  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.content = ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=ft.Column(  # pragma: no cover
                        [  # pragma: no cover
                            self.sql_editor,  # pragma: no cover
                            ft.Row(  # pragma: no cover
                                [  # pragma: no cover
                                    self.btn_run,  # pragma: no cover
                                    self.progress_ring,  # pragma: no cover
                                    ft.Container(expand=True),  # pragma: no cover
                                    self.date_fmt_hint_text,  # pragma: no cover
                                    ft.OutlinedButton(  # pragma: no cover
                                        "SELECT * LIMIT 10",  # pragma: no cover
                                        style=AppStyles.outline_button(),  # pragma: no cover
                                        on_click=lambda e: self._set_sql(  # pragma: no cover
                                            "SELECT * FROM stock_basic LIMIT 10",  # pragma: no cover
                                        ),  # pragma: no cover
                                    ),  # pragma: no cover
                                    self.btn_count,  # pragma: no cover
                                ],  # pragma: no cover
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                            ),  # pragma: no cover
                        ],  # pragma: no cover
                    ),  # pragma: no cover
                    padding=10,  # pragma: no cover
                    bgcolor=AppColors.SURFACE,  # pragma: no cover
                    border=ft.border.only(  # pragma: no cover
                        bottom=ft.border.BorderSide(1, AppColors.BORDER),  # pragma: no cover
                    ),  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=ft.Column(  # pragma: no cover
                        [
                            self.empty_state,
                            ft.Row([self.result_table], scroll=ft.ScrollMode.ALWAYS),
                        ],  # pragma: no cover
                        scroll=ft.ScrollMode.AUTO,  # pragma: no cover
                    ),  # pragma: no cover
                    expand=True,  # pragma: no cover
                    padding=10,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=self.status_text,  # pragma: no cover
                    padding=5,  # pragma: no cover
                    bgcolor=AppColors.SURFACE_VARIANT,  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            expand=True,  # pragma: no cover
            spacing=0,  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,  # pragma: no cover
        )  # pragma: no cover

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作）。

        由父视图 DataExplorerView.refresh_locale 级联调用，自身不订阅 I18n。
        注：status_text 为运行时动态文案，由 _run_query 流程自行管理，不在此刷新。
        """
        try:
            MetaDataManager.invalidate_cache()
            self.sql_editor.label = I18n.get("data_sql_label")
            self.sql_editor.hint_text = I18n.get("data_sql_hint")
            self.btn_run.text = I18n.get("data_sql_execute")
            self.empty_hint_text.value = I18n.get("data_sql_empty_hint")
            self.date_fmt_hint_text.value = I18n.get("data_date_fmt_hint")
            self.btn_count.text = I18n.get("data_btn_count")
            self.result_table.columns = [ft.DataColumn(ft.Text(I18n.get("data_sql_result")))]
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[SQLConsoleTab] refresh_locale error: %s", e, exc_info=True)

    def _set_sql(self, sql):  # pragma: no cover
        self.sql_editor.value = sql
        self.sql_editor.update()

    async def _run_query(self, e):
        sql = self.sql_editor.value
        if not sql:
            return

        UILogger.log_action("SQLConsoleTab", "Click", "btn_run_query")
        self.btn_run.disabled = True
        self.progress_ring.visible = True
        self.status_text.value = I18n.get("data_status_executing")
        self.status_text.color = ft.Colors.BLUE
        self.result_table.visible = False
        self.empty_state.visible = False
        self.update()

        has_data = False

        try:
            start_time = time.time()

            # Execute via ViewModel
            result = await self.vm.execute_sql(sql)

            elapsed = time.time() - start_time

            if result["success"]:
                df = result["data"]
                MAX_ROWS_UI = 100
                display_df = df

                if len(df) > MAX_ROWS_UI:
                    self.status_text.value = I18n.get(
                        "data_sql_success_truncated",
                    ).format(time=elapsed, limit=MAX_ROWS_UI, rows=len(df))
                    display_df = df.head(MAX_ROWS_UI)
                else:
                    self.status_text.value = I18n.get("data_sql_success").format(
                        time=elapsed,
                        rows=len(df),
                    )
                self.status_text.color = ft.Colors.GREEN

                # Rebuild Table on Main Thread
                self.result_table.columns = [
                    ft.DataColumn(
                        ft.Text(
                            MetaDataManager.get_column_alias(None, col),
                            weight=ft.FontWeight.BOLD,
                            color=AppColors.TABLE_HEADER_TEXT,
                        ),
                    )
                    for col in display_df.columns
                ]

                self.result_table.rows = []
                for row_idx, (_, row) in enumerate(display_df.iterrows()):
                    cells = []
                    for idx, val in enumerate(row):
                        col_name = display_df.columns[idx]
                        if val is None or pd.isna(val):
                            str_val = "-"
                        else:
                            str_val = str(val)
                        if "date" in col_name.lower():
                            if isinstance(val, (datetime.date, datetime.datetime)):
                                str_val = val.strftime("%Y-%m-%d")
                            elif isinstance(val, str) and len(val) == 8 and val.isdigit():
                                str_val = f"{val[:4]}-{val[4:6]}-{val[6:8]}"
                        cells.append(
                            ft.DataCell(
                                ft.Text(
                                    str_val,
                                    size=12,
                                    color=AppColors.TABLE_CELL_TEXT,
                                ),
                            ),
                        )

                    row_color = AppColors.TABLE_ROW_ODD if row_idx % 2 == 0 else AppColors.TABLE_ROW_EVEN
                    self.result_table.rows.append(
                        ft.DataRow(cells=cells, color=row_color),
                    )

                has_data = True

            else:
                self.status_text.value = I18n.get("data_sql_error")
                self.status_text.color = AppColors.ERROR
                self.result_table.rows = []

        except Exception as e:
            self.status_text.value = I18n.get(
                "data_sys_error",
            )
            self.status_text.color = AppColors.ERROR
            self.result_table.rows = []
            logger.error("SQL Execution error: %s", DataSanitizer.sanitize_error(e))
            logger.debug("SQL Execution error traceback", exc_info=True)
        finally:
            self.result_table.visible = has_data
            self.empty_state.visible = not has_data
            self.btn_run.disabled = False
            self.progress_ring.visible = False
            if self.page:
                self.update()

    def update_theme(self):  # pragma: no cover
        """Update styles on theme change"""
        self.sql_editor.bgcolor = AppColors.INPUT_BG
        self.sql_editor.color = AppColors.INPUT_TEXT
        self.sql_editor.border_color = AppColors.INPUT_BORDER
        self.sql_editor.cursor_color = AppColors.PRIMARY
        self.sql_editor.text_style = ft.TextStyle(
            font_family="Consolas, monospace",
            color=AppColors.INPUT_TEXT,
        )
        self.sql_editor.hint_style = ft.TextStyle(color=AppColors.TEXT_HINT)

        # Buttons
        self.btn_run.style = AppStyles.primary_button()

        self.result_table.vertical_lines = ft.BorderSide(1, AppColors.TABLE_GRID_V)
        self.result_table.horizontal_lines = ft.BorderSide(1, AppColors.TABLE_GRID_H)
        self.result_table.heading_row_color = AppColors.TABLE_HEADER_BG
        self.result_table.border = ft.border.all(1, AppColors.TABLE_BORDER)

        for col in self.result_table.columns:
            if isinstance(col.label, ft.Text):
                col.label.color = AppColors.TABLE_HEADER_TEXT

        # Table Rows
        for i, row in enumerate(self.result_table.rows):  # type: ignore[untyped]
            row.color = AppColors.TABLE_ROW_ODD if i % 2 == 0 else AppColors.TABLE_ROW_EVEN
            for cell in row.cells:
                if isinstance(cell.content, ft.Text):
                    cell.content.color = AppColors.TABLE_CELL_TEXT

        # Empty State
        if isinstance(self.empty_state.content, ft.Column):
            for ctrl in self.empty_state.content.controls:
                if isinstance(ctrl, ft.Icon | ft.Text):
                    ctrl.color = AppColors.TEXT_HINT

        if self.page:
            self.update()


class DataExplorerView(ft.Container):
    """
    Main View Container for Data Explorer
    Refactored to use Lazy Loading to prevent UI freeze during tab switch.
    """

    def __init__(self, viewmodel: DataExplorerViewModel | None = None):
        super().__init__()
        self.expand = True
        self.vm = viewmodel or DataExplorerViewModel()
        self._ui_built = False  # Track if UI has been built
        self._pubsub_subscribed = False
        self._mount_task = None
        self._locale_subscription_id: object | None = None
        self._loading_text = ft.Text(  # pragma: no cover
            I18n.get("data_loading"),  # pragma: no cover
            size=12,  # pragma: no cover
            color=AppColors.TEXT_SECONDARY,  # pragma: no cover
        )  # pragma: no cover

        # Start with a loading state to ensure instant tab switching
        self.loading_view = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.ProgressRing(),  # pragma: no cover
                    self._loading_text,  # pragma: no cover
                ],  # pragma: no cover
                alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
            ),  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

        self.content = self.loading_view  # pragma: no cover

    def did_mount(self):  # pragma: no cover
        """
        Trigger lazy initialization.
        """
        if getattr(self, "_mounted", False):
            return
        self._mounted = True
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)
        if self.page:
            self._mount_task = self.page.run_task(self.did_mount_async)  # type: ignore[untyped]

    def will_unmount(self):  # pragma: no cover
        """Clean up subscriptions when view is detached"""
        self._mounted = False
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
        if self.page and getattr(self, "_pubsub_subscribed", False):
            try:
                self.page.pubsub.unsubscribe(self._on_broadcast_message)  # type: ignore[untyped]
            except Exception as exc:
                logger.debug("[DataView] PubSub unsubscribe skipped: %s", exc, exc_info=True)
            self._pubsub_subscribed = False
        if self._mount_task:
            self._mount_task.cancel()
            self._mount_task = None
        self.vm.dispose()

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作）。

        级联调用 table_tab 和 sql_tab 的 refresh_locale。
        """
        try:
            self._loading_text.value = I18n.get("data_loading")
            if hasattr(self, "tabs"):
                # 刷新 Tabs 标题
                if len(self.tabs.tabs) >= 1:
                    self.tabs.tabs[0].text = I18n.get("data_tab_explorer")
                if len(self.tabs.tabs) >= 2:
                    self.tabs.tabs[1].text = I18n.get("data_tab_sql")
                # 级联调用子 tab 的 refresh_locale
                if hasattr(self, "table_tab"):
                    self.table_tab.refresh_locale()
                if hasattr(self, "sql_tab"):
                    self.sql_tab.refresh_locale()
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[DataExplorerView] refresh_locale error: %s", e, exc_info=True)

    async def did_mount_async(self):  # pragma: no cover
        import time as _time

        _t0 = _time.perf_counter()
        logger.debug("[PERF] >>> DataExplorerView.did_mount START")

        # Subscribe to broadcast messages (only once)
        if self.page and not self._pubsub_subscribed:
            self.page.pubsub.subscribe(self._on_broadcast_message)
            self._pubsub_subscribed = True

        # Start lazy build if not done
        if not self._ui_built:
            await self._lazy_build_ui()
            # _lazy_build_ui sets self.tabs if successful.
            # We only mark built if we actually have the content we expect.
            if hasattr(self, "tabs"):
                self._ui_built = True

        # Trigger data load for child tabs if needed
        # Check if tabs exists to avoid AttributeError
        if hasattr(self, "tabs") and self.tabs.selected_index == 0:
            # Prevent double-loading if _lazy_build_ui just ran (it calls did_mount_async internaly)
            # But TableViewerTab handles idempotency so it is safe.
            await self.table_tab.did_mount_async()

        logger.debug(
            "[PERF] <<< DataExplorerView.did_mount END (sync part) took %.1fms",
            (_time.perf_counter() - _t0) * 1000,
        )

    async def _lazy_build_ui(self):  # pragma: no cover
        import time as _time

        _t0 = _time.perf_counter()
        logger.debug("[PERF] >>> DataExplorerView._lazy_build_ui START")

        try:
            # Check if still mounted after potential delay
            if not self.page:
                logger.debug(
                    "[DataExplorerView] View unmounted before build, aborting.",
                )
                return

            # Create complex tabs here
            self.table_tab = TableViewerTab(self.vm)
            self.sql_tab = SQLConsoleTab(self.vm)

            self.tabs = ft.Tabs(  # pragma: no cover
                selected_index=0,  # pragma: no cover
                animation_duration=300,  # pragma: no cover
                tabs=[  # pragma: no cover
                    ft.Tab(  # pragma: no cover
                        text=I18n.get("data_tab_explorer"),  # pragma: no cover
                        icon=ft.Icons.TABLE_CHART,  # pragma: no cover
                        content=self.table_tab,  # pragma: no cover
                    ),  # pragma: no cover
                    ft.Tab(  # pragma: no cover
                        text=I18n.get("data_tab_sql"),  # pragma: no cover
                        icon=ft.Icons.CODE,  # pragma: no cover
                        content=self.sql_tab,  # pragma: no cover
                    ),  # pragma: no cover
                ],  # pragma: no cover
                expand=True,  # pragma: no cover
                on_change=self._on_tab_changed,  # pragma: no cover
            )  # pragma: no cover

            # Swap content
            self.content = self.tabs

            if self.page:
                self.update()
                # Yield to Flet event loop to ensure child controls are fully mounted
                # before triggering data load (prevents 'Control must be added to page first')
                await asyncio.sleep(0)
                # 生命周期兜底：若语言切换发生在 _lazy_build_ui 完成前，
                # refresh_locale 会因 hasattr(self, "tabs") 为 False 而跳过级联，
                # 此处构建完成后显式调用一次 refresh_locale 兜底（§5.8 规范 7）。
                self.refresh_locale()

        except Exception as e:
            logger.error("Error building DataExplorerView: %s", e, exc_info=True)
            self.content = ft.Text(f"Error loading view: {e}", color=ft.Colors.RED)
            if self.page:
                self.update()

        logger.debug(
            "[PERF] <<< DataExplorerView._lazy_build_ui END took %.1fms",
            (_time.perf_counter() - _t0) * 1000,
        )

    def _on_tab_changed(self, e):  # pragma: no cover
        if not self._ui_built:
            return

        tab_name = "table_viewer" if self.tabs.selected_index == 0 else "sql_console"
        UILogger.log_action("DataExplorerView", "Navigate", f"tab={tab_name}")

        # Trigger async mount for logic if needed
        # We can use the page task to run async methods
        if self.tabs.selected_index == 0:
            self.page.run_task(self.table_tab.did_mount_async)  # type: ignore[untyped]

    def _on_broadcast_message(self, message):  # pragma: no cover
        if message == "cache_cleared":
            # Reset tables_loaded flag to force reload on next mount
            if self._ui_built:
                self.vm.tables_loaded = False
            logger.debug(
                "[DataExplorerView] Cache cleared - will reload data on next view",
            )

    def update_theme(self):  # pragma: no cover
        """Update styles on theme change"""
        if hasattr(self, "table_tab"):
            self.table_tab.update_theme()
        if hasattr(self, "sql_tab"):
            self.sql_tab.update_theme()
