import flet as ft
from ui.i18n import I18n
from ui.theme import AppColors
from data.database_manager import DatabaseManager
import logging
import traceback
import time

# --- Components (Inline for now, could be split later) ---

class TableViewerTab(ft.Container):
    """
    Tab 1: Visual Table Explorer with Filtering and Pagination
    """
    def __init__(self, db_manager: DatabaseManager):
        super().__init__()
        self.db_manager = db_manager
        self.current_table = "stock_basic" # Default table
        self.current_page = 1
        self.page_size = 50
        self.total_rows = 0
        self.table_columns = []
        
        # UI Elements
        self.table_selector = ft.Dropdown(
            width=250,
            label=I18n.get("data_select_table"),
            on_change=self._on_table_changed
        )
        
        # Filtering (Basic implementation: Single column filter)
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
        
        self.data_table = ft.DataTable(
            columns=[ft.DataColumn(ft.Text("Loading..."))],
            rows=[],
            vertical_lines=dict(width=1, color=ft.Colors.GREY_100),
            horizontal_lines=dict(width=1, color=ft.Colors.GREY_100),
            heading_row_color=ft.Colors.GREY_100,
            heading_row_height=40,
            data_row_max_height=40,
            column_spacing=20,
        )
        
        # Pagination
        self.btn_prev = ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=self._on_prev_page)
        self.btn_next = ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=self._on_next_page)
        self.txt_page = ft.Text(I18n.get("data_page_num").format(current=1, total=1))
        
        self.content = self._build_layout()
        
    def _build_layout(self):
        # Toolbar
        toolbar = ft.Container(
            content=ft.Row([
                self.table_selector,
                ft.VerticalDivider(),
                self.filter_col,
                self.filter_op,
                self.filter_val,
                ft.IconButton(ft.Icons.SEARCH, tooltip=I18n.get("common_query"), on_click=self._on_query_click, icon_color=ft.Colors.BLUE),
                ft.IconButton(ft.Icons.REFRESH, tooltip=I18n.get("common_refresh"), on_click=self._on_refresh_click),
                ft.Container(expand=True),
                ft.PopupMenuButton(
                    icon=ft.Icons.DOWNLOAD,
                    tooltip=I18n.get("common_download"),
                    items=[
                        ft.PopupMenuItem(text=I18n.get("data_export_current"), on_click=lambda e: self._export_csv(current_page=True)),
                        ft.PopupMenuItem(text=I18n.get("data_export_all"), on_click=lambda e: self._export_csv(current_page=False)),
                    ]
                )
            ], alignment=ft.MainAxisAlignment.START),
            padding=10,
            bgcolor=ft.Colors.GREY_50,
            border=ft.border.only(bottom=ft.border.BorderSide(1, ft.Colors.GREY_200))
        )
        
        # Data Grid Container (Scrollable)
        grid_container = ft.Column(
            [ft.Row([self.data_table], scroll=ft.ScrollMode.ALWAYS)], 
            expand=True, 
            scroll=ft.ScrollMode.AUTO
        )
        
        # Pagination Bar
        self.txt_count_info = ft.Text("", size=12, color=ft.Colors.GREY)
        pagination_bar = ft.Container(
            content=ft.Row([
                self.txt_count_info,
                ft.Container(expand=True),
                self.btn_prev,
                self.txt_page,
                self.btn_next,
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            padding=ft.padding.symmetric(horizontal=20, vertical=5),
            bgcolor=ft.Colors.GREY_50,
            border=ft.border.only(top=ft.border.BorderSide(1, ft.Colors.GREY_200))
        )
        
        return ft.Column([toolbar, grid_container, pagination_bar], expand=True, spacing=0)

    def did_mount(self):
        # Load tables on mount
        tables = self.db_manager.get_all_tables()
        self.table_selector.options = [ft.dropdown.Option(t) for t in tables]
        if tables:
            # Default to stock_basic if exists, else first
            default_t = "stock_basic" if "stock_basic" in tables else tables[0]
            self.table_selector.value = default_t
            self.current_table = default_t
            self._load_schema_and_data()
        self.update()

    def _on_table_changed(self, e):
        self.current_table = self.table_selector.value
        self.current_page = 1
        # Clear filters
        self.filter_val.value = ""
        self._load_schema_and_data()
        self.update()

    def _load_schema_and_data(self):
        # 1. Get Schema
        schema = self.db_manager.get_table_schema(self.current_table)
        self.table_columns = [col['name'] for col in schema]
        
        # Update Filter Dropdown
        self.filter_col.options = [ft.dropdown.Option(col) for col in self.table_columns]
        if self.table_columns:
            self.filter_col.value = self.table_columns[0]
            
        # Update DataTable Columns
        self.data_table.columns = [
            ft.DataColumn(ft.Text(col, weight=ft.FontWeight.BOLD)) for col in self.table_columns
        ]
        
        # 2. Get Data Count
        self._update_data_view()

    def _update_data_view(self):
        # Build Filters
        filters = []
        if self.filter_val.value:
            filters.append((self.filter_col.value, self.filter_op.value, self.filter_val.value))
            
        # Get Count
        self.total_rows = self.db_manager.get_table_count(self.current_table, filters)
        total_pages = (self.total_rows // self.page_size) + 1
        
        # Get Data
        df = self.db_manager.query_table(
            self.current_table, 
            page=self.current_page, 
            page_size=self.page_size, 
            filters=filters
        )
        
        # Render Rows
        self.data_table.rows = []
        for _, row in df.iterrows():
            cells = [ft.DataCell(ft.Text(str(val), size=12, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)) for val in row]
            self.data_table.rows.append(ft.DataRow(cells=cells))
            
        # Update UI Labels
        self.txt_count_info.value = I18n.get("data_total_rows").format(count=self.total_rows)
        self.txt_page.value = I18n.get("data_page_num").format(current=self.current_page, total=total_pages)
        
        # Enable/Disable Buttons
        self.btn_prev.disabled = self.current_page <= 1
        self.btn_next.disabled = self.current_page >= total_pages
        
        self.update()

    def _on_query_click(self, e):
        self.current_page = 1
        self._update_data_view()

    def _on_refresh_click(self, e):
        self._update_data_view()

    def _on_prev_page(self, e):
        if self.current_page > 1:
            self.current_page -= 1
            self._update_data_view()

    def _on_next_page(self, e):
        # We can calculate max pages on fly
        total_pages = (self.total_rows // self.page_size) + 1
        if self.current_page < total_pages:
            self.current_page += 1
            self._update_data_view()

    def _export_csv(self, current_page=True):
        # TODO: Implement generic file picker for save location
        pass

class SQLConsoleTab(ft.Container):
    """
    Tab 2: Advanced SQL Console
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
            text_style=ft.TextStyle(font_family="Consolas, monospace") # Monospace font if possible
        )
        
        self.btn_run = ft.ElevatedButton(
            I18n.get("data_sql_execute"), 
            icon=ft.Icons.PLAY_ARROW, 
            bgcolor=ft.Colors.BLUE,
            color=ft.Colors.WHITE,
            on_click=self._run_query
        )
        
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
                        ft.Container(expand=True),
                        ft.OutlinedButton("SELECT * LIMIT 10", on_click=lambda e: self._set_sql("SELECT * FROM stock_basic LIMIT 10")),
                        ft.OutlinedButton(I18n.get("data_btn_count"), on_click=lambda e: self._set_sql("SELECT COUNT(*) FROM daily_quotes")),
                    ])
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

    def _run_query(self, e):
        sql = self.sql_editor.value
        if not sql:
            return
            
        start_time = time.time()
        result = self.db_manager.execute_sql(sql)
        elapsed = time.time() - start_time
        
        if result['success']:
            df = result['data']
            # Truncate if too large to prevent UI freeze
            MAX_ROWS_UI = 100
            if len(df) > MAX_ROWS_UI:
                self.status_text.value = I18n.get("data_sql_success_truncated").format(time=elapsed, limit=MAX_ROWS_UI, rows=len(df))
                df = df.head(MAX_ROWS_UI)
            else:
                self.status_text.value = I18n.get("data_sql_success").format(time=elapsed, rows=len(df))
            self.status_text.color = ft.Colors.GREEN
            
            # Rebuild Table
            self.result_table.columns = [
                ft.DataColumn(ft.Text(col, weight=ft.FontWeight.BOLD)) for col in df.columns
            ]
            
            self.result_table.rows = []
            for _, row in df.iterrows():
                cells = [ft.DataCell(ft.Text(str(val), size=12)) for val in row]
                self.result_table.rows.append(ft.DataRow(cells=cells))
                
        else:
            self.status_text.value = I18n.get("data_sql_error").format(error=result['error'])
            self.status_text.color = ft.Colors.RED
            self.result_table.rows = []
            
        self.update()

class DataExplorerView(ft.Container):
    """
    Main View Container for Data Explorer
    """
    def __init__(self):
        super().__init__()
        self.expand = True
        self.db_manager = DatabaseManager()
        
        self.tabs = ft.Tabs(
            selected_index=0,
            animation_duration=300,
            tabs=[
                ft.Tab(
                    text=I18n.get("data_tab_viewer"),
                    icon=ft.Icons.TABLE_CHART,
                    content=TableViewerTab(self.db_manager),
                ),
                ft.Tab(
                    text=I18n.get("data_tab_sql"),
                    icon=ft.Icons.CODE,
                    content=SQLConsoleTab(self.db_manager),
                ),
            ],
            expand=True,
        )
        
        self.content = self.tabs

