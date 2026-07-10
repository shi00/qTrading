import flet as ft

from ui.components.virtual_table import PaginatedTable
from ui.components.toast_manager import ToastCard
from ui.components.health_report_dialog import (
    CoverageDetailTable,
    HealthScoreCard,
    KeyMetricsGrid,
    MetricTile,
)
from ui.theme import AppColors
import pytest


pytestmark = pytest.mark.unit


class TestPaginatedTable:
    def test_init_has_no_columns(self):
        table = PaginatedTable()
        assert table.columns_def == []

    def test_init_sort_asc_is_true(self):
        table = PaginatedTable()
        assert table.sort_asc is True

    def test_init_sort_col_is_none(self):
        table = PaginatedTable()
        assert table.sort_col is None

    def test_set_columns_stores_definitions(self):
        table = PaginatedTable()
        cols = [
            {"id": "name", "label": "Name", "width": 100},
            {"id": "price", "label": "Price", "width": 80},
        ]
        table.set_columns(cols)
        assert table.columns_def == cols

    def test_set_columns_builds_header_row(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        assert len(table.header_row.controls) == 1

    def test_set_columns_header_text_shows_label(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        cell = table.header_row.controls[0]
        text = cell.content.content
        assert "Name" in text.value

    def test_set_columns_missing_width_defaults_to_100(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name"}])
        cell = table.header_row.controls[0]
        assert cell.width == 100

    def test_handle_sort_click_same_column_toggles_direction(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table._handle_sort_click("name")
        assert table.sort_col == "name"
        assert table.sort_asc is True
        table._handle_sort_click("name")
        assert table.sort_asc is False

    def test_handle_sort_click_new_column_defaults_asc(self):
        table = PaginatedTable()
        table.set_columns(
            [
                {"id": "name", "label": "Name", "width": 100},
                {"id": "price", "label": "Price", "width": 80},
            ]
        )
        table._handle_sort_click("name")
        table._handle_sort_click("price")
        assert table.sort_col == "price"
        assert table.sort_asc is True

    def test_handle_sort_click_calls_on_sort_callback(self):
        callback_calls = []
        table = PaginatedTable(on_sort=lambda col, asc: callback_calls.append((col, asc)))
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table._handle_sort_click("name")
        assert len(callback_calls) == 1
        assert callback_calls[0] == ("name", True)

    def test_handle_sort_click_shows_asc_indicator(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table._handle_sort_click("name")
        table._build_header()
        cell = table.header_row.controls[0]
        text = cell.content.content
        assert "↑" in text.value

    def test_handle_sort_click_shows_desc_indicator(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table._handle_sort_click("name")
        table._handle_sort_click("name")
        table._build_header()
        cell = table.header_row.controls[0]
        text = cell.content.content
        assert "↓" in text.value

    def test_set_rows_with_empty_data_clears_canvas(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "test"}])
        table.set_rows([])
        assert len(table.rendered_row_controls) == 0
        assert table._canvas.height == 0

    def test_set_rows_creates_row_controls(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "AAPL"}, {"name": "GOOG"}])
        assert len(table.rendered_row_controls) == 2

    def test_set_rows_numeric_data_right_aligned(self):
        table = PaginatedTable()
        table.set_columns([{"id": "price", "label": "Price", "width": 80}])
        table.set_rows([{"price": "123.45"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        assert cell_container.content.alignment == ft.Alignment.CENTER_RIGHT

    def test_set_rows_string_data_left_aligned(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "AAPL"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        assert cell_container.content.alignment == ft.Alignment.CENTER_LEFT

    def test_set_rows_pct_chg_positive_uses_up_red_color(self):
        table = PaginatedTable()
        table.set_columns([{"id": "pct_chg", "label": "Change", "width": 80}])
        table.set_rows([{"pct_chg": "5.2%"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert text.color == AppColors.UP_RED

    def test_set_rows_pct_chg_negative_uses_down_green_color(self):
        table = PaginatedTable()
        table.set_columns([{"id": "pct_chg", "label": "Change", "width": 80}])
        table.set_rows([{"pct_chg": "-3.1%"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert text.color == AppColors.DOWN_GREEN

    def test_set_rows_with_sort_params(self):
        table = PaginatedTable()
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "B"}, {"name": "A"}], sort_col="name", sort_asc=False)
        assert table.sort_col == "name"
        assert table.sort_asc is False

    def test_set_rows_missing_column_value_shows_empty_string(self):
        table = PaginatedTable()
        table.set_columns(
            [
                {"id": "name", "label": "Name", "width": 100},
                {"id": "price", "label": "Price", "width": 80},
            ]
        )
        table.set_rows([{"name": "AAPL"}])
        row = table.rendered_row_controls[0]
        price_cell = row.content.controls[1]
        text = price_cell.content.content
        assert text.value == ""

    def test_set_rows_ts_code_with_dot_renders_spans(self):
        table = PaginatedTable()
        table.set_columns([{"id": "ts_code", "label": "Code", "width": 100}])
        table.set_rows([{"ts_code": "000001.SZ"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert isinstance(text, ft.Text)
        assert text.spans is not None
        assert len(text.spans) == 2

    def test_set_rows_numeric_font_is_monospace(self):
        table = PaginatedTable()
        table.set_columns([{"id": "price", "label": "Price", "width": 80}])
        table.set_rows([{"price": "123.45"}])
        row = table.rendered_row_controls[0]
        cell_container = row.content.controls[0]
        text = cell_container.content.content
        assert text.font_family is not None
        assert "monospace" in text.font_family

    def test_on_row_click_callback_receives_correct_row_data(self):
        callback_calls = []
        table = PaginatedTable()
        table.on_row_click = lambda r: callback_calls.append(r)
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "AAPL"}, {"name": "GOOG"}])

        # Simulate clicking the first row by invoking its on_click handler
        first_row = table.rendered_row_controls[0]
        assert first_row.on_click is not None  # type: ignore[reportAttributeAccessIssue]
        first_row.on_click(None)

        assert len(callback_calls) == 1
        assert callback_calls[0] == {"name": "AAPL"}

    def test_on_row_click_callback_receives_second_row_data(self):
        callback_calls = []
        table = PaginatedTable()
        table.on_row_click = lambda r: callback_calls.append(r)
        table.set_columns([{"id": "name", "label": "Name", "width": 100}])
        table.set_rows([{"name": "AAPL"}, {"name": "GOOG"}])

        second_row = table.rendered_row_controls[1]
        assert second_row.on_click is not None  # type: ignore[reportAttributeAccessIssue]
        second_row.on_click(None)

        assert len(callback_calls) == 1
        assert callback_calls[0] == {"name": "GOOG"}


class TestToastCard:
    def test_init_short_message_has_no_expand_button(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.expand_btn is None

    def test_init_long_message_has_expand_button(self):
        long_msg = "x" * 81
        card = ToastCard(
            message=long_msg,
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.expand_btn is not None

    def test_init_short_message_max_lines_is_3(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.text_control.max_lines == 3

    def test_init_text_width_is_270(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.text_control.width == 270

    def test_init_border_left_color_matches_param(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.border_left.color == AppColors.INFO

    def test_init_bgcolor_is_surface(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.bgcolor == ft.Colors.SURFACE

    def test_init_offset_is_slide_in_start(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.offset.x == 1.1

    def test_init_opacity_is_zero(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.opacity == 0

    def test_init_duration_stored(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=15,
            on_dismiss=lambda c: None,
        )
        assert card.duration == 15
        assert card.remaining == 15

    def test_init_icon_displayed_in_content(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.CHECK_CIRCLE,
            color=AppColors.SUCCESS,
            duration=10,
            on_dismiss=lambda c: None,
        )
        icons = [c for c in card.content.controls if isinstance(c, ft.Icon)]
        assert len(icons) == 1
        assert icons[0].icon == ft.Icons.CHECK_CIRCLE

    def test_init_has_close_button(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        close_btns = [c for c in card.content.controls if isinstance(c, ft.IconButton)]
        assert len(close_btns) == 1
        assert close_btns[0].icon == ft.Icons.CLOSE

    def test_cancel_timer_sets_cancelled_flag(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card._is_cancelled is False
        card.cancel_timer()
        assert card._is_cancelled is True

    def test_init_long_message_expand_btn_icon_is_arrow_down(self):
        long_msg = "x" * 81
        card = ToastCard(
            message=long_msg,
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.expand_btn.icon == ft.Icons.KEYBOARD_ARROW_DOWN

    def test_init_padding_is_12(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.padding == 12

    def test_init_border_radius_is_8(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.border_radius == 8

    def test_init_shadow_is_set(self):
        card = ToastCard(
            message="Short msg",
            icon=ft.Icons.INFO,
            color=AppColors.INFO,
            duration=10,
            on_dismiss=lambda c: None,
        )
        assert card.shadow is not None


class TestHealthScoreCard:
    def test_init_green_status_uses_success_color(self):
        card = HealthScoreCard(status="green", tables_count=5)
        assert card.color == AppColors.SUCCESS

    def test_init_green_status_uses_check_circle_icon(self):
        card = HealthScoreCard(status="green", tables_count=5)
        assert card.icon == ft.Icons.CHECK_CIRCLE

    def test_init_yellow_status_uses_warning_color(self):
        card = HealthScoreCard(status="yellow", tables_count=5)
        assert card.color == AppColors.WARNING

    def test_init_yellow_status_uses_warning_rounded_icon(self):
        card = HealthScoreCard(status="yellow", tables_count=5)
        assert card.icon == ft.Icons.WARNING_ROUNDED

    def test_init_red_status_uses_error_color(self):
        card = HealthScoreCard(status="red", tables_count=5)
        assert card.color == AppColors.ERROR

    def test_init_red_status_uses_error_outline_icon(self):
        card = HealthScoreCard(status="red", tables_count=5)
        assert card.icon == ft.Icons.ERROR_OUTLINE

    def test_init_unknown_status_uses_error_color(self):
        card = HealthScoreCard(status="unknown", tables_count=5)
        assert card.color == AppColors.ERROR

    def test_init_border_uses_status_color(self):
        card = HealthScoreCard(status="green", tables_count=5)
        assert card.border is not None

    def test_init_gradient_is_set(self):
        card = HealthScoreCard(status="green", tables_count=5)
        assert card.gradient is not None

    def test_init_padding_is_20(self):
        card = HealthScoreCard(status="green", tables_count=5)
        assert card.padding == 20

    def test_init_border_radius_is_8(self):
        card = HealthScoreCard(status="green", tables_count=5)
        assert card.border_radius == 8


class TestMetricTile:
    def test_init_label_displayed(self):
        tile = MetricTile(label="Lag", value="3")
        texts = tile.content.controls
        assert texts[0].value == "Lag"

    def test_init_label_color_is_text_secondary(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.content.controls[0].color == AppColors.TEXT_SECONDARY

    def test_init_value_displayed(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.content.controls[1].value == "3"

    def test_init_value_size_is_18(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.content.controls[1].size == 18

    def test_init_value_weight_is_bold(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.content.controls[1].weight == ft.FontWeight.BOLD

    def test_init_default_trend_color_is_text_primary(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.content.controls[1].color == AppColors.TEXT_PRIMARY

    def test_init_custom_trend_color(self):
        tile = MetricTile(label="Lag", value="3", trend_color=AppColors.ERROR)
        assert tile.content.controls[1].color == AppColors.ERROR

    def test_init_with_sub_text_adds_extra_text(self):
        tile = MetricTile(label="Lag", value="3", sub_text="days behind")
        assert len(tile.content.controls) == 3
        assert tile.content.controls[2].value == "days behind"

    def test_init_without_sub_text_has_two_controls(self):
        tile = MetricTile(label="Lag", value="3")
        assert len(tile.content.controls) == 2

    def test_init_sub_text_color_is_text_hint(self):
        tile = MetricTile(label="Lag", value="3", sub_text="days behind")
        assert tile.content.controls[2].color == AppColors.TEXT_HINT

    def test_init_bgcolor_is_surface_variant(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.bgcolor == AppColors.SURFACE_VARIANT

    def test_init_border_radius_is_6(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.border_radius == 6

    def test_init_expand_is_true(self):
        tile = MetricTile(label="Lag", value="3")
        assert tile.expand is True

    def test_init_none_value_converted_to_string(self):
        tile = MetricTile(label="Lag", value=None)
        assert tile.content.controls[1].value == "None"


class TestKeyMetricsGrid:
    def test_init_with_zero_lag_uses_success_color(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 0, "latest_local": "2024-01-01"},
            fundamentals={"gap_count": 0, "sanity_errors": 0},
        )
        lag_tile = grid.controls[1].controls[0]
        assert lag_tile.content.controls[1].color == AppColors.SUCCESS

    def test_init_with_positive_lag_uses_error_color(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 3, "latest_local": "2024-01-01"},
            fundamentals={"gap_count": 0, "sanity_errors": 0},
        )
        lag_tile = grid.controls[1].controls[0]
        assert lag_tile.content.controls[1].color == AppColors.ERROR

    def test_init_with_zero_gaps_uses_success_color(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 0, "latest_local": "2024-01-01"},
            fundamentals={"gap_count": 0, "sanity_errors": 0},
        )
        gap_tile = grid.controls[1].controls[1]
        assert gap_tile.content.controls[1].color == AppColors.SUCCESS

    def test_init_with_positive_gaps_uses_error_color(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 0, "latest_local": "2024-01-01"},
            fundamentals={"gap_count": 5, "sanity_errors": 0},
        )
        gap_tile = grid.controls[1].controls[1]
        assert gap_tile.content.controls[1].color == AppColors.ERROR

    def test_init_with_zero_sanity_errors_uses_success_color(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 0, "latest_local": "2024-01-01"},
            fundamentals={"gap_count": 0, "sanity_errors": 0},
        )
        sanity_tile = grid.controls[1].controls[2]
        assert sanity_tile.content.controls[1].color == AppColors.SUCCESS

    def test_init_with_sanity_errors_uses_error_color(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 0, "latest_local": "2024-01-01"},
            fundamentals={"gap_count": 0, "sanity_errors": 2},
        )
        sanity_tile = grid.controls[1].controls[2]
        assert sanity_tile.content.controls[1].color == AppColors.ERROR

    def test_init_missing_market_data_defaults_lag_to_zero(self):
        grid = KeyMetricsGrid(
            market={},
            fundamentals={"gap_count": 0, "sanity_errors": 0},
        )
        lag_tile = grid.controls[1].controls[0]
        assert lag_tile.content.controls[1].color == AppColors.SUCCESS

    def test_init_missing_fundamentals_defaults_to_zero(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 0, "latest_local": "2024-01-01"},
            fundamentals={},
        )
        gap_tile = grid.controls[1].controls[1]
        assert gap_tile.content.controls[1].color == AppColors.SUCCESS

    def test_init_spacing_is_10(self):
        grid = KeyMetricsGrid(
            market={"lag_days": 0},
            fundamentals={"gap_count": 0, "sanity_errors": 0},
        )
        assert grid.spacing == 10


class TestCoverageDetailTable:
    def test_init_with_empty_tables(self):
        table = CoverageDetailTable(tables={})
        assert len(table.controls) == 0

    def test_init_with_stock_table_creates_section(self):
        tables = {
            "daily_quotes": {
                "ratio": 0.95,
                "fresh_ratio": 0.90,
                "type": "stock",
            }
        }
        table = CoverageDetailTable(tables=tables)
        assert len(table.controls) >= 2

    def test_init_with_global_table_creates_section(self):
        tables = {
            "index_daily": {
                "ratio": 1.0,
                "fresh_ratio": 1.0,
                "type": "global",
                "covered": 10,
            }
        }
        table = CoverageDetailTable(tables=tables)
        assert len(table.controls) >= 2

    def test_init_excellent_ratio_uses_success_color(self):
        tables = {
            "daily_quotes": {
                "ratio": 0.99,
                "fresh_ratio": 0.95,
                "type": "stock",
            }
        }
        table = CoverageDetailTable(tables=tables)
        row_container = table.controls[-1]
        row_content = row_container.content
        icon = row_content.controls[0].controls[0]
        assert icon.color == AppColors.SUCCESS

    def test_init_warning_ratio_uses_warning_color(self):
        tables = {
            "daily_quotes": {
                "ratio": 0.92,
                "fresh_ratio": 0.85,
                "type": "stock",
            }
        }
        table = CoverageDetailTable(tables=tables)
        row_container = table.controls[-1]
        row_content = row_container.content
        icon = row_content.controls[0].controls[0]
        assert icon.color == AppColors.WARNING

    def test_init_low_ratio_uses_error_color(self):
        tables = {
            "daily_quotes": {
                "ratio": 0.50,
                "fresh_ratio": 0.30,
                "type": "stock",
            }
        }
        table = CoverageDetailTable(tables=tables)
        row_container = table.controls[-1]
        row_content = row_container.content
        icon = row_content.controls[0].controls[0]
        assert icon.color == AppColors.ERROR

    def test_init_global_and_stock_creates_both_sections(self):
        tables = {
            "index_daily": {
                "ratio": 1.0,
                "fresh_ratio": 1.0,
                "type": "global",
                "covered": 10,
            },
            "daily_quotes": {
                "ratio": 0.95,
                "fresh_ratio": 0.90,
                "type": "stock",
            },
        }
        table = CoverageDetailTable(tables=tables)
        section_headers = [
            c
            for c in table.controls
            if isinstance(c, ft.Container)
            and isinstance(c.content, ft.Row)
            and any(isinstance(sub, ft.Icon) and sub.icon == ft.Icons.SUBTITLES for sub in c.content.controls)
        ]
        assert len(section_headers) == 2

    def test_init_stock_table_has_progress_bar(self):
        tables = {
            "daily_quotes": {
                "ratio": 0.95,
                "fresh_ratio": 0.90,
                "type": "stock",
            }
        }
        table = CoverageDetailTable(tables=tables)
        row_container = table.controls[-1]
        row_content = row_container.content
        progress_bars = [c for c in row_content.controls if isinstance(c, ft.ProgressBar)]
        assert len(progress_bars) == 1
        assert progress_bars[0].value == 0.95

    def test_init_spacing_is_10(self):
        tables = {
            "daily_quotes": {
                "ratio": 0.95,
                "fresh_ratio": 0.90,
                "type": "stock",
            }
        }
        table = CoverageDetailTable(tables=tables)
        assert table.spacing == 10


class TestPaginatedTableVirtualization:
    def _cols(self):
        return [{"id": "name", "label": "Name", "width": 100}]

    def test_large_page_renders_bounded_row_controls(self):
        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        # DEFAULT_VIEWPORT_ROWS=30 + 2*BUFFER_ROWS=16 = 46
        assert len(table.rendered_row_controls) == 46

    def test_canvas_height_represents_full_page_height(self):
        from ui.components.virtual_table import ROW_HEIGHT

        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        assert table._canvas.height == 500 * ROW_HEIGHT

    def test_initial_rows_are_positioned_at_absolute_offsets(self):
        from ui.components.virtual_table import ROW_HEIGHT

        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        rows = table.rendered_row_controls
        assert rows[0].top == 0
        assert rows[1].top == ROW_HEIGHT

    def test_scroll_shifts_window_without_growing_attached_rows(self):
        from ui.components.virtual_table import ROW_HEIGHT

        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        first_count = len(table.rendered_row_controls)

        class _Evt:
            pixels = 200 * ROW_HEIGHT
            viewport_dimension = 20 * ROW_HEIGHT

        table._on_scroll(_Evt())
        assert table._win_start > 0
        assert len(table.rendered_row_controls) <= first_count
        assert table.rendered_row_controls[0].top == table._win_start * ROW_HEIGHT  # type: ignore[union-attr]

    def test_row_pool_is_reused_on_scroll(self):
        from ui.components.virtual_table import ROW_HEIGHT

        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        first_row_id = id(table.rendered_row_controls[0])

        class _Evt:
            pixels = 200 * ROW_HEIGHT
            viewport_dimension = 20 * ROW_HEIGHT

        table._on_scroll(_Evt())
        assert id(table.rendered_row_controls[0]) == first_row_id

    def test_set_columns_rebuilds_pool_without_dropping_rows(self):
        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": "A"}])
        assert table._row_pool
        first_row_id = id(table.rendered_row_controls[0])

        table.set_columns([{"id": "price", "label": "Price", "width": 80}])
        assert table.rendered_row_controls
        assert id(table.rendered_row_controls[0]) != first_row_id
        assert len(table._row_pool) == len(table.rendered_row_controls)

    def test_clear_releases_rows_but_preserves_canvas_in_list_view(self):
        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": "A"}, {"name": "B"}])
        assert table.rendered_row_controls

        table.clear()
        assert table.rendered_row_controls == []
        assert table._row_pool == []
        # _canvas must remain in list_view.controls for re-mount
        assert table.list_view.controls == [table._canvas]
        assert table._canvas.height == 0

    def test_refresh_viewport_is_public_method(self):
        table = PaginatedTable()
        assert callable(getattr(table, "refresh_viewport", None))

    def test_refresh_viewport_with_no_rows_does_nothing(self):
        table = PaginatedTable()
        table.set_columns(self._cols())
        table.refresh_viewport(viewport_height=600)
        assert len(table.rendered_row_controls) == 0

    def test_refresh_viewport_recalculates_visible_rows_for_taller_viewport(self):
        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        # Initially uses DEFAULT_VIEWPORT_ROWS=30 + 2*BUFFER_ROWS=16 = 46
        assert len(table.rendered_row_controls) == 46
        # Simulate a taller viewport via refresh_viewport
        table.refresh_viewport(viewport_height=60 * 30)
        # 60 + 2*BUFFER_ROWS(16) = 76
        assert len(table.rendered_row_controls) == 76

    def test_refresh_viewport_reduces_rows_for_smaller_viewport(self):
        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        assert len(table.rendered_row_controls) == 46
        # Shrink viewport
        table.refresh_viewport(viewport_height=10 * 30)
        # 10 + 2*BUFFER_ROWS(16) = 26
        assert len(table.rendered_row_controls) == 26

    def test_refresh_viewport_without_height_uses_existing_viewport(self):
        table = PaginatedTable()
        table.set_columns(self._cols())
        table.set_rows([{"name": f"S{i}"} for i in range(500)])
        # Set a viewport height via scroll event first
        from ui.components.virtual_table import ROW_HEIGHT

        class _Evt:
            pixels = 0
            viewport_dimension = 20 * ROW_HEIGHT

        # Force re-render since _on_scroll skips when delta < RERENDER_THRESHOLD
        table._last_rendered_first = -1
        table._on_scroll(_Evt())
        assert len(table.rendered_row_controls) == 36
        # refresh_viewport without arg should reuse the scroll-set height
        table.refresh_viewport()
        assert len(table.rendered_row_controls) == 36
