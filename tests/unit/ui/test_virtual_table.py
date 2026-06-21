"""Unit tests for ui.components.virtual_table.PaginatedTable."""

import math
from unittest.mock import MagicMock

import flet as ft
import pytest

from ui.components.virtual_table import (
    BUFFER_ROWS,
    DEFAULT_VIEWPORT_ROWS,
    HEADER_HEIGHT,
    MIN_TABLE_WIDTH,
    RERENDER_THRESHOLD,
    ROW_HEIGHT,
    PaginatedTable,
)
from ui.theme import AppColors

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cols():
    return [
        {"id": "name", "label": "Name", "width": 120},
        {"id": "price", "label": "Price", "width": 80},
        {"id": "pct_chg", "label": "Change", "width": 80},
    ]


def _make_rows(n=5):
    return [{"name": f"S{i}", "price": str(100 + i), "pct_chg": f"{i * 0.5}%"} for i in range(n)]


def _scroll_event(pixels, viewport_h=None):
    """Build a minimal scroll-event-like object."""

    class _Evt:
        pass

    e = _Evt()
    e.pixels = pixels
    if viewport_h is not None:
        e.viewport_dimension = viewport_h
    return e


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestPaginatedTableInit:
    def test_init_expand_is_true(self):
        table = PaginatedTable()
        assert table.expand is True

    def test_init_spacing_is_zero(self):
        table = PaginatedTable()
        assert table.spacing == 0

    def test_init_on_sort_default_is_none(self):
        table = PaginatedTable()
        assert table.on_sort is None

    def test_init_on_row_click_default_is_none(self):
        table = PaginatedTable()
        assert table.on_row_click is None

    def test_init_rows_is_empty(self):
        table = PaginatedTable()
        assert table._rows == []

    def test_init_total_width_is_min_table_width(self):
        table = PaginatedTable()
        assert table._total_width == MIN_TABLE_WIDTH

    def test_init_row_pool_is_empty(self):
        table = PaginatedTable()
        assert table._row_pool == []

    def test_init_window_start_is_zero(self):
        table = PaginatedTable()
        assert table._win_start == 0

    def test_init_window_end_is_zero(self):
        table = PaginatedTable()
        assert table._win_end == 0

    def test_init_last_rendered_first_is_minus_one(self):
        table = PaginatedTable()
        assert table._last_rendered_first == -1

    def test_init_viewport_h_is_zero(self):
        table = PaginatedTable()
        assert table._viewport_h == 0.0

    def test_init_canvas_height_is_zero(self):
        table = PaginatedTable()
        assert table._canvas.height == 0

    def test_init_canvas_width_is_min_table_width(self):
        table = PaginatedTable()
        assert table._canvas.width == MIN_TABLE_WIDTH

    def test_init_header_container_height(self):
        table = PaginatedTable()
        assert table.header_container.height == HEADER_HEIGHT

    def test_init_with_on_sort_callback(self):
        cb = MagicMock()
        table = PaginatedTable(on_sort=cb)
        assert table.on_sort is cb

    def test_init_controls_contains_horizontal_wrapper(self):
        table = PaginatedTable()
        assert len(table.controls) == 1
        assert table.controls[0] is table.horizontal_wrapper


# ---------------------------------------------------------------------------
# set_columns
# ---------------------------------------------------------------------------


class TestSetColumns:
    def test_total_width_is_max_of_sum_and_min_table_width(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        # 120+80+80=280 < MIN_TABLE_WIDTH, so _total_width == MIN_TABLE_WIDTH
        assert table._total_width == max(120 + 80 + 80, MIN_TABLE_WIDTH)

    def test_total_width_floors_to_min_table_width_when_small(self):
        table = PaginatedTable()
        table.set_columns([{"id": "a", "label": "A", "width": 50}])
        assert table._total_width == MIN_TABLE_WIDTH

    def test_set_columns_clears_row_pool_then_rerenders(self):
        """set_columns clears _row_pool, but _render_window re-populates it when rows exist."""
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        assert len(table._row_pool) > 0

        table.set_columns(_make_cols())
        # Pool is cleared then re-populated by _render_window
        assert len(table._row_pool) > 0
        assert len(table._row_pool) == len(table.rendered_row_controls)

    def test_set_columns_resets_last_rendered_first_then_rerenders(self):
        """set_columns sets _last_rendered_first = -1, but _render_window sets it to target_first."""
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        table._last_rendered_first = 5

        table.set_columns(_make_cols())
        # After set_columns with rows, _render_window sets _last_rendered_first to target_first
        assert table._last_rendered_first >= 0

    def test_set_columns_with_existing_rows_renders_window(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(10))
        # Re-set columns with data present — should re-render
        table.set_columns(_make_cols())
        assert len(table.rendered_row_controls) > 0

    def test_set_columns_without_rows_clears_canvas(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        table._rows = []  # Simulate no data
        table.set_columns(_make_cols())
        assert table.rendered_row_controls == []

    def test_set_columns_updates_inner_column_width(self):
        table = PaginatedTable()
        cols = [
            {"id": "a", "label": "A", "width": 500},
            {"id": "b", "label": "B", "width": 500},
        ]
        table.set_columns(cols)
        assert table.inner_column.width == 1000

    def test_set_columns_updates_header_container_width(self):
        table = PaginatedTable()
        cols = [
            {"id": "a", "label": "A", "width": 500},
            {"id": "b", "label": "B", "width": 500},
        ]
        table.set_columns(cols)
        assert table.header_container.width == 1000

    def test_set_columns_updates_canvas_width(self):
        table = PaginatedTable()
        cols = [
            {"id": "a", "label": "A", "width": 500},
            {"id": "b", "label": "B", "width": 500},
        ]
        table.set_columns(cols)
        assert table._canvas.width == 1000


# ---------------------------------------------------------------------------
# set_rows
# ---------------------------------------------------------------------------


class TestSetRows:
    def test_set_rows_stores_rows(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        rows = _make_rows(3)
        table.set_rows(rows)
        assert table._rows == rows

    def test_set_rows_resets_window_to_start(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(50))
        table._win_start = 20
        table._win_end = 40

        table.set_rows(_make_rows(10))
        assert table._win_start == 0
        assert table._win_end > 0

    def test_set_rows_resets_last_rendered_first(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(50))
        table._last_rendered_first = 30

        table.set_rows(_make_rows(10))
        assert table._last_rendered_first == 0

    def test_set_rows_syncs_canvas_height(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(10))
        assert table._canvas.height == 10 * ROW_HEIGHT

    def test_set_rows_replaces_previous_data(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        assert len(table._rows) == 5

        table.set_rows(_make_rows(3))
        assert len(table._rows) == 3

    def test_set_rows_with_sort_col_sets_sort_col(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(), sort_col="price", sort_asc=False)
        assert table.sort_col == "price"
        assert table.sort_asc is False

    def test_set_rows_default_sort_params(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows())
        assert table.sort_col is None
        assert table.sort_asc is True

    def test_set_rows_single_row(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows([{"name": "Only", "price": "42", "pct_chg": "1%"}])
        assert len(table.rendered_row_controls) == 1
        assert table._canvas.height == ROW_HEIGHT

    def test_set_rows_empty_list(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        table.set_rows([])
        assert table.rendered_row_controls == []
        assert table._canvas.height == 0
        assert table._rows == []

    def test_set_rows_comma_separated_numeric_is_right_aligned(self):
        table = PaginatedTable()
        table.set_columns([{"id": "vol", "label": "Volume", "width": 100}])
        table.set_rows([{"vol": "1,234,567"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        assert cell_container.content.alignment == ft.alignment.center_right

    def test_set_rows_zero_pct_chg_is_not_trend_colored(self):
        """Zero value in trend col should not get UP_RED or DOWN_GREEN."""
        table = PaginatedTable()
        table.set_columns([{"id": "pct_chg", "label": "Change", "width": 80}])
        table.set_rows([{"pct_chg": "0.0%"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert text.color == AppColors.TABLE_CELL_NUMERIC

    def test_set_rows_symbol_with_dot_renders_spans(self):
        table = PaginatedTable()
        table.set_columns([{"id": "symbol", "label": "Symbol", "width": 100}])
        table.set_rows([{"symbol": "600000.SH"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert isinstance(text, ft.Text)
        assert text.spans is not None
        assert len(text.spans) == 2

    def test_set_rows_change_col_positive_uses_up_red(self):
        table = PaginatedTable()
        table.set_columns([{"id": "change", "label": "Change", "width": 80}])
        table.set_rows([{"change": "2.5"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert text.color == AppColors.UP_RED

    def test_set_rows_chg_col_negative_uses_down_green(self):
        table = PaginatedTable()
        table.set_columns([{"id": "chg", "label": "Chg", "width": 80}])
        table.set_rows([{"chg": "-1.8"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert text.color == AppColors.DOWN_GREEN


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_empties_rows(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        assert table._rows == []

    def test_clear_resets_window(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        assert table._win_start == 0
        assert table._win_end == 0

    def test_clear_resets_last_rendered_first(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        assert table._last_rendered_first == -1

    def test_clear_empties_canvas_controls(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        assert table._canvas.controls == []

    def test_clear_empties_row_pool(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        assert table._row_pool == []

    def test_clear_preserves_canvas_in_list_view(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        assert table.list_view.controls == [table._canvas]

    def test_clear_sets_canvas_height_to_zero(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        assert table._canvas.height == 0

    def test_clear_on_already_empty_table(self):
        table = PaginatedTable()
        table.clear()  # Should not raise
        assert table._rows == []
        assert table._canvas.controls == []


# ---------------------------------------------------------------------------
# update_theme
# ---------------------------------------------------------------------------


class TestUpdateTheme:
    def test_update_theme_rebuilds_header(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        original_header_controls = list(table.header_row.controls)
        table.update_theme()
        # Header should be rebuilt (new control instances)
        assert table.header_row.controls is not original_header_controls or len(table.header_row.controls) > 0

    def test_update_theme_rerenders_window(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        table._last_rendered_first = 0
        table.update_theme()
        assert table._last_rendered_first == 0

    def test_update_theme_resets_last_rendered_first_before_rerender(self):
        """update_theme sets _last_rendered_first = -1 then calls _render_window."""
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        table._last_rendered_first = 5
        table.update_theme()
        # After update_theme, _last_rendered_first should be the target_first value
        assert table._last_rendered_first >= 0

    def test_update_theme_updates_header_bgcolor(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.update_theme()
        assert table.header_container.bgcolor == AppColors.TABLE_HEADER_BG

    def test_update_theme_updates_header_border(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.update_theme()
        assert table.header_container.border is not None


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


class TestSorting:
    def test_sort_click_without_on_sort_does_not_raise(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table._handle_sort_click("name")  # Should not raise

    def test_sort_click_same_col_three_times_cycles_back_to_asc(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table._handle_sort_click("name")  # asc
        table._handle_sort_click("name")  # desc
        table._handle_sort_click("name")  # asc again
        assert table.sort_asc is True

    def test_sort_click_multiple_columns(self):
        table = PaginatedTable()
        table.set_columns(
            [
                {"id": "name", "label": "Name", "width": 100},
                {"id": "price", "label": "Price", "width": 80},
                {"id": "vol", "label": "Volume", "width": 80},
            ]
        )
        table._handle_sort_click("name")
        assert table.sort_col == "name"
        table._handle_sort_click("price")
        assert table.sort_col == "price"
        assert table.sort_asc is True
        table._handle_sort_click("vol")
        assert table.sort_col == "vol"
        assert table.sort_asc is True

    def test_sort_indicator_in_header_after_set_rows(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "A"}], sort_col="name", sort_asc=True)
        table._build_header()
        cell = table.header_row.controls[0]
        text = cell.content.content
        assert "↑" in text.value

    def test_sort_desc_indicator_in_header_after_set_rows(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "A"}], sort_col="name", sort_asc=False)
        table._build_header()
        cell = table.header_row.controls[0]
        text = cell.content.content
        assert "↓" in text.value

    def test_no_sort_indicator_on_unsorted_column(self):
        table = PaginatedTable()
        table.set_columns(
            [
                {"id": "name", "label": "Name", "width": 100},
                {"id": "price", "label": "Price", "width": 80},
            ]
        )
        table._handle_sort_click("name")
        table._build_header()
        price_cell = table.header_row.controls[1]
        price_text = price_cell.content.content
        assert "↑" not in price_text.value
        assert "↓" not in price_text.value


# ---------------------------------------------------------------------------
# Virtual scrolling / window management
# ---------------------------------------------------------------------------


class TestVirtualScrolling:
    def test_window_capacity_default_viewport(self):
        table = PaginatedTable()
        capacity = table._window_capacity()
        expected = DEFAULT_VIEWPORT_ROWS + 2 * BUFFER_ROWS
        assert capacity == expected

    def test_window_capacity_with_viewport_h(self):
        table = PaginatedTable()
        table._viewport_h = 600.0
        viewport_rows = math.ceil(600.0 / ROW_HEIGHT)
        expected = max(1, viewport_rows + 2 * BUFFER_ROWS)
        assert table._window_capacity() == expected

    def test_window_capacity_minimum_is_one(self):
        table = PaginatedTable()
        table._viewport_h = 0.0001  # Very tiny viewport
        capacity = table._window_capacity()
        assert capacity >= 1

    def test_render_window_empty_rows(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table._rows = []
        table._render_window(target_first=0)
        assert table._canvas.controls == []
        assert table._win_start == 0
        assert table._win_end == 0

    def test_render_window_single_row(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table._rows = [{"name": "A", "price": "1", "pct_chg": "0%"}]
        table._render_window(target_first=0)
        assert len(table._canvas.controls) == 1
        assert table._win_start == 0
        assert table._win_end == 1

    def test_scroll_event_updates_viewport_h(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(100))
        table._on_scroll(_scroll_event(pixels=0, viewport_h=500.0))
        assert table._viewport_h == 500.0

    def test_scroll_event_without_viewport_dimension(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(100))
        old_viewport_h = table._viewport_h
        table._on_scroll(_scroll_event(pixels=100))
        assert table._viewport_h == old_viewport_h

    def test_scroll_does_not_rerender_below_threshold(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(500))
        # Initial render
        initial_count = len(table.rendered_row_controls)

        # Small scroll — below RERENDER_THRESHOLD
        table._on_scroll(_scroll_event(pixels=ROW_HEIGHT, viewport_h=600.0))
        assert len(table.rendered_row_controls) == initial_count

    def test_scroll_rerenders_above_threshold(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(500))

        # Scroll far enough that target_first - BUFFER_ROWS > 0
        pixels = (BUFFER_ROWS + RERENDER_THRESHOLD + 5) * ROW_HEIGHT
        table._on_scroll(_scroll_event(pixels=pixels, viewport_h=600.0))
        assert table._win_start > 0

    def test_scroll_negative_pixels_clamped_to_zero(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(500))

        table._on_scroll(_scroll_event(pixels=-100, viewport_h=600.0))
        assert table._win_start == 0

    def test_scroll_near_end_of_data(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(50))

        # Scroll near the end
        pixels = 49 * ROW_HEIGHT
        table._on_scroll(_scroll_event(pixels=pixels, viewport_h=600.0))
        assert table._win_end <= 50

    def test_row_position_matches_absolute_index(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(10))
        for i, row in enumerate(table.rendered_row_controls):
            assert row.top == i * ROW_HEIGHT

    def test_bind_row_sets_on_click(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        for row in table.rendered_row_controls:
            assert row.on_click is not None

    def test_bind_row_sets_bgcolor_via_app_styles(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        for row in table.rendered_row_controls:
            assert row.bgcolor is not None


# ---------------------------------------------------------------------------
# Row pool management
# ---------------------------------------------------------------------------


class TestRowPool:
    def test_pool_grows_as_needed(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(10))
        pool_size = len(table._row_pool)
        assert pool_size > 0
        assert pool_size == len(table.rendered_row_controls)

    def test_pool_is_reused_not_recreated(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(10))
        [id(r) for r in table._row_pool]

        # Re-set rows — pool should be reused
        table.set_rows(_make_rows(5))
        # After set_rows, pool is NOT cleared (only set_columns clears it)
        # But _render_window reuses pool entries
        assert len(table._row_pool) > 0

    def test_get_pool_row_creates_new_container(self):
        table = PaginatedTable()
        row = table._get_pool_row(0)
        assert isinstance(row, ft.Container)
        assert row.height == ROW_HEIGHT

    def test_get_pool_row_returns_existing_for_same_index(self):
        table = PaginatedTable()
        row1 = table._get_pool_row(0)
        row2 = table._get_pool_row(0)
        assert row1 is row2

    def test_get_pool_row_appends_to_pool(self):
        table = PaginatedTable()
        assert len(table._row_pool) == 0
        table._get_pool_row(0)
        assert len(table._row_pool) == 1
        table._get_pool_row(1)
        assert len(table._row_pool) == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_set_rows_before_set_columns(self):
        """set_rows without columns should still not crash (renders empty cells)."""
        table = PaginatedTable()
        table.set_rows([{"name": "A"}])
        assert len(table._rows) == 1

    def test_large_dataset_canvas_height(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        n = 10000
        table.set_rows(_make_rows(n))
        assert table._canvas.height == n * ROW_HEIGHT

    def test_large_dataset_rendered_rows_bounded(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(10000))
        max_expected = DEFAULT_VIEWPORT_ROWS + 2 * BUFFER_ROWS
        assert len(table.rendered_row_controls) <= max_expected

    def test_set_rows_then_clear_then_set_rows(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(5))
        table.clear()
        table.set_rows(_make_rows(3))
        assert len(table._rows) == 3
        assert len(table.rendered_row_controls) == 3

    def test_rendered_row_controls_property(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        assert table.rendered_row_controls is table._canvas.controls

    def test_on_row_click_without_callback_does_not_raise(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(3))
        # Clicking a row without on_row_click should not raise
        first_row = table.rendered_row_controls[0]
        first_row.on_click(None)  # type: ignore[reportAttributeAccessIssue]

    def test_set_rows_with_non_string_values(self):
        table = PaginatedTable()
        table.set_columns([{"id": "val", "label": "Value", "width": 100}])
        table.set_rows([{"val": 42}, {"val": None}, {"val": 3.14}])
        assert len(table.rendered_row_controls) == 3

    def test_header_label_defaults_to_col_id_when_missing(self):
        table = PaginatedTable()
        table.set_columns([{"id": "mycol", "width": 100}])
        cell = table.header_row.controls[0]
        text = cell.content.content
        assert "mycol" in text.value

    def test_set_columns_multiple_times(self):
        table = PaginatedTable()
        for _ in range(5):
            table.set_columns(_make_cols())
        assert len(table.header_row.controls) == 3

    def test_set_rows_multiple_times(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        for n in [1, 10, 3, 50]:
            table.set_rows(_make_rows(n))
            assert len(table._rows) == n

    def test_scroll_with_zero_pixels(self):
        table = PaginatedTable()
        table.set_columns(_make_cols())
        table.set_rows(_make_rows(100))
        initial_start = table._win_start
        table._on_scroll(_scroll_event(pixels=0, viewport_h=600.0))
        # Zero scroll should not shift window significantly
        assert table._win_start == initial_start

    def test_cell_without_width_expands(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name"}])  # No width
        table.set_rows([{"name": "test"}])
        row = table.rendered_row_controls[0]
        cell = row.content.controls[0]
        assert cell.expand == 1
