import logging

import flet as ft

from data.metadata_manager import MetaDataManager
from ui.components.virtual_table import VirtualTable
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.screener_view_model import ScreenerViewModel

logger = logging.getLogger(__name__)


class ScreenerView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__(expand=True)
        self._page_ref = page

        # ViewModel
        self.vm = ScreenerViewModel()

        # UI State
        self.selected_strategy = None
        self._pending_strategy_key = None  # For deep linking
        self.strategies = {}

        # --- UI Components ---
        # 1. Controls
        self.strategy_dropdown = ft.Dropdown(
            label=I18n.get("select_strategy"),
            options=[],
            on_change=self._on_strategy_change,
            width=300,
            text_size=14,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
            focused_border_color=AppColors.PRIMARY,
        )
        self.strategy_desc_text = ft.Text(
            "",
            size=12,
            color=AppColors.TEXT_SECONDARY,
            # italic removed for readability
            no_wrap=False,
            # max_lines removed for dynamic length
            # width removed to allowing resizing
        )

        self.run_btn = ft.ElevatedButton(
            text=I18n.get("run_screening"),
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_run_click,
            disabled=True,
            style=AppStyles.primary_button()  # Use factory style
        )
        self.export_btn = ft.ElevatedButton(
            text=I18n.get("screener_export"),
            icon=ft.Icons.DOWNLOAD,
            on_click=self._on_export_click,
            disabled=True,
            style=AppStyles.outline_button()  # Use factory style
        )
        self.status_text = ft.Text("", color=AppColors.TEXT_SECONDARY)
        self.progress_ring = ft.ProgressRing(visible=False, width=20, height=20, color=AppColors.ACCENT)

        # 2. Result Table (VirtualTable)
        self.result_table = VirtualTable(
            on_sort=self._on_virtual_sort
        )

        # 3. Logs (Virtualized via ListView)
        self.log_view = ft.ListView(
            expand=True,
            spacing=2,
            auto_scroll=True,
            item_extent=20
        )

        # 4. Pagination
        self.page_info_text = ft.Text(
            I18n.get("screener_page_info").format(current=1, total=1),
            color=AppColors.TEXT_PRIMARY
        )
        self.prev_btn = ft.IconButton(ft.Icons.CHEVRON_LEFT, on_click=lambda e: self._change_page(-1),
                                      icon_color=AppColors.PRIMARY)
        self.next_btn = ft.IconButton(ft.Icons.CHEVRON_RIGHT, on_click=lambda e: self._change_page(1),
                                      icon_color=AppColors.PRIMARY)

        # Layout
        self._setup_layout()

    def did_mount(self):
        # Initialize ViewModel and Bindings
        self.vm.bind(
            on_update=self._update_ui,
            on_log=self._append_log,
            on_status=self._update_status,
            on_progress=self._toggle_progress
        )

        # Load Strategies Async
        self.page.run_task(self._load_strategies)

    def will_unmount(self):
        self.vm.dispose()

    async def _load_strategies(self):
        try:
            strategies = await self.vm.get_strategies()
            if not self.page: return

            # Use Name (value) for display, Key for ID
            # strategies is Dict[key, name]
            self.strategy_dropdown.options = [
                ft.dropdown.Option(k, v)
                for k, v in strategies.items()
            ]
            self.strategy_dropdown.update()
        except Exception as e:
            logger.error(f"[ScreenerView] Failed to load strategies: {e}")
            self.status_text.value = I18n.get("screener_load_failed").format(error=e)
            self.status_text.color = AppColors.ERROR
            self.status_text.update()
            return

        # Handle Pending Deep Link
        if self._pending_strategy_key:
            logger.info(f"[ScreenerView] Executing pending strategy: {self._pending_strategy_key}")
            await self.select_and_run_strategy(self._pending_strategy_key)
            self._pending_strategy_key = None

    async def select_and_run_strategy(self, strategy_key: str):
        """Public API to select and run a strategy (Deep Link)"""
        if not self.strategy_dropdown.options:
            logger.info(f"[ScreenerView] Strategies not loaded yet. Queuing {strategy_key}")
            self._pending_strategy_key = strategy_key
            return

        # Validate existence
        exists = any(opt.key == strategy_key for opt in self.strategy_dropdown.options)
        if not exists:
            logger.warning(f"[ScreenerView] Strategy {strategy_key} not found.")
            return

        self.strategy_dropdown.value = strategy_key
        self.selected_strategy = strategy_key
        # Update description manually for deep link
        desc = self.vm.get_strategy_desc(strategy_key)
        self.strategy_desc_text.value = desc
        self.strategy_desc_text.update()
        self.strategy_dropdown.update()

        # Unlock button
        self.run_btn.disabled = False
        self.run_btn.update()

        # Execute
        self.log_view.controls.clear()
        self.log_view.update()  # clear logs visually immediately
        await self.vm.run_strategy(strategy_key)

    def _setup_layout(self):
        # Wrap Dropdown and Desc in a Column
        strategy_col = ft.Column([
            self.strategy_dropdown,
            self.strategy_desc_text
        ], spacing=2, alignment=ft.MainAxisAlignment.START, expand=True)

        toolbar = ft.Row([
            strategy_col,
            self.run_btn,
            self.export_btn,
            self.progress_ring,
            self.status_text
        ], alignment=ft.MainAxisAlignment.START, vertical_alignment=ft.CrossAxisAlignment.START)  # Align top

        result_area = ft.Column([
            ft.Row([
                self.result_table
            ], expand=True),  # Remove scroll=ADAPTIVE as VirtualTable handles scroll

            # Pagination
            ft.Row([
                self.prev_btn,
                self.page_info_text,
                self.next_btn
            ], alignment=ft.MainAxisAlignment.CENTER)
        ], expand=2)  # Take 2/3 space

        log_area = ft.Container(
            content=ft.Column([
                ft.Text(I18n.get("screener_log_title"), weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Container(
                    content=self.log_view,
                    border=ft.border.all(1, AppColors.BORDER),
                    border_radius=4,
                    bgcolor=AppColors.BACKGROUND,  # Darker bg for logs
                    padding=5,
                    expand=True
                )
            ]),
            expand=1,  # Take 1/3 space
            padding=ft.padding.only(left=10)
        )

        self.content = ft.Column([
            toolbar,
            ft.Divider(height=1, thickness=1, color=AppColors.DIVIDER),
            ft.Row([result_area, log_area], expand=True, spacing=10)
        ], expand=True, spacing=10, scroll=ft.ScrollMode.HIDDEN)

    # --- Event Handlers ---

    def _on_strategy_change(self, e):
        self.selected_strategy = self.strategy_dropdown.value
        self.run_btn.disabled = not self.selected_strategy

        # Update description text
        if self.selected_strategy:
            desc = self.vm.get_strategy_desc(self.selected_strategy)
            self.strategy_desc_text.value = desc
        else:
            self.strategy_desc_text.value = ""

        self.strategy_desc_text.update()
        self.run_btn.update()

    def _on_run_click(self, e):
        if not self.selected_strategy: return
        self.run_btn.disabled = True
        self.run_btn.update()
        self.log_view.controls.clear()
        self.page.run_task(self.vm.run_strategy, self.selected_strategy)

    def _toggle_progress(self, visible):
        self.progress_ring.visible = visible
        self.run_btn.disabled = visible
        self.strategy_dropdown.disabled = visible
        self.progress_ring.update()
        self.run_btn.update()
        self.strategy_dropdown.update()

    def _change_page(self, delta):
        self.page.run_task(self.vm.change_page, delta)

    def _on_virtual_sort(self, col_id, ascending):
        # Trigger sorting via ViewModel
        self.page.run_task(self.vm.sort_data, col_id)

    async def _on_export_click(self, e):
        """Export current results"""
        self.export_btn.disabled = True
        self.export_btn.update()

        try:
            path, error = await self.vm.export_results()
            if path:
                # Show snackbar info
                self.page.show_snack_bar(ft.SnackBar(content=ft.Text(I18n.get("data_export_success").format(file=path)),
                                                     bgcolor=AppColors.SUCCESS))
            else:
                self.page.show_snack_bar(ft.SnackBar(content=ft.Text(I18n.get("data_export_fail").format(error=error)),
                                                     bgcolor=AppColors.ERROR))
        except Exception as ex:
            logger.error(f"UI Export error: {ex}")
        finally:
            self.export_btn.disabled = False
            self.export_btn.update()

    # --- UI Update Callbacks ---

    def _update_ui(self):
        if not self.page: return
        # 1. Update Table
        self._render_table()

        # 2. Update Pagination
        self.page_info_text.value = I18n.get("screener_page_info").format(
            current=self.vm.page_no,
            total=self.vm.total_pages
        )
        self.prev_btn.disabled = self.vm.page_no <= 1
        self.next_btn.disabled = self.vm.page_no >= self.vm.total_pages

        # 3. Enable Export if data exists
        self.export_btn.disabled = self.vm.total_items == 0

        self.update()

    def _render_table(self):
        """Re-render table based on VM current page data"""
        df = self.vm.get_current_page_data()

        if df is None:
            # Clear table
            return

        # Define Columns (Dynamic based on data)
        # Map DataFrame columns to VirtualTable columns
        vt_columns = []
        for col in df.columns:
            # Determine width
            width = 100
            if col == 'ts_code':
                width = 100
            elif col == 'name':
                width = 120
            elif col in ['industry', 'area', 'list_date']:
                width = 100
            else:
                width = 80  # numeric cols usually smaller

            # Use MetaDataManager to get I18n label
            # Use 'screening_history' as context since results mostly align with it
            label = MetaDataManager.get_column_alias("screening_history", col)

            vt_columns.append({
                "id": col,
                "label": label,
                "width": width
            })

        self.result_table.set_columns(vt_columns)

        formatted_rows = []
        for _, row in df.iterrows():
            row_dict = {}
            for col in df.columns:
                val = row[col]
                if isinstance(val, (float, int)) and col not in ['volume', 'amount']:  # exclude large ints?
                    # Try to format floats
                    if isinstance(val, float):
                        row_dict[col] = f"{val:.2f}"
                    else:
                        row_dict[col] = str(val)
                else:
                    row_dict[col] = str(val)
            formatted_rows.append(row_dict)

        self.result_table.set_rows(
            formatted_rows,
            sort_col=self.vm.sort_column,
            sort_asc=self.vm.sort_ascending
        )

    def _append_log(self, name, score, thinking):
        if not self.page: return
        line = f"[{name}] {I18n.get('screener_score')}: {score} | {thinking[:50]}..."

        # Financial Terminal colors for logs
        color = AppColors.SUCCESS if score > 80 else AppColors.WARNING if score > 50 else AppColors.ERROR

        # Limit total logs to avoid memory leak (Basic virtualization)
        if len(self.log_view.controls) > 500:
            self.log_view.controls.pop(0)

        self.log_view.controls.append(
            ft.Text(line, color=color, size=12, no_wrap=True, font_family="Consolas, monospace"))
        self.log_view.update()

    def _update_status(self, msg, color=None):
        if not self.page: return
        self.status_text.value = msg
        self.status_text.color = color or AppColors.TEXT_PRIMARY
        self.status_text.update()

    def update_theme(self):
        """Update styles on theme change"""

        # 1. Controls (Update props always)
        self.strategy_dropdown.bgcolor = AppColors.INPUT_BG
        self.strategy_dropdown.border_color = AppColors.INPUT_BORDER
        self.strategy_dropdown.color = AppColors.INPUT_TEXT
        self.strategy_dropdown.focused_border_color = AppColors.PRIMARY

        self.run_btn.style = AppStyles.primary_button()
        self.export_btn.style = AppStyles.outline_button()

        self.status_text.color = AppColors.TEXT_SECONDARY
        self.progress_ring.color = AppColors.ACCENT

        # 2. Result Table (Update props always, VirtualTable.update_theme checks self.page for UI)
        self.result_table.update_theme()

        # 3. Logs (Update props always)
        self.log_view.bgcolor = AppColors.LOG_BG
        self.log_view.border = ft.border.all(1, AppColors.BORDER)

        # Log container theming logic
        try:
            # Structure: Column -> [Toolbar, Divider, Row]
            # Row -> [result_area(Col), log_area(Container)]
            main_row = self.content.controls[2]
            if isinstance(main_row, ft.Row) and len(main_row.controls) > 1:
                log_area = main_row.controls[1]
                if isinstance(log_area, ft.Container):
                    # Inner container
                    inner_col = log_area.content
                    if isinstance(inner_col, ft.Column) and len(inner_col.controls) > 1:
                        # Log Title
                        inner_col.controls[0].color = AppColors.TEXT_PRIMARY
                        # Log View Container
                        log_view_container = inner_col.controls[1]
                        log_view_container.border = ft.border.all(1, AppColors.BORDER)
                        log_view_container.bgcolor = AppColors.BACKGROUND
        except Exception as e:
            logger.warning(f"Failed to update log area theme: {e}")

        # 4. Pagination
        self.page_info_text.color = AppColors.TEXT_PRIMARY
        self.prev_btn.icon_color = AppColors.PRIMARY
        self.next_btn.icon_color = AppColors.PRIMARY

        # 5. UI Refresh (Only if mounted)
        if self.page:
            # Re-render table data to update cell colors
            try:
                self._render_table()
            except Exception as e:
                logger.error(f"Error re-rendering table on theme change: {e}")

            self.update()
