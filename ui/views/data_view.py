import asyncio
import functools
import logging
import re
import time
import traceback

import flet as ft

from data.database_manager import DatabaseManager
from data.metadata_manager import MetaDataManager
from ui.i18n import I18n
from ui.theme import AppColors
from utils.thread_pool import ThreadPoolManager, TaskType

# Initialize logger properly
logger = logging.getLogger(__name__)


class TableViewerTab(ft.Container):
    """
    Tab 1: Visual Table Explorer with Filtering and Pagination
    Async implementation to prevent UI freezing.
    """

    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.current_table = "stock_basic"  # Default table
        self.current_page = 1
        self.page_size = 50
        self.total_rows = 0
        self.table_columns = []
        self.numeric_cols = set()  # Track numeric columns for alignment

        # Sorting state
        self.sort_col = None  # Currently sorted column
        self.sort_asc = True  # Sort direction (True = ASC, False = DESC)

        # UI Elements
        self.table_selector = ft.Dropdown(
            width=250,
            label=I18n.get("data_select_table"),
            on_change=self._on_table_changed,
            disabled=True  # Disabled until loaded
        )

        # Loading Indicator
        self.progress_bar = ft.ProgressBar(width=None, visible=False, color=AppColors.PRIMARY)

        # Filtering
        self.filter_col = ft.Dropdown(label=I18n.get("data_filter_col"), width=150)
        self.filter_op = ft.Dropdown(
            label=I18n.get("data_filter_op"),
            width=100,
            options=[
                ft.dropdown.Option("="),
                ft.dropdown.Option("LIKE"),
                ft.dropdown.Option(">"),
                ft.dropdown.Option("<"),
                ft.dropdown.Option(">="),
                ft.dropdown.Option("<="),
                ft.dropdown.Option("!="),
            ],
            value="="
        )
        self.filter_val = ft.TextField(label=I18n.get("data_filter_val"), width=200, on_submit=self._on_query_click)

        # Buttons
        self.btn_query = ft.IconButton(
            ft.Icons.SEARCH,
            tooltip=I18n.get("common_query"),
            on_click=self._on_query_click,
            icon_color=AppColors.PRIMARY,
            icon_size=20
        )
        self.btn_refresh = ft.IconButton(
            ft.Icons.REFRESH,
            tooltip=I18n.get("common_refresh"),
            on_click=self._on_refresh_click,
            icon_size=20
        )

        # Professional Financial DataTable
        self.data_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("Loading..."))],
            rows=[],
            vertical_lines=ft.BorderSide(1, AppColors.TABLE_GRID_V),
            horizontal_lines=ft.BorderSide(1, AppColors.TABLE_GRID_H),
            heading_row_color=AppColors.TABLE_HEADER_BG,
            heading_row_height=42,
            data_row_min_height=40,
            data_row_max_height=40,
            column_spacing=20,
            horizontal_margin=16,
            divider_thickness=0,
            show_checkbox_column=False,
            border_radius=8,
            border=ft.border.all(1, AppColors.TABLE_BORDER),
        )

        # Pagination
        self.btn_prev = ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=self._on_prev_page, disabled=True)
        self.btn_next = ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=self._on_next_page, disabled=True)
        self.txt_page = ft.Text(I18n.get("data_page_num").format(current=1, total=1))
        self.txt_count_info = ft.Text("", size=12, color=ft.Colors.GREY)

        self.content = self._build_layout()

    def _build_layout(self):
        # Toolbar
        toolbar_content = ft.Row([
            self.table_selector,
            ft.VerticalDivider(width=10, color=ft.Colors.TRANSPARENT),

            # Filter Group
            ft.Container(
                content=ft.Row([
                    self.filter_col,
                    self.filter_op,
                    self.filter_val,
                    self.btn_query,
                    self.btn_refresh,
                ], spacing=5),
                padding=5,
                border=ft.border.all(1, AppColors.BORDER),
                border_radius=8,
                bgcolor=ft.Colors.WHITE,
            ),

            ft.Container(expand=True),

            # Actions
            ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT,
                tooltip=I18n.get("common_more_actions"),
                items=[
                    ft.PopupMenuItem(text=I18n.get("data_export_current"), icon=ft.Icons.DOWNLOAD,
                                     on_click=lambda e: asyncio.create_task(self._export_csv(current_page=True))),
                    ft.PopupMenuItem(text=I18n.get("data_export_all"), icon=ft.Icons.DRIVE_FILE_MOVE,
                                     on_click=lambda e: asyncio.create_task(self._export_csv(current_page=False))),
                ]
            )
        ], alignment=ft.MainAxisAlignment.START, spacing=10)

        # Update visuals for inputs to be 'Dense'
        for ctrl in [self.table_selector, self.filter_col, self.filter_op, self.filter_val]:
            ctrl.height = 36
            ctrl.text_size = 13
            ctrl.content_padding = 10
            if hasattr(ctrl, 'border'): ctrl.border = "outline"

        self.filter_op.content_padding = 5

        toolbar_container = ft.Column([
            ft.Container(
                content=toolbar_content,
                padding=10,
                bgcolor=ft.Colors.WHITE,
            ),
            self.progress_bar  # Loading bar right below toolbar
        ], spacing=0)

        # Data Grid Container (Scrollable)
        grid_container = ft.Column(
            [ft.Row([self.data_table], scroll=ft.ScrollMode.ALWAYS)],
            expand=True,
            scroll=ft.ScrollMode.AUTO
        )

        # Pagination Bar
        pagination_bar = ft.Container(
            content=ft.Row([
                self.txt_count_info,
                ft.Container(expand=True),
                self.btn_prev,
                self.txt_page,
                self.btn_next,
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.padding.symmetric(horizontal=20, vertical=5),
            bgcolor=ft.Colors.WHITE,
            border=ft.border.only(top=ft.border.BorderSide(1, AppColors.BORDER))
        )

        return ft.Column([toolbar_container, grid_container, pagination_bar], expand=True, spacing=0)

    async def did_mount_async(self):
        """Called when the control is added to the page (Async wrapper manually called)"""
        # Note: standard did_mount is sync. we call this manually or use create_task in init if possible.
        # But safest is to trigger from a known start point. 
        # In this architecture, we can just launch the task.
        try:
            # Run db fetch in executor (CPU Pool)
            tables = await ThreadPoolManager().run_async(TaskType.CPU, self.db_manager.get_all_tables)

            # Update UI on main thread
            self.table_selector.options = [ft.dropdown.Option(key=t, text=MetaDataManager.get_table_alias(t)) for t in
                                           tables]
            self.table_selector.disabled = False

            if tables:
                default_t = "stock_basic" if "stock_basic" in tables else tables[0]
                self.table_selector.value = default_t
                self.current_table = default_t
                await self._load_schema_and_data()

            self.update()
        except Exception as e:
            logger.error(f"Error loading tables: {e}")
            if self.page:
                self.page.show_toast(f"Error loading tables: {e}", "error")

    async def _on_table_changed(self, e):
        self.current_table = self.table_selector.value
        self.current_page = 1
        self.filter_val.value = ""  # Clear filters
        await self._load_schema_and_data()

    async def _toggle_loading(self, loading: bool):
        self.progress_bar.visible = loading
        self.btn_query.disabled = loading
        self.btn_refresh.disabled = loading
        self.btn_prev.disabled = loading or self.current_page <= 1
        self.btn_next.disabled = loading  # Will be updated after load
        self.table_selector.disabled = loading
        self.update()

    async def _load_schema_and_data(self):
        await self._toggle_loading(True)
        try:

            # 1. Get Schema
            schema = await ThreadPoolManager().run_async(TaskType.CPU, self.db_manager.get_table_schema,
                                                         self.current_table)
            self.table_columns = [col['name'] for col in schema]

            # Detect numeric columns
            self.numeric_cols.clear()
            for col in schema:
                c_name = col['name']
                c_type = col.get('type', '').upper()
                if any(x in c_type for x in ['INT', 'REAL', 'FLOAT', 'DOUBLE', 'NUMERIC', 'DECIMAL']):
                    self.numeric_cols.add(c_name)

            # Update Filter Dropdown
            self.filter_col.options = [
                ft.dropdown.Option(
                    key=col,
                    text=MetaDataManager.get_column_alias(self.current_table, col)
                ) for col in self.table_columns
            ]
            if self.table_columns:
                self.filter_col.value = self.table_columns[0]

            # Update DataTable Columns
            self.data_table.columns = []
            for col in self.table_columns:
                is_numeric = col in self.numeric_cols
                header_text = MetaDataManager.get_column_alias(self.current_table, col)

                # Bind click event with closure
                # Note: Flet events are simple, need to bridge to async
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
                            on_click=lambda e, c=col: asyncio.create_task(self._on_sort(c))
                            # Allow clicking header to sort
                        ),
                        numeric=is_numeric,
                        on_sort=lambda e, c=col: asyncio.create_task(self._on_sort(c)),
                    )
                )

            # Reset sorting
            self.sort_col = None
            self.sort_asc = True

            # 2. Get Data
            await self._refresh_data_rows()

        except Exception as e:
            logger.error(f"Error loading schema: {e}")
            logger.error(traceback.format_exc())
            if self.page:
                self.page.show_toast(f"Error loading schema: {e}", "error")
        finally:
            await self._toggle_loading(False)

    async def _refresh_data_rows(self):
        """Fetch count and data rows based on current state"""
        try:

            # Build Filters
            filters = []
            if self.filter_val.value:
                filter_col = self.filter_col.value
                filter_val = self.filter_val.value

                # Date format conversion
                if filter_col and 'date' in filter_col.lower():
                    if re.match(r'^\d{4}-\d{2}-\d{2}$', filter_val):
                        filter_val = filter_val.replace('-', '')

                filters.append((filter_col, self.filter_op.value, filter_val))

            # Run SQL queries in Executor
            self.total_rows = await ThreadPoolManager().run_async(TaskType.CPU, self.db_manager.get_table_count,
                                                                  self.current_table, filters)

            total_pages = max(1, (self.total_rows // self.page_size) + 1)

            df = await ThreadPoolManager().run_async(TaskType.CPU,
                                                     functools.partial(
                                                         self.db_manager.query_table,
                                                         self.current_table,
                                                         page=self.current_page,
                                                         page_size=self.page_size,
                                                         filters=filters,
                                                         sort_col=self.sort_col,
                                                         sort_asc=self.sort_asc
                                                     )
                                                     )

            # Render Rows (Main Thread)
            self.data_table.rows = []
            for idx, (_, row) in enumerate(df.iterrows()):
                cells = []
                for col_name in self.table_columns:
                    val = row[col_name]
                    is_numeric = col_name in self.numeric_cols

                    # Formatting
                    str_val = str(val)
                    if val is None:
                        str_val = "-"
                    elif 'date' in col_name.lower() and isinstance(val, str) and len(val) == 8 and val.isdigit():
                        str_val = f"{val[:4]}-{val[4:6]}-{val[6:8]}"

                    cell_text = ft.Text(
                        str_val,
                        size=13,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        font_family="Roboto Mono" if is_numeric or "code" in col_name.lower() or "date" in col_name.lower() else None,
                        color=AppColors.TABLE_CELL_NUMERIC if is_numeric else AppColors.TABLE_CELL_TEXT,
                        text_align=ft.TextAlign.CENTER,
                    )
                    cells.append(
                        ft.DataCell(ft.Container(content=cell_text, alignment=ft.alignment.center, expand=True)))

                row_color = AppColors.TABLE_ROW_ODD if idx % 2 == 0 else AppColors.TABLE_ROW_EVEN
                self.data_table.rows.append(ft.DataRow(cells=cells, color=row_color))

            # Update Info Labels
            self.txt_count_info.value = I18n.get("data_total_rows").format(count=self.total_rows)
            self.txt_page.value = I18n.get("data_page_num").format(current=self.current_page, total=total_pages)

            # Update Pagination Buttons
            self.btn_prev.disabled = self.current_page <= 1
            self.btn_next.disabled = self.current_page >= total_pages

            self.update()

        except Exception as e:
            logger.error(f"Error fetching data: {e}")
            logger.error(traceback.format_exc())

    async def _on_query_click(self, e):
        self.current_page = 1
        await self._toggle_loading(True)
        await self._refresh_data_rows()
        await self._toggle_loading(False)

    async def _on_refresh_click(self, e):
        await self._toggle_loading(True)
        await self._refresh_data_rows()
        await self._toggle_loading(False)

    async def _on_sort(self, col_name):
        if self.sort_col == col_name:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = col_name
            self.sort_asc = True

        self.current_page = 1
        await self._toggle_loading(True)
        await self._refresh_data_rows()
        await self._toggle_loading(False)

    async def _on_prev_page(self, e):
        if self.current_page > 1:
            self.current_page -= 1
            await self._toggle_loading(True)
            await self._refresh_data_rows()
            await self._toggle_loading(False)

    async def _on_next_page(self, e):
        total_pages = (self.total_rows // self.page_size) + 1
        if self.current_page < total_pages:
            self.current_page += 1
            await self._toggle_loading(True)
            await self._refresh_data_rows()
            await self._toggle_loading(False)

    async def _export_csv(self, current_page=True):
        import os
        from datetime import datetime

        try:
            if loading := self.progress_bar.visible: return  # Prevent double click
            await self._toggle_loading(True)

            await self._toggle_loading(True)

            # Prepare args (must be done on main thread if accessing UI controls)
            filters = []
            if self.filter_val.value:
                filter_col = self.filter_col.value
                filter_val = self.filter_val.value
                if filter_col and 'date' in filter_col.lower() and re.match(r'^\d{4}-\d{2}-\d{2}$', filter_val):
                    filter_val = filter_val.replace('-', '')
                filters.append((filter_col, self.filter_op.value, filter_val))

            query_func = functools.partial(
                self.db_manager.query_table,
                self.current_table,
                page=self.current_page if current_page else 1,
                page_size=self.page_size if current_page else 50000,
                filters=filters,
                sort_col=self.sort_col,
                sort_asc=self.sort_asc
            )

            # Execute in background
            df = await ThreadPoolManager().run_async(TaskType.CPU, query_func)

            if df.empty:
                self.page.show_toast(I18n.get("status_error") + ": No data to export", "error")
                return

            # Save File (IO operation, also good to keep off main thread if heavy, but usually fast enough)
            export_dir = "exports"
            if not os.path.exists(export_dir):
                os.makedirs(export_dir)

            suffix = f"_p{self.current_page}" if current_page else "_all"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.current_table}{suffix}_{timestamp}.csv"
            filepath = os.path.join(export_dir, filename)

            # Writing large CSV can also block, so we push it to executor
            await ThreadPoolManager().run_async(TaskType.CPU,
                                                lambda: df.to_csv(filepath, index=False, encoding='utf-8-sig'))

            msg = I18n.get("status_ready") + f": Exported to {filename}"
            self.page.show_toast(msg, "success")

        except Exception as e:
            logger.error(f"Export failed: {e}")
            self.page.show_toast(f"Export failed: {e}", "error")
        finally:
            await self._toggle_loading(False)


class SQLConsoleTab(ft.Container):
    """
    Tab 2: Advanced SQL Console
    Async implementation.
    """

    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager

        self.sql_editor = ft.TextField(
            multiline=True,
            min_lines=5,
            max_lines=10,
            text_size=14,
            label=I18n.get("data_sql_label"),
            hint_text=I18n.get("data_sql_hint"),
            border_color=ft.Colors.BLUE_400,
            text_style=ft.TextStyle(font_family="Consolas, monospace")
        )

        self.btn_run = ft.ElevatedButton(
            I18n.get("data_sql_execute"),
            icon=ft.Icons.PLAY_ARROW,
            bgcolor=ft.Colors.BLUE,
            color=ft.Colors.WHITE,
            on_click=self._run_query
        )

        self.progress_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)

        self.result_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text(I18n.get("data_sql_result")))],
            rows=[],
            vertical_lines=dict(width=1, color=ft.Colors.GREY_100),
            horizontal_lines=dict(width=1, color=ft.Colors.GREY_100),
            heading_row_color=ft.Colors.GREY_100,
        )

        self.status_text = ft.Text(I18n.get("data_sql_ready"), size=12, color=ft.Colors.GREY)

        self.content = ft.Column([
            ft.Container(
                content=ft.Column([
                    self.sql_editor,
                    ft.Row([
                        self.btn_run,
                        self.progress_ring,
                        ft.Container(expand=True),
                        ft.Text("💡 日期格式: YYYYMMDD (如 '20260203')", size=11, color=ft.Colors.GREY_500, italic=True),
                        ft.OutlinedButton("SELECT * LIMIT 10",
                                          on_click=lambda e: self._set_sql("SELECT * FROM stock_basic LIMIT 10")),
                        ft.OutlinedButton(I18n.get("data_btn_count"),
                                          on_click=lambda e: self._set_sql("SELECT COUNT(*) FROM daily_quotes")),
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER)
                ]),
                padding=10,
                bgcolor=ft.Colors.GREY_50,
                border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.GREY_200))
            ),
            ft.Container(
                content=ft.Column([
                    ft.Row([self.result_table], scroll=ft.ScrollMode.ALWAYS)
                ], scroll=ft.ScrollMode.AUTO),
                expand=True,
                padding=10
            ),
            ft.Container(
                content=self.status_text,
                padding=5,
                bgcolor=ft.Colors.GREY_100
            )
        ], expand=True, spacing=0)

    def _set_sql(self, sql):
        self.sql_editor.value = sql
        self.sql_editor.update()

    async def _run_query(self, e):
        sql = self.sql_editor.value
        if not sql:
            return

        self.btn_run.disabled = True
        self.progress_ring.visible = True
        self.status_text.value = "Executing..."
        self.status_text.color = ft.Colors.BLUE
        self.update()

        try:
            start_time = time.time()

            # Execute in Background
            result = await ThreadPoolManager().run_async(TaskType.CPU, self.db_manager.execute_sql, sql)

            elapsed = time.time() - start_time

            if result['success']:
                df = result['data']
                MAX_ROWS_UI = 100
                display_df = df

                if len(df) > MAX_ROWS_UI:
                    self.status_text.value = I18n.get("data_sql_success_truncated").format(time=elapsed,
                                                                                           limit=MAX_ROWS_UI,
                                                                                           rows=len(df))
                    display_df = df.head(MAX_ROWS_UI)
                else:
                    self.status_text.value = I18n.get("data_sql_success").format(time=elapsed, rows=len(df))
                self.status_text.color = ft.Colors.GREEN

                # Rebuild Table on Main Thread
                self.result_table.columns = [
                    ft.DataColumn(ft.Text(MetaDataManager.get_column_alias(None, col), weight=ft.FontWeight.BOLD)) for
                    col in display_df.columns
                ]

                self.result_table.rows = []
                for _, row in display_df.iterrows():
                    cells = []
                    for idx, val in enumerate(row):
                        col_name = display_df.columns[idx]
                        str_val = str(val)
                        if 'date' in col_name.lower() and isinstance(val, str) and len(val) == 8 and val.isdigit():
                            str_val = f"{val[:4]}-{val[4:6]}-{val[6:8]}"
                        cells.append(ft.DataCell(ft.Text(str_val, size=12)))
                    self.result_table.rows.append(ft.DataRow(cells=cells))

            else:
                self.status_text.value = I18n.get("data_sql_error").format(error=result['error'])
                self.status_text.color = ft.Colors.RED
                self.result_table.rows = []

        except Exception as e:
            self.status_text.value = f"System Error: {str(e)}"
            self.status_text.color = ft.Colors.RED
            logger.error(f"SQL Execution error: {e}")
        finally:
            self.btn_run.disabled = False
            self.progress_ring.visible = False
            self.update()


class DataExplorerView(ft.Container):
    """
    Main View Container for Data Explorer
    """

    def __init__(self):
        super().__init__()
        self.expand = True
        self.db_manager = DatabaseManager()

        self.table_tab = TableViewerTab(self.db_manager)
        self.sql_tab = SQLConsoleTab(self.db_manager)

        self.tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(
                    text=I18n.get("data_tab_viewer"),
                    icon=ft.Icons.TABLE_CHART,
                    content=self.table_tab,
                ),
                ft.Tab(
                    text=I18n.get("data_tab_sql"),
                    icon=ft.Icons.CODE,
                    content=self.sql_tab,
                ),
            ],
            expand=True,
        )

        self.content = self.tabs

    def did_mount(self):
        """
        Trigger async initialization.
        Since did_mount is sync, we schedule the async init on the existing loop.
        """
        # This is a critical bridge: Calling async logic from sync lifecycle hook
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.table_tab.did_mount_async())
        except RuntimeError:
            # Fallback if no loop (should not happen in Flet app)
            logger.warning("No running event loop found in DataExplorerView.did_mount")
