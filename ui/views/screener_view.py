import datetime
import logging
import os
import time

import flet as ft
import pandas as pd

from data.persistence.metadata_manager import MetaDataManager
from services.task_manager import TaskManager
from ui.components.stock_detail_dialog import StockDetailDialog
from ui.components.virtual_table import PaginatedTable
from ui.i18n import I18n, translate_strategy_name
from ui.theme import AppColors, AppStyles
from ui.viewmodels.screener_view_model import TASK_NAME_PREFIX, ScreenerViewModel
from utils.log_decorators import UILogger
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
                return f"{val / 1_000_000_000:.2f}{I18n.get('unit_yi', '亿')}"
            if val > 10_000:
                return f"{val / 10_000:.2f}{I18n.get('unit_wan', '万')}"
            return f"{val:,.0f}"
        if isinstance(val, float):
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
    formatted_rows = [{col: _format_cell_value(col, val) for col, val in row.items()} for row in records]
    return vt_columns, formatted_rows


class ScreenerView(ft.Container):
    def __init__(self, page: ft.Page):
        super().__init__(expand=True)
        self._page_ref = page

        # ViewModel
        self.vm = ScreenerViewModel()

        # UI State
        self.selected_strategy = None
        self._pending_strategy_key = None  # For deep linking

        self.save_file_picker = ft.FilePicker(on_result=self._on_save_file_result)

        # --- UI Components ---
        # 1. Controls
        self.strategy_dropdown = ft.Dropdown(
            label=I18n.get("select_strategy"),
            options=[],
            on_change=self._on_strategy_change,
            width=AppStyles.CONTROL_WIDTH_MD,
            text_size=14,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
            focused_border_color=AppColors.PRIMARY,
        )
        self.strategy_desc_text = ft.Text(
            I18n.get("screener_no_strategy_hint"),
            size=13,
            color=AppColors.TEXT_PRIMARY,
            no_wrap=False,
        )

        self.run_btn = ft.ElevatedButton(
            text=I18n.get("run_screening"),
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_run_click,
            disabled=True,
            style=AppStyles.primary_button(),
            height=45,
        )
        self.export_btn = ft.ElevatedButton(
            text=I18n.get("screener_export"),
            icon=ft.Icons.DOWNLOAD,
            on_click=self._on_export_click,
            disabled=True,
            style=AppStyles.outline_button(),
            height=45,
        )
        self.status_text = ft.Text("", color=AppColors.TEXT_SECONDARY)
        self.progress_ring = ft.ProgressRing(
            visible=False,
            width=20,
            height=20,
            color=AppColors.ACCENT,
        )

        self.result_table = PaginatedTable(on_sort=self._on_virtual_sort)

        # 3. Dynamic Strategy Parameters Panel
        self.params_container = ft.Column(spacing=8)

        # 4. Logs (Virtualized via Column for auto-scrolling)
        self.log_view = ft.Column(
            expand=True,
            spacing=4,
            scroll=ft.ScrollMode.ALWAYS,
            auto_scroll=True,
        )

        # 5. Pagination
        self.page_info_text = ft.Text(
            I18n.get("screener_page_info").format(current=1, total=1),
            color=AppColors.TEXT_PRIMARY,
        )
        self.prev_btn = ft.IconButton(
            ft.Icons.CHEVRON_LEFT,
            on_click=lambda e: self.vm.change_page(-1),
            icon_color=AppColors.PRIMARY,
        )
        self.next_btn = ft.IconButton(
            ft.Icons.CHEVRON_RIGHT,
            on_click=lambda e: self.vm.change_page(1),
            icon_color=AppColors.PRIMARY,
        )

        # Page size dropdown
        self.page_size_dropdown = ft.Dropdown(
            options=[
                ft.dropdown.Option(
                    "10",
                    text=f"10 {I18n.get('screener_per_page', '条/页')}",
                ),
                ft.dropdown.Option(
                    "20",
                    text=f"20 {I18n.get('screener_per_page', '条/页')}",
                ),
                ft.dropdown.Option(
                    "50",
                    text=f"50 {I18n.get('screener_per_page', '条/页')}",
                ),
                ft.dropdown.Option(
                    "100",
                    text=f"100 {I18n.get('screener_per_page', '条/页')}",
                ),
            ],
            value="50",
            width=120,
            dense=True,
            text_size=13,
            on_change=self._on_page_size_change,
        )

        # 6. Detail Dialog
        self.detail_dialog = None

        # 7. Mode Toggle (Realtime / History)
        self.mode_toggle = ft.SegmentedButton(
            segments=[
                ft.Segment(
                    value="REALTIME",
                    label=ft.Text(I18n.get("screener_mode_run", "执行选股")),
                    icon=ft.Icon(ft.Icons.ELECTRIC_BOLT),
                ),
                ft.Segment(
                    value="HISTORY",
                    label=ft.Text(I18n.get("screener_mode_history", "历史档案")),
                    icon=ft.Icon(ft.Icons.HISTORY),
                ),
            ],
            selected={"REALTIME"},
            on_change=self._on_mode_change,
        )

        # 8. History Tree (left sidebar, hidden by default)
        self.history_tree_list = ft.ListView(
            expand=True,
            spacing=0,
        )
        self.history_load_more_btn = ft.TextButton(
            text=I18n.get("history_load_more", "加载更早记录"),
            icon=ft.Icons.EXPAND_MORE,
            on_click=self._on_load_more_history,
            visible=False,  # shown after first load
        )
        self.history_tree_container = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Text(
                            I18n.get("screener_mode_history", "历史档案"),
                            weight=ft.FontWeight.BOLD,
                            color=AppColors.TEXT_PRIMARY,
                            size=14,
                        ),
                        padding=ft.padding.only(left=12, top=10, bottom=5),
                    ),
                    ft.Divider(height=1, color=AppColors.DIVIDER),
                    self.history_tree_list,
                    self.history_load_more_btn,
                ],
                spacing=0,
                expand=True,
            ),
            width=0,
            visible=False,
            bgcolor=ft.Colors.SURFACE,
            border=ft.border.only(right=ft.border.BorderSide(1, AppColors.DIVIDER)),
        )
        self._history_tree_offset = 0  # For pagination

        # Layout
        self._setup_layout()

    def did_mount(self):
        if getattr(self, "_mounted", False):
            return
        self._mounted = True
        if self.page:
            self.page.overlay.append(self.save_file_picker)
            self.page.update()

        # Initialize ViewModel and Bindings
        self.vm.bind(
            on_update=self._update_ui,
            on_log=self._append_log,
            on_status=self._update_status,
            on_progress=self._toggle_progress,
            on_log_stream_start=self._on_log_stream_start,
        )

        # Subscribe to TaskManager to unlock UI on background task completion
        TaskManager().subscribe(self._on_tasks_updated)

        # Load Strategies Async
        self.page.run_task(self._load_strategies)  # type: ignore[untyped]

    def will_unmount(self):
        TaskManager().unsubscribe(self._on_tasks_updated)
        self.vm.dispose()

        if self.page and getattr(self, "save_file_picker", None) in self.page.overlay:
            self.page.overlay.remove(self.save_file_picker)
            self.page.update()

        # Detach Flet Row references inside PaginatedTable to prevent memory leak
        if hasattr(self, "result_table") and self.result_table:
            self.result_table.list_view.controls.clear()

        # Cleanup overlay to prevent memory leak
        if self.detail_dialog and self.page:
            try:
                self.page.overlay.remove(self.detail_dialog)
            except ValueError:
                pass  # Already removed
            self.detail_dialog = None

        # U-1 fix: Reset mounted state for proper re-mount handling
        self._mounted = False

    def _on_tasks_updated(self, tasks):
        """Monitor TaskManager for the currently running AI Strategy execution"""
        if not self.page:
            return

        # Determine if we have any running strategy task
        running_strategy_tasks = [
            t for t in tasks if TASK_NAME_PREFIX in t.name and t.status.name in ("RUNNING", "QUEUED")
        ]

        # If there are no active strategy tasks, unlock the UI
        # Only unlock if we actually disabled it previously
        if not running_strategy_tasks and getattr(self, "selected_strategy", None):

            async def _unlock():
                if self.run_btn.disabled:
                    self.run_btn.disabled = False
                    self.run_btn.update()

                if self.progress_ring.visible:
                    self.progress_ring.visible = False
                    self.progress_ring.update()

            self.page.run_task(_unlock)

    async def _load_strategies(self):
        try:
            strategies = await self.vm.get_strategies()
            if not self.page:
                return

            # Use Name (value) for display, Key for ID
            # strategies is Dict[key, name]
            self.strategy_dropdown.options = [ft.dropdown.Option(k, v) for k, v in strategies.items()]
            self.strategy_dropdown.update()
        except Exception as e:
            logger.error(
                f"[ScreenerView] Strategy | ❌ Failed to load strategies: {e}",
                exc_info=True,
            )
            self.status_text.value = I18n.get("screener_load_failed").format(error=e)
            self.status_text.color = AppColors.ERROR
            self.status_text.update()
            return

        # Handle Pending Deep Link
        if self._pending_strategy_key:
            logger.debug(
                f"[ScreenerView] Executing pending strategy: {self._pending_strategy_key}",
            )
            await self.select_and_run_strategy(self._pending_strategy_key)
            self._pending_strategy_key = None

    async def select_and_run_strategy(self, strategy_key: str):
        """Public API to select and run a strategy (Deep Link)"""
        if not self.strategy_dropdown.options:
            logger.debug(
                f"[ScreenerView] Strategies not loaded yet. Queuing {strategy_key}",
            )
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

        # Render strategy params (so defaults are available for deep link)
        self._render_strategy_params()

        # Execute with default params
        self.log_view.controls.clear()
        self.log_view.update()
        params = self._collect_params()
        await self.vm.run_strategy(strategy_key, params=params)

    def _setup_layout(self):
        # ==========================================
        # 1. Top Control Deck (Card Layout)
        # ==========================================
        # Left side: Title + Mode Toggle + Dropdown + Desc
        title_row = ft.Row(
            [
                ft.Icon(ft.Icons.ELECTRIC_BOLT, color=AppColors.PRIMARY, size=24),
                ft.Text(
                    I18n.get("screener_title"),
                    size=20,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(width=20),  # Spacer
                self.mode_toggle,
            ],
            alignment=ft.MainAxisAlignment.START,
            spacing=10,
        )

        self.realtime_controls = ft.Column(
            [
                ft.Row([self.strategy_dropdown], spacing=10),
                self.strategy_desc_text,
                self.params_container,
            ],
            spacing=10,
            visible=True,
        )

        left_controls = ft.Column(
            [
                title_row,
                self.realtime_controls,
            ],
            spacing=10,
            expand=True,
        )

        # Right side: Status + Buttons
        status_row = ft.Row(
            [self.progress_ring, self.status_text],
            alignment=ft.MainAxisAlignment.END,
            spacing=10,
        )

        right_controls = ft.Column(
            [
                status_row,
                ft.Row(
                    [self.export_btn, self.run_btn],
                    spacing=15,
                    alignment=ft.MainAxisAlignment.END,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            horizontal_alignment=ft.CrossAxisAlignment.END,
        )

        # Combine into a floating Card
        control_card = ft.Container(
            content=ft.Row(
                [left_controls, right_controls],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            **AppStyles.dashboard_card(padding=20),
        )

        # ==========================================
        # 2. Middle Data Grid
        # ==========================================
        pagination_row = ft.Row(
            [
                self.prev_btn,
                self.page_info_text,
                self.next_btn,
                ft.Container(width=20),  # Spacer
                self.page_size_dropdown,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        )

        table_card = ft.Container(
            content=ft.Column(
                [
                    self.result_table,
                    ft.Divider(height=1, color=AppColors.DIVIDER),
                    pagination_row,
                ],
                spacing=0,
                expand=True,
            ),
            **AppStyles.dashboard_card(padding=0),
            expand=True,  # Fill all available vertical space (sole child in Row)
        )

        # ==========================================
        # 3. Bottom AI Analysis View (Streamed Cards)
        # ==========================================
        self.log_title_text = ft.Text(
            I18n.get("ai_analysis_report", "深度分析简报"),
            font_family="Roboto",
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )

        # AI Stream Container Styling
        self.log_view_container = ft.Container(
            content=self.log_view,
            border_radius=8,
            padding=5,
            expand=True,
        )

        self.log_card = ft.Container(
            content=ft.Column(
                [self.log_title_text, self.log_view_container],
                spacing=5,
            ),
            expand=True,
            padding=ft.padding.only(top=10),
        )

        # ==========================================
        # 4. Right content column (table + log)
        # ==========================================
        right_content = ft.Column([table_card, self.log_card], expand=True, spacing=10)

        # ==========================================
        # 5. Main Layout: Row(left_tree + right_content)
        # ==========================================
        main_body = ft.Row(
            [
                self.history_tree_container,
                right_content,
            ],
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        # ==========================================
        # Final Assembly
        # ==========================================
        self.content = ft.Column(
            [
                control_card,
                main_body,
            ],
            expand=True,
            spacing=15,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    # --- Mode Switching & History ---

    def _on_mode_change(self, e):
        """Handle SegmentedButton mode toggle.
        NOTE: This event handler runs in Flet's worker thread.
        All UI mutations must be routed through page.run_task() to avoid
        cross-thread races with _do_update() on the async event loop.
        """
        UILogger.log_action(
            "ScreenerView",
            "Toggle",
            f"mode={list(e.control.selected)[0] if e.control.selected else 'unknown'}",
        )
        selected = e.control.selected
        if not selected:
            return
        mode = list(selected)[0]
        if mode == "HISTORY":
            self.page.run_task(self._switch_to_history_mode)  # type: ignore[untyped]
        else:
            self.page.run_task(self._switch_to_realtime_mode)  # type: ignore[untyped]

    async def _switch_to_history_mode(self):
        """Activate history viewing mode."""
        self.vm.switch_to_history()
        # Show tree, hide realtime controls and log card
        self.history_tree_container.visible = True
        self.history_tree_container.width = 250
        self.realtime_controls.visible = False
        self.log_card.visible = False
        self.run_btn.visible = False
        # Clear table
        self.result_table.set_columns([])
        self.result_table.set_rows([], sort_col=None, sort_asc=True)
        # Load tree
        self._history_tree_offset = 0
        if self.page:
            self.page.update()
        await self._load_history_tree(append=False)

    async def _switch_to_realtime_mode(self):
        """Activate realtime execution mode."""
        self.vm.switch_to_realtime()
        # Hide tree, show realtime controls and log card
        self.history_tree_container.visible = False
        self.history_tree_container.width = 0
        self.realtime_controls.visible = True
        self.log_card.visible = True
        self.run_btn.visible = True
        self._render_table()
        if self.page:
            self.page.update()

    async def _load_history_tree(self, append=False):
        """Fetch and render the history tree from DB."""
        try:
            tree_data = await self.vm.load_history_tree(
                offset=self._history_tree_offset,
            )
            if not self.page:
                return

            if not append:
                self.history_tree_list.controls.clear()

            if not tree_data:
                if not append:
                    self.history_tree_list.controls.append(
                        ft.Container(
                            content=ft.Text(
                                I18n.get("screener_no_results", "暂无历史数据"),
                                color=AppColors.TEXT_SECONDARY,
                                size=13,
                            ),
                            padding=20,
                        ),
                    )
                self.history_load_more_btn.visible = False
            else:
                for date_str, strategies in tree_data.items():
                    total_cnt = sum(s["cnt"] for s in strategies)
                    # Format date for display
                    if isinstance(date_str, (datetime.date, datetime.datetime)):
                        display_date = date_str.strftime("%Y-%m-%d")
                        d_key = date_str.strftime("%Y-%m-%d")  # Use ISO format for internal key tracking
                    else:
                        date_str_s = str(date_str)
                        display_date = (
                            f"{date_str_s[:4]}-{date_str_s[4:6]}-{date_str_s[6:]}"
                            if len(date_str_s) == 8 and date_str_s.isdigit()
                            else date_str_s
                        )
                        d_key = date_str_s

                    # Build subtiles (strategy items)
                    subtiles = []
                    # "All strategies" option
                    subtiles.append(
                        ft.ListTile(
                            leading=ft.Icon(
                                ft.Icons.SELECT_ALL,
                                size=18,
                                color=AppColors.ACCENT,
                            ),
                            title=ft.Text(
                                f"{I18n.get('screener_all_strategies', '全部策略')} ({total_cnt})",
                                size=13,
                            ),
                            on_click=lambda e, d=d_key: self._on_tree_item_click(
                                d,
                                run_id=None,
                            ),
                            dense=True,
                        ),
                    )
                    for s in strategies:
                        strategy_display = translate_strategy_name(s["strategy_name"])
                        run_suffix = f" [{s['run_id'][:8]}]" if len(strategies) > 1 else ""
                        subtiles.append(
                            ft.ListTile(
                                leading=ft.Icon(
                                    ft.Icons.TRENDING_UP,
                                    size=16,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                                title=ft.Text(
                                    f"{strategy_display}{run_suffix} ({s['cnt']})",
                                    size=13,
                                ),
                                on_click=lambda e, d=d_key, rid=s["run_id"]: self._on_tree_item_click(d, run_id=rid),
                                dense=True,
                            ),
                        )

                    tile = ft.ExpansionTile(
                        title=ft.Text(
                            f"📅 {display_date}",
                            size=14,
                            weight=ft.FontWeight.W_500,
                        ),
                        subtitle=ft.Text(
                            I18n.get("history_total", "共 {count} 条").format(
                                count=total_cnt,
                            ),
                            size=11,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        controls=subtiles,
                        initially_expanded=(
                            self._history_tree_offset == 0 and self.history_tree_list.controls.__len__() == 0
                        ),
                        collapsed_icon_color=AppColors.TEXT_SECONDARY,
                    )
                    self.history_tree_list.controls.append(tile)

                self.history_load_more_btn.visible = len(tree_data) >= 5  # Show if likely more data
                self._history_tree_offset += len(tree_data) * 5  # Advance offset

            self.history_tree_list.update()
            self.history_load_more_btn.update()

        except Exception as ex:
            logger.error(
                f"[ScreenerView] History | ❌ Failed to load history tree: {ex}",
                exc_info=True,
            )

    def _on_tree_item_click(self, trade_date: str, strategy_name=None, run_id=None):
        """Handle click on a tree node to load historical records."""
        if not self.page:
            return
        self.page.run_task(self._load_history_for_date, trade_date, strategy_name, run_id)

    async def _load_history_for_date(self, trade_date, strategy_name=None, run_id=None):
        """Load historical data for a specific run_id or date/strategy and refresh table."""
        self._toggle_progress(True)
        if isinstance(trade_date, (datetime.date, datetime.datetime)):
            display = trade_date.strftime("%Y-%m-%d")
            trade_date = display
        else:
            ts = str(trade_date)
            display = f"{ts[:4]}-{ts[4:6]}-{ts[6:]}" if len(ts) == 8 and ts.isdigit() else ts
        if run_id:
            label = f"#{run_id[:8]}"
        else:
            label = (
                translate_strategy_name(strategy_name)
                if strategy_name
                else I18n.get("screener_all_strategies", "全部策略")
            )
        self._update_status(f"{display} / {label}", "blue")
        await self.vm.load_history_data(trade_date, strategy_name, run_id)
        self._toggle_progress(False)

    def _on_load_more_history(self, e):
        """Load more history tree entries."""
        if self.page:
            self.page.run_task(self._load_history_tree, append=True)

    # --- Event Handlers ---

    def _on_strategy_change(self, e):
        UILogger.log_action(
            "ScreenerView",
            "Select",
            f"strategy={self.strategy_dropdown.value}",
        )
        self.selected_strategy = self.strategy_dropdown.value
        self.run_btn.disabled = not self.selected_strategy

        # Update description text
        if self.selected_strategy:
            # Gather default params before rendering to push initial dynamic desc
            strategy_obj = self.vm.strategy_mgr.get_strategy(self.selected_strategy)
            if strategy_obj:
                defaults = {p["name"]: p.get("default") for p in strategy_obj.get_parameters()}
                desc = strategy_obj.get_dynamic_description(defaults)
            else:
                desc = self.vm.get_strategy_desc(self.selected_strategy)
            self.strategy_desc_text.value = desc
        else:
            self.strategy_desc_text.value = ""

        self.strategy_desc_text.update()
        self.run_btn.update()

        # Render dynamic parameters for this strategy
        self._render_strategy_params()

    async def _on_run_click(self, e):
        UILogger.log_action(
            "ScreenerView",
            "Click",
            f"btn_run | strategy={self.selected_strategy}",
        )
        if not self.selected_strategy:
            return
        self.run_btn.disabled = True
        self.run_btn.update()
        self.log_view.controls.clear()
        self.log_view.update()  # Refresh immediately to clear stale cards
        # Collect dynamic params from UI controls
        params = self._collect_params()
        self.page.run_task(self.vm.run_strategy, self.selected_strategy, params=params)  # type: ignore[untyped]

    def _render_strategy_params(self):
        """Dynamically render UI controls based on the selected strategy's parameter definitions."""
        from ui.theme import PARAM_GROUP_ORDER

        self.params_container.controls.clear()

        if not self.selected_strategy:
            self.params_container.update()
            return

        params_def = self.vm.get_strategy_params(self.selected_strategy)
        if not params_def:
            self.params_container.update()
            return

        groups = {g: [] for g in PARAM_GROUP_ORDER}
        custom_groups = {}
        group_labels = {}

        for p in params_def:
            group = p.get("group", "default")
            if group not in groups:
                custom_groups[group] = p.get("group_label_key")
                groups[group] = []
            groups[group].append(p)
            if group not in group_labels:
                group_labels[group] = p.get("group_label_key")

        rendered_groups = []

        for group_name in PARAM_GROUP_ORDER:
            if group_name == "default":
                continue
            if groups[group_name]:
                group_controls = self._build_param_controls(groups[group_name])
                if group_controls:
                    title = self._resolve_group_title(
                        group_name,
                        group_labels.get(group_name),  # type: ignore[untyped]
                    )
                    rendered_groups.append((group_name, title, group_controls))

        if groups["default"]:
            default_controls = self._build_param_controls(groups["default"])
            if default_controls:
                title = self._resolve_group_title(
                    "default",
                    group_labels.get("default"),  # type: ignore[untyped]
                )
                rendered_groups.append(("default", title, default_controls))

        for group_name in custom_groups:
            if groups[group_name]:
                group_controls = self._build_param_controls(groups[group_name])
                if group_controls:
                    title = self._resolve_group_title(group_name, custom_groups[group_name])
                    rendered_groups.append((group_name, title, group_controls))

        for group_name, title, controls in rendered_groups:
            if group_name == "advanced":
                continue
            group_card = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(
                            title,
                            size=13,
                            weight=ft.FontWeight.W_500,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                        ft.Divider(height=1, color=AppColors.DIVIDER),
                        ft.Column(controls, spacing=8),
                    ],
                    spacing=8,
                ),
                padding=ft.padding.all(12),
                bgcolor=AppColors.SURFACE_VARIANT,
                border_radius=8,
                margin=ft.margin.only(bottom=8),
            )
            self.params_container.controls.append(group_card)

        if groups["advanced"]:
            advanced_controls = self._build_param_controls(groups["advanced"])
            if advanced_controls:
                exp_tile = ft.ExpansionTile(
                    title=ft.Text(
                        I18n.get("ai_advanced_settings", "⚙️ 高级设置"),
                        size=14,
                        weight=ft.FontWeight.W_500,
                    ),
                    subtitle=ft.Text(
                        I18n.get(
                            "ai_advanced_settings_desc",
                            "仅供专业用户调整的底层策略参数或大模型系统提示词",
                        ),
                        size=12,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    controls=advanced_controls,
                    collapsed_text_color=AppColors.TEXT_PRIMARY,
                    text_color=AppColors.PRIMARY,
                    initially_expanded=False,
                )
                self.params_container.controls.append(exp_tile)

        self.params_container.update()

    def _resolve_group_title(self, group_name: str, label_key: str = None) -> str:  # type: ignore[untyped]
        """Resolve group title with priority: label_key > DEFAULT_GROUP_LABELS > group_name."""
        from ui.theme import DEFAULT_GROUP_LABELS

        if label_key:
            return I18n.get(label_key)
        if group_name in DEFAULT_GROUP_LABELS:
            return DEFAULT_GROUP_LABELS[group_name]
        return group_name

    def _build_param_controls(self, params: list) -> list:
        """Build UI controls for a list of parameter definitions."""
        controls = []

        for p in params:
            label = I18n.get(p.get("label_key", p["name"]))
            p_type = p.get("type", "number")

            if p_type == "slider":
                min_val = p.get("min", 0)
                max_val = p.get("max", 100)
                default = p.get("default", min_val)
                step = p.get("step", 1)
                divisions = int((max_val - min_val) / step) if step > 0 else 10

                init_display = int(default) if default == int(default) else round(default, 1)
                value_text = ft.Text(
                    f"{label}: {init_display}",
                    size=12,
                    color=AppColors.TEXT_SECONDARY,
                )

                def make_on_change(vt, lbl):
                    def handler(e):
                        val = e.control.value
                        display = int(val) if val == int(val) else round(val, 1)
                        vt.value = f"{lbl}: {display}"
                        e.control.tooltip = str(display)
                        vt.update()
                        e.control.update()

                        if self.selected_strategy:
                            strategy_obj = self.vm.strategy_mgr.get_strategy(
                                self.selected_strategy,
                            )
                            if strategy_obj and hasattr(
                                strategy_obj,
                                "get_dynamic_description",
                            ):
                                params = self._collect_params()
                                self.strategy_desc_text.value = strategy_obj.get_dynamic_description(params)
                                self.strategy_desc_text.update()

                    return handler

                slider = ft.Slider(
                    min=min_val,
                    max=max_val,
                    value=default,
                    divisions=divisions,
                    label="{value}",
                    active_color=AppColors.PRIMARY,
                    tooltip=str(init_display),
                    on_change=make_on_change(value_text, label),
                )
                slider.data = p["name"]

                controls.append(value_text)
                controls.append(slider)

            elif p_type == "number":
                ctrl = ft.TextField(
                    label=label,
                    value=str(p.get("default", "")),
                    keyboard_type=ft.KeyboardType.NUMBER,
                    dense=True,
                    border_color=AppColors.DIVIDER,
                    focused_border_color=AppColors.PRIMARY,
                    text_size=13,
                    content_padding=ft.padding.symmetric(horizontal=10, vertical=8),
                )
                ctrl.data = p["name"]
                controls.append(ctrl)

            elif p_type == "dropdown":
                options = p.get("options", [])
                ctrl = ft.Dropdown(
                    label=label,
                    value=str(p.get("default", "")),
                    options=[ft.dropdown.Option(str(o)) for o in options],
                    dense=True,
                    border_color=AppColors.DIVIDER,
                    focused_border_color=AppColors.PRIMARY,
                    text_size=13,
                    content_padding=ft.padding.symmetric(horizontal=10, vertical=8),
                )
                ctrl.data = p["name"]
                controls.append(ctrl)

            elif p_type == "textarea":
                if p["name"] == "ai_system_prompt" and self.selected_strategy:
                    from strategies.strategy_prompts import get_base_prompt

                    current_val = get_base_prompt(self.selected_strategy) or p.get(
                        "default",
                        "",
                    )
                else:
                    current_val = p.get("default", "")

                ctrl = ft.TextField(
                    label=label,
                    value=str(current_val),
                    multiline=True,
                    min_lines=6,
                    max_lines=15,
                    border_color=AppColors.DIVIDER,
                    focused_border_color=AppColors.PRIMARY,
                    text_size=12,
                    content_padding=ft.padding.symmetric(horizontal=10, vertical=10),
                )

                reset_btn = None
                if p["name"] == "ai_system_prompt":
                    ctrl.label = None

                    def make_restore_default(strat, ctrl_field):
                        def restore_default(e):
                            from strategies.strategy_prompts import get_base_prompt
                            from utils.config_handler import ConfigHandler

                            ConfigHandler.set_strategy_prompt(strat, None)
                            ctrl_field.value = str(get_base_prompt(strat))
                            ctrl_field.update()
                            if self.page and hasattr(self.page, "show_toast"):
                                self.page.show_toast(  # type: ignore[untyped]
                                    I18n.get(
                                        "ai_settings_restored",
                                        "系统提示词已恢复默认",
                                    ),
                                    "info",
                                )

                        return restore_default

                    def make_save_prompt(strat, ctrl_field):
                        def save_prompt(e):
                            from utils.config_handler import ConfigHandler
                            from utils.prompt_guard import validate_prompt, MAX_PROMPT_LENGTH

                            prompt_val = ctrl_field.value or ""
                            is_valid, warning = validate_prompt(prompt_val)
                            if not is_valid:
                                if self.page and hasattr(self.page, "show_toast"):
                                    msg = I18n.get(warning, warning)
                                    if warning == "prompt_err_length":
                                        msg = I18n.get("prompt_err_length").format(max=MAX_PROMPT_LENGTH)
                                    self.page.show_toast(
                                        f"⚠ {msg}",
                                        "warning",
                                    )
                                return

                            ConfigHandler.set_strategy_prompt(strat, prompt_val)
                            UILogger.log_action(
                                "ScreenerView",
                                "SavePrompt",
                                f"strategy={strat}",
                            )
                            if self.page and hasattr(self.page, "show_toast"):
                                self.page.show_toast(  # type: ignore[untyped]
                                    I18n.get("ai_settings_saved", "系统提示词已保存"),
                                    "success",
                                )

                        return save_prompt

                    reset_btn = ft.TextButton(
                        text=I18n.get("ai_reset_default", "恢复默认"),
                        icon=ft.Icons.RESTORE,
                        style=ft.ButtonStyle(color=AppColors.TEXT_SECONDARY),
                        height=30,
                        on_click=make_restore_default(self.selected_strategy, ctrl),
                    )

                    save_btn = ft.TextButton(
                        text=I18n.get("ai_save_prompt", "保存修改"),
                        icon=ft.Icons.SAVE,
                        style=ft.ButtonStyle(color=AppColors.PRIMARY),
                        height=30,
                        on_click=make_save_prompt(self.selected_strategy, ctrl),
                    )

                ctrl.data = p["name"]
                if p["name"] == "ai_system_prompt" and reset_btn:
                    wrapper = ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            label,
                                            size=12,
                                            color=AppColors.TEXT_SECONDARY,
                                        ),
                                        ft.Container(expand=True),
                                        save_btn,
                                        reset_btn,
                                    ],
                                ),
                                ctrl,
                            ],
                            spacing=5,
                        ),
                        margin=ft.margin.only(top=10, bottom=5),
                    )
                    controls.append(wrapper)
                else:
                    wrapper = ft.Container(
                        content=ctrl,
                        margin=ft.margin.only(top=10, bottom=5),
                    )
                    controls.append(wrapper)

        return controls

    def _collect_params(self) -> dict:
        """Collect current parameter values from dynamic UI controls."""
        params = {}

        def extract(controls_list):
            for ctrl in controls_list:
                if isinstance(ctrl, ft.ExpansionTile):
                    # Recursive extraction for nested ExpansionTile
                    extract(ctrl.controls)
                    continue
                if isinstance(ctrl, ft.Container) and ctrl.content:
                    # Parse into Container wrappers (e.g. margin/padding wrappers)
                    extract([ctrl.content])
                    continue
                if isinstance(ctrl, (ft.Column, ft.Row)):
                    # Ensure we traverse layout groupings (like the custom Restore button layout)
                    extract(ctrl.controls)
                    continue

                if not hasattr(ctrl, "data") or ctrl.data is None:
                    continue  # Skip labels/decorators

                name = ctrl.data
                if isinstance(ctrl, ft.Slider):
                    val = ctrl.value
                    params[name] = int(val) if val == int(val) else round(val, 2)
                elif isinstance(ctrl, ft.TextField):
                    if ctrl.multiline:
                        params[name] = ctrl.value
                    else:
                        try:
                            params[name] = float(ctrl.value)  # type: ignore[untyped]
                        except ValueError, TypeError:
                            params[name] = ctrl.value
                elif isinstance(ctrl, ft.Dropdown):
                    params[name] = ctrl.value

        extract(self.params_container.controls)
        return params

    def _toggle_progress(self, visible):
        if not self.page:
            return

        async def _do_toggle():
            self.progress_ring.visible = visible
            self.run_btn.disabled = visible
            self.strategy_dropdown.disabled = visible
            self.progress_ring.update()
            self.run_btn.update()
            self.strategy_dropdown.update()

        self.page.run_task(_do_toggle)

    def _on_virtual_sort(self, col_id, ascending):
        self.page.run_task(self.vm.sort_data, col_id, ascending)

    async def _on_export_click(self, e):
        """Export current results"""
        UILogger.log_action("ScreenerView", "Click", "btn_export")

        df = self.vm.get_export_data()
        if df is None:
            if hasattr(self.page, "show_toast"):
                self.page.show_toast(I18n.get("data_export_no_data"), "error")  # type: ignore[untyped]
            return

        timestamp = get_now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"screener_results_{timestamp}.csv"

        self.save_file_picker.save_file(
            dialog_title=I18n.get("data_export_save_title"),
            file_name=default_filename,
            allowed_extensions=["csv"],
        )

    def _on_save_file_result(self, e: ft.FilePickerResultEvent):
        if not self.page:
            return

        if not e.path:
            return

        self.export_btn.disabled = True
        self.export_btn.update()

        async def _do_export(filepath):
            try:
                path, error = await self.vm.export_results(filepath)
                if path:
                    filename = os.path.basename(filepath)
                    if hasattr(self.page, "show_toast"):
                        self.page.show_toast(  # type: ignore[untyped]
                            I18n.get("data_export_success", file=filename),
                            "success",
                        )
                elif hasattr(self.page, "show_toast"):
                    self.page.show_toast(  # type: ignore[untyped]
                        I18n.get("data_export_fail", error=error),
                        "error",
                    )
            except Exception as ex:
                logger.error(f"[ScreenerView] Export | ❌ Failed: {ex}", exc_info=True)
            finally:
                self.export_btn.disabled = False
                self.export_btn.update()

        self.page.run_task(_do_export, e.path)  # type: ignore[untyped]

    def _on_page_size_change(self, e):
        try:
            new_size = int(self.page_size_dropdown.value)  # type: ignore[untyped]
            self.vm.change_page_size(new_size)
        except ValueError:
            pass

    def _on_row_click(self, row_data):
        """Handler passed down to PaginatedTable for row clicks.
        row_data here is the FORMATTED dict (for display). We look up the
        RAW dict (with numeric values) for StockDetailDialog."""
        if not self.page:
            return

        # Look up raw row data by ts_code for the detail dialog
        ts_code = row_data.get("ts_code", "")
        raw_data = getattr(self, "_raw_row_lookup", {}).get(ts_code, row_data)

        # Instantiate or update dialog
        if not self.detail_dialog:
            self.detail_dialog = StockDetailDialog(
                stock_data=raw_data,
                data_processor=self.vm.data_processor,
            )
            self.page.overlay.append(self.detail_dialog)
        else:
            self.detail_dialog.update_data(raw_data)

        self.detail_dialog.open = True
        self.page.update()

        # Trigger async chart load
        if ts_code:
            self.page.run_task(self.detail_dialog.load_chart, ts_code)

    # --- UI Update Callbacks ---

    def _update_ui(self):
        if not self.page:
            return

        async def _do_update():
            # 1. Update Table
            self._render_table()

            # 2. Update Pagination
            self.page_info_text.value = I18n.get("screener_page_info").format(
                current=self.vm.page_no,
                total=getattr(self.vm, "total_pages", 0),
            )
            self.prev_btn.disabled = self.vm.page_no <= 1
            self.next_btn.disabled = self.vm.page_no >= getattr(
                self.vm,
                "total_pages",
                0,
            )

            # 3. Enable Export if data exists
            self.export_btn.disabled = getattr(self.vm, "total_items", 0) == 0

            self.page.update()  # type: ignore[untyped]

        self.page.run_task(_do_update)

    def _render_table(self):
        """Re-render table based on VM current page data"""
        df = self.vm.get_current_page_data()

        if df is None or df.empty:
            self.result_table.set_columns([])
            self.result_table.set_rows(
                [],
                sort_col=self.vm.sort_column,
                sort_asc=self.vm.sort_ascending,
            )
            self._raw_row_lookup = {}
            return

        self._raw_row_lookup = {str(r.get("ts_code", "")): r for r in df.to_dict("records")}

        vt_columns, formatted_rows = _build_table_data(df)

        self.result_table.on_row_click = self._on_row_click
        self.result_table.set_columns(vt_columns)
        self.result_table.set_rows(
            formatted_rows,
            sort_col=self.vm.sort_column,
            sort_asc=self.vm.sort_ascending,
        )

    def _append_log(self, name, score, thinking):
        if not self.page:
            return

        async def _do_log():
            line = f"[{name}] {I18n.get('screener_score')}: {score} | {thinking[:80]}..."

            # Colors based on score
            color = AppColors.ACCENT if score > 80 else "#FFB86C" if score > 50 else "#FF5555"

            # Limit total logs to avoid memory leak (aligned with stream card cap)
            if len(self.log_view.controls) > 10:
                self.log_view.controls.pop(0)

            self.log_view.controls.append(
                ft.Text(
                    line,
                    color=color,
                    size=12,
                    no_wrap=False,
                    font_family="Roboto Mono, Consolas, monospace",
                ),
            )
            self.log_view.update()

        self.page.run_task(_do_log)

    def _on_log_stream_start(self, name):
        """Creates a streaming Markdown card and returns a throttled chunk receiver closure."""
        if not self.page:
            return None

        # 1. Component initialization
        reasoning_md = ft.Markdown(
            "",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            code_theme="atom-one-dark",  # type: ignore[untyped]
        )

        content_md = ft.Markdown(
            "",
            selectable=True,
            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            code_theme="atom-one-dark",  # type: ignore[untyped]
        )

        reasoning_tile = ft.ExpansionTile(
            title=ft.Text(f"💡 {I18n.get('ai_thinking', '推演思考过程')}..."),
            subtitle=ft.Text(
                I18n.get("ai_expand_reasoning", "点击展开/折叠思考过程"),
                size=10,
                color=AppColors.TEXT_SECONDARY,
            ),
            controls=[
                ft.Container(
                    content=reasoning_md,
                    padding=10,
                    bgcolor=AppColors.BACKGROUND,
                    border_radius=4,
                ),
            ],
            initially_expanded=True,
            visible=False,  # Hidden until reasoning text arrives
        )

        # Main stock Card
        card_content = ft.Column(
            [
                ft.Text(f"📈 {name}", weight=ft.FontWeight.W_600, size=16),
                reasoning_tile,
                ft.Container(
                    content=content_md,
                    padding=ft.padding.only(left=5, right=5),
                ),
            ],
            spacing=10,
        )

        card = ft.Container(
            content=card_content,
            border=ft.border.all(1, AppColors.DIVIDER),
            border_radius=8,
            padding=15,
            bgcolor=AppColors.SURFACE,
            margin=ft.margin.only(bottom=10),
        )

        async def _add_line_task():
            # optional: limit max cards to avoid memory explosion if analyzing 100 stocks
            if len(self.log_view.controls) > 10:
                self.log_view.controls.pop(0)
            self.log_view.controls.append(card)
            self.log_view.update()

        self.page.run_task(_add_line_task)

        state = {"reasoning": "", "content": "", "last_flush": 0.0, "pending": False}
        THROTTLE_INTERVAL = 0.15  # 150ms — smooth but not flooding

        def _flush_display():
            """Snapshot current text and schedule a UI update."""

            # Snapshots for closure safety
            snap_reas = state["reasoning"]
            snap_cont = state["content"]

            async def _update_line_task():
                if not card.page:
                    return

                # Update reasoning
                if snap_reas:
                    reasoning_md.value = snap_reas
                    reasoning_tile.visible = True

                # Update content
                if snap_cont:
                    content_md.value = snap_cont

                self.log_view.update()

            if self.page:
                self.page.run_task(_update_line_task)
            state["last_flush"] = time.time()
            state["pending"] = False

        def _on_chunk(chunk_text, is_reasoning=False):
            if not self.page:
                return
            if is_reasoning:
                state["reasoning"] += chunk_text
            else:
                state["content"] += chunk_text

            now = time.time()
            if now - state["last_flush"] >= THROTTLE_INTERVAL:
                _flush_display()
            else:
                state["pending"] = True

        # Attach final_flush so caller can drain last pending chunk
        _on_chunk.final_flush = lambda: _flush_display() if state["pending"] else None

        return _on_chunk

    def _update_status(self, msg, color=None):
        if not self.page:
            return

        async def _do_status():
            self.status_text.value = msg
            self.status_text.color = color or AppColors.TEXT_PRIMARY
            self.status_text.update()

        self.page.run_task(_do_status)

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

        # 2. Result Table (Update props always, PaginatedTable.update_theme checks self.page for UI)
        self.result_table.update_theme()

        # 3. Logs (Use modern card style)
        try:
            if hasattr(self, "log_title_text"):
                self.log_title_text.color = AppColors.TEXT_PRIMARY
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
                logger.error(
                    f"[ScreenerView] Theme | ❌ Re-render failed: {e}",
                    exc_info=True,
                )

            self.page.update()
