import logging
import math
from collections.abc import Sequence
from typing import Any

import flet as ft

from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)

ROW_HEIGHT = 30
HEADER_HEIGHT = 35
BUFFER_ROWS = 8
RERENDER_THRESHOLD = 4
DEFAULT_VIEWPORT_ROWS = 30
MIN_TABLE_WIDTH = 800
_TREND_COLS = frozenset({"pct_chg", "change", "chg"})
_CODE_COLS = frozenset({"ts_code", "symbol"})


class PaginatedTable(ft.Column):
    """Viewport-virtualized table with sorting support.

    `set_rows()` stores the full current page but renders only a window of row
    controls. A single Stack with virtual height preserves scroll extent; pooled
    row controls are absolutely positioned inside the Stack.
    """

    def __init__(self, on_sort=None):
        super().__init__(expand=True, spacing=0)
        self.on_sort = on_sort
        self.on_row_click = None

        self.columns_def: list[dict[str, Any]] = []
        self.sort_col: str | None = None
        self.sort_asc = True

        self._rows: list[dict[str, Any]] = []
        self._total_width = MIN_TABLE_WIDTH
        self._row_pool: list[ft.Container] = []
        self._win_start = 0
        self._win_end = 0
        self._last_rendered_first = -1
        self._viewport_h = 0.0

        self.header_row = ft.Row(spacing=0)
        self.header_container = ft.Container(
            content=self.header_row,
            bgcolor=AppColors.TABLE_HEADER_BG,
            height=HEADER_HEIGHT,
            border=ft.Border.only(bottom=ft.BorderSide(1, AppColors.TABLE_BORDER)),
        )

        self._canvas = ft.Stack(
            controls=[],
            height=0,
            width=MIN_TABLE_WIDTH,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        self.list_view = ft.ListView(
            controls=[self._canvas],
            expand=True,
            spacing=0,
            on_scroll=self._on_scroll,
            scroll_interval=100,
        )

        self.inner_column = ft.Column(
            controls=[self.header_container, self.list_view],
            spacing=0,
        )
        self.horizontal_wrapper = ft.Row(
            controls=[self.inner_column],
            expand=True,
            scroll=ft.ScrollMode.ALWAYS,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self.controls = [self.horizontal_wrapper]

    @property
    def rendered_row_controls(self) -> Sequence[ft.Control]:
        """Rows currently attached to the virtual canvas."""
        return self._canvas.controls

    def set_columns(self, columns: Sequence[dict[str, Any]]):
        """Set column definitions. Caller is responsible for page.update()."""
        self.columns_def = list(columns)
        self._total_width = max(sum(int(col.get("width", 100)) for col in self.columns_def), MIN_TABLE_WIDTH)
        self._build_header()
        self._sync_canvas_size()

        # Column layout changed, so old pooled rows have the wrong cell tree.
        # Re-render current data window without changing the scroll contract.
        self._row_pool.clear()
        self._last_rendered_first = -1
        if self._rows:
            self._render_window(target_first=max(self._win_start, 0))
        else:
            self._canvas.controls = []

        if self.page:
            try:
                self.header_container.update()
                self._canvas.update()
            except Exception as e:
                logger.debug("UI render error: %s", e, exc_info=True)

    def set_rows(self, data_rows, sort_col=None, sort_asc=True):
        """Store full page rows and render the first visible window.

        This remains a pure data setter. Do not call page.update() here; the
        parent view already owns the update cycle.
        """
        self.sort_col = sort_col
        self.sort_asc = sort_asc
        self._build_header()

        self._rows = list(data_rows)
        self._last_rendered_first = -1
        self._win_start = 0
        self._win_end = 0
        self._sync_canvas_size()
        self._render_window(target_first=0)

        if self.page:
            # Best-effort only. `set_rows` must still be valid before mount.
            # 不调用 list_view.scroll_to：Flet 0.85.3 中该方法是协程，同步调用会触发
            # RuntimeWarning 且实际未执行；滚动重置交由用户交互触发。
            try:
                self._canvas.update()
            except Exception as e:
                logger.debug("UI render error: %s", e, exc_info=True)

    def clear(self):
        """Detach all row controls and reset window state.

        Used by the parent view's `will_unmount()` to break references to row
        Containers. IMPORTANT: never clear `list_view.controls` directly — the
        ListView must keep the single `_canvas` child, otherwise re-mount loses
        the scroll canvas and renders nothing.
        """
        self._rows = []
        self._win_start = 0
        self._win_end = 0
        self._last_rendered_first = -1
        self._canvas.controls = []
        self._row_pool.clear()
        self._sync_canvas_size()

    def update_theme(self):
        """Refresh theme-dependent styles."""
        self.header_container.bgcolor = AppColors.TABLE_HEADER_BG
        self.header_container.border = ft.Border.only(bottom=ft.BorderSide(1, AppColors.TABLE_BORDER))
        self._build_header()

        # Rebind current window because cell colors and row backgrounds are theme-dependent.
        first = max(self._win_start, 0)
        self._last_rendered_first = -1
        self._render_window(target_first=first)

        if self.page:
            self.header_container.update()
            self._canvas.update()

    def _build_header(self):
        row_controls = []
        total_width = 0
        for col in self.columns_def:
            col_id = str(col["id"])
            label = str(col.get("label", col_id))
            if self.sort_col == col_id:
                label += " ↑" if self.sort_asc else " ↓"

            text = ft.Text(
                label,
                weight=ft.FontWeight.BOLD,
                size=12,
                color=AppColors.TABLE_HEADER_TEXT,
                no_wrap=True,
            )
            content = ft.Container(
                content=text,
                alignment=ft.Alignment.CENTER_LEFT,
                padding=ft.Padding.only(left=8, right=8),
                on_click=lambda e, cid=col_id: self._handle_sort_click(cid),
            )
            width = int(col.get("width", 100))
            total_width += width
            row_controls.append(ft.Container(content, width=width))

        self.header_row.controls = row_controls
        self._total_width = max(total_width, MIN_TABLE_WIDTH)
        self.inner_column.width = self._total_width
        self.header_container.width = self._total_width
        self._canvas.width = self._total_width

    def _handle_sort_click(self, col_id: str):
        if self.sort_col == col_id:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col = col_id
            self.sort_asc = True

        self._build_header()
        if self.page:
            self.header_container.update()
        if self.on_sort:
            self.on_sort(col_id, self.sort_asc)

    def _build_cells(self, row_data: dict[str, Any]) -> list[ft.Container]:
        cells = []
        for col in self.columns_def:
            col_id = str(col["id"])
            val = str(row_data.get(col_id, ""))

            numeric_val: float | None = None
            is_numeric = False
            try:
                numeric_val = float(val.replace("%", "").replace(",", ""))
                is_numeric = True
            except ValueError:
                pass

            text_color = AppColors.TABLE_CELL_NUMERIC if is_numeric else AppColors.TABLE_CELL_TEXT
            alignment = ft.Alignment.CENTER_RIGHT if is_numeric else ft.Alignment.CENTER_LEFT

            is_trend = col_id in _TREND_COLS
            if is_trend and numeric_val is not None:
                if numeric_val > 0:
                    text_color = AppColors.UP_RED if hasattr(AppColors, "UP_RED") else "#F44336"
                elif numeric_val < 0:
                    text_color = AppColors.DOWN_GREEN if hasattr(AppColors, "DOWN_GREEN") else "#4CAF50"

            if col_id in _CODE_COLS and "." in val:
                parts = val.split(".", maxsplit=1)
                text = ft.Text(
                    spans=[
                        ft.TextSpan(parts[0], ft.TextStyle(weight=ft.FontWeight.BOLD, color=text_color)),
                        ft.TextSpan(
                            "." + parts[1],
                            ft.TextStyle(
                                size=10,
                                color=AppColors.TEXT_TERTIARY  # type: ignore[untyped]
                                if hasattr(AppColors, "TEXT_TERTIARY")
                                else "#888888",
                            ),
                        ),
                    ],
                    size=12,
                    no_wrap=True,
                )
            else:
                text = ft.Text(
                    val,
                    size=12,
                    no_wrap=True,
                    weight=ft.FontWeight.BOLD if is_trend else None,
                    color=text_color,
                    font_family="Roboto Mono, monospace" if is_numeric else None,
                )

            content = ft.Container(
                content=text,
                alignment=alignment,
                padding=ft.Padding.only(left=8, right=8),
            )
            width = col.get("width")
            cells.append(ft.Container(content, width=int(width)) if width else ft.Container(content, expand=1))
        return cells

    def _sync_canvas_size(self):
        self._canvas.height = len(self._rows) * ROW_HEIGHT
        self._canvas.width = self._total_width

    def _window_capacity(self) -> int:
        if self._viewport_h > 0:
            viewport_rows = math.ceil(self._viewport_h / ROW_HEIGHT)
        else:
            viewport_rows = DEFAULT_VIEWPORT_ROWS
        return max(1, viewport_rows + 2 * BUFFER_ROWS)

    def _render_window(self, target_first: int):
        row_count = len(self._rows)
        if row_count == 0:
            self._canvas.controls = []
            self._win_start = 0
            self._win_end = 0
            self._last_rendered_first = -1
            self._sync_canvas_size()
            return

        capacity = self._window_capacity()
        start = max(0, min(target_first - BUFFER_ROWS, max(0, row_count - capacity)))
        end = min(row_count, start + capacity)

        visible_rows: list[ft.Container] = []
        for pool_idx, abs_idx in enumerate(range(start, end)):
            row = self._get_pool_row(pool_idx)
            self._bind_row(row, abs_idx, self._rows[abs_idx])
            visible_rows.append(row)

        self._canvas.controls = visible_rows
        self._sync_canvas_size()
        self._win_start = start
        self._win_end = end
        self._last_rendered_first = target_first

    def _get_pool_row(self, pool_idx: int) -> ft.Container:
        if pool_idx < len(self._row_pool):
            return self._row_pool[pool_idx]

        row = ft.Container(
            left=0,
            top=0,
            height=ROW_HEIGHT,
            width=self._total_width,
            ink=True,
        )
        self._row_pool.append(row)
        return row

    def _bind_row(self, row: ft.Container, abs_idx: int, row_data: dict[str, Any]):
        row.left = 0
        row.top = abs_idx * ROW_HEIGHT
        row.height = ROW_HEIGHT
        row.width = self._total_width
        row.bgcolor = AppStyles.data_table_row(abs_idx)
        row.content = ft.Row(self._build_cells(row_data), spacing=0)
        row.on_click = lambda e, r=row_data: self._handle_row_click(r)

    def refresh_viewport(self, viewport_height=None):
        """Recalculate visible rows based on viewport height.

        If *viewport_height* is provided, it replaces the stored viewport
        height.  If omitted, the previously stored height (from the last
        scroll event) is reused.  When the resulting row count differs from
        the current window, the table is re-rendered.
        """
        if viewport_height is not None:
            self._viewport_h = float(viewport_height)

        if not self._viewport_h:
            return

        new_capacity = self._window_capacity()
        current_capacity = self._win_end - self._win_start
        if new_capacity != current_capacity:
            target = max(self._win_start, 0)
            self._last_rendered_first = -1
            self._render_window(target_first=target)
            if self.page:
                try:
                    self._canvas.update()
                except Exception as e:
                    logger.debug("UI render error: %s", e, exc_info=True)

    def _on_scroll(self, e):
        viewport_h = getattr(e, "viewport_dimension", None)
        if viewport_h:
            self._viewport_h = float(viewport_h)

        # KNOWN LIMITATION: _viewport_h only updates on scroll events.
        # If the user resizes the window taller without scrolling, the bottom
        # of the table may show blank rows until the next scroll triggers an
        # update. To fix this, the parent could call a public refresh_viewport()
        # method from a resize callback — but that method is not introduced in
        # this iteration to avoid over-engineering.
        offset = float(getattr(e, "pixels", None) or 0.0)
        new_first = max(0, int(offset // ROW_HEIGHT))
        if self._last_rendered_first < 0 or abs(new_first - self._last_rendered_first) >= RERENDER_THRESHOLD:
            self._render_window(target_first=new_first)
            if self.page:
                self._canvas.update()

    def _handle_row_click(self, row_data):
        if self.on_row_click:
            self.on_row_click(row_data)
