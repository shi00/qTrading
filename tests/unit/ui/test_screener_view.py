import asyncio
import datetime
from unittest.mock import MagicMock, patch

import flet as ft
import pandas as pd
import pytest


from ui.views.screener_view import (
    _COLUMN_WIDTHS,
    _build_table_data,
    _format_cell_value,
    ScreenerView,
)
from tests.unit.ui.conftest import set_page, wrap_mock_page


def _asyncio_dummy():
    fut = asyncio.Future()
    fut.set_result(None)
    return fut


def _asyncio_result(val):
    fut = asyncio.Future()
    fut.set_result(val)
    return fut


class TestFormatCellValue:
    def test_nan_returns_dash(self):
        result = _format_cell_value("close", float("nan"))
        assert result == "-"

    def test_strategy_name_translates(self):
        with patch("ui.views.screener_view.translate_strategy_name", return_value="策略A"):
            with patch("ui.views.screener_view.I18n"):
                result = _format_cell_value("strategy_name", "strategy_a")
                assert result == "策略A"

    def test_date_col_with_datetime(self):
        dt = datetime.date(2024, 1, 15)
        result = _format_cell_value("trade_date", dt)
        assert result == "2024-01-15"

    def test_date_col_with_8digit_string(self):
        result = _format_cell_value("trade_date", "20240115")
        assert result == "2024-01-15"

    def test_date_col_with_non_date_string(self):
        result = _format_cell_value("trade_date", "notadate")
        assert result == "notadate"

    def test_volume_col_over_yi(self):
        with patch("ui.views.screener_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: "亿" if key == "unit_yi" else key
            result = _format_cell_value("vol", 2_000_000_000)
            assert "亿" in result

    def test_volume_col_over_wan(self):
        with patch("ui.views.screener_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: "万" if key == "unit_wan" else key
            result = _format_cell_value("vol", 50_000)
            assert "万" in result

    def test_volume_col_small(self):
        result = _format_cell_value("vol", 9999)
        assert "9,999" in result

    def test_float_format_two_decimals(self):
        result = _format_cell_value("close", 12.3456)
        assert result == "12.35"

    def test_ts_code_not_formatted(self):
        result = _format_cell_value("ts_code", "000001.SZ")
        assert result == "000001.SZ"

    def test_string_value_returns_str(self):
        result = _format_cell_value("name", "平安银行")
        assert result == "平安银行"


class TestBuildTableData:
    def test_hides_hidden_columns(self):
        df = pd.DataFrame({"symbol": ["s1"], "ts_code": ["000001.SZ"], "name": ["test"]})
        cols, rows = _build_table_data(df)
        col_ids = [c["id"] for c in cols]
        assert "symbol" not in col_ids
        assert "ts_code" in col_ids
        assert "name" in col_ids

    def test_uses_custom_width(self):
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        cols, _ = _build_table_data(df)
        assert cols[0]["width"] == _COLUMN_WIDTHS["ts_code"]

    def test_default_width_for_unknown_col(self):
        df = pd.DataFrame({"unknown_col": ["val"]})
        cols, _ = _build_table_data(df)
        assert cols[0]["width"] == 80

    def test_formats_rows(self):
        df = pd.DataFrame({"name": ["test"], "close": [12.34]})
        _, rows = _build_table_data(df)
        assert len(rows) == 1
        assert rows[0]["name"] == "test"
        assert rows[0]["close"] == "12.34"


class TestScreenerView:
    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_vm = MagicMock()
        self.mock_vm.strategy_mgr = MagicMock()
        self.mock_vm.dispose = MagicMock()
        self.mock_vm.switch_to_history = MagicMock()
        self.mock_vm.switch_to_realtime = MagicMock()
        self.mock_vm.get_current_page_data.return_value = None
        self.mock_vm.page_no = 1
        self.mock_vm.total_pages = 0
        self.mock_vm.total_items = 0
        self.mock_vm.sort_column = None
        self.mock_vm.sort_ascending = True
        self.patches = [
            patch("ui.views.screener_view.I18n", self.mock_i18n),
            patch("ui.views.screener_view.AppColors", self.mock_ac),
            patch("ui.views.screener_view.AppStyles", self.mock_as),
            patch("ui.views.screener_view.ScreenerViewModel", return_value=self.mock_vm),
            patch("ui.views.screener_view.PaginatedTable", MagicMock()),
            patch("ui.views.screener_view.TaskManager", return_value=MagicMock()),
            patch("ui.views.screener_view.UILogger"),
            patch("ui.views.screener_view.MetaDataManager"),
            patch("ui.views.screener_view.StockDetailDialog"),
            patch(
                "flet.core.control.Control.update"
            ),  # no-op: Flet update() requires page binding; UI state tested via mock attributes
        ]
        for p in self.patches:
            p.start()
        yield
        for p in self.patches:
            p.stop()

    def _make_view(self, mock_page):
        view = ScreenerView(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        return view

    def test_instantiation_creates_controls(self, mock_page):
        view = self._make_view(mock_page)
        assert view.strategy_dropdown is not None
        assert view.run_btn is not None
        assert view.export_btn is not None
        assert view.result_table is not None

    def test_initial_run_btn_disabled(self, mock_page):
        view = self._make_view(mock_page)
        assert view.run_btn.disabled is True

    def test_initial_export_btn_disabled(self, mock_page):
        view = self._make_view(mock_page)
        assert view.export_btn.disabled is True

    def test_on_strategy_change_enables_run_btn(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = "momentum"
        view.vm.strategy_mgr.get_strategy.return_value = None
        view.vm.get_strategy_desc.return_value = "desc"
        e = MagicMock()
        view._on_strategy_change(e)
        assert view.run_btn.disabled is False
        assert view.selected_strategy == "momentum"

    def test_on_strategy_change_disables_run_btn_when_none(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = None
        e = MagicMock()
        view._on_strategy_change(e)
        assert view.run_btn.disabled is True

    def test_on_strategy_change_updates_desc(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = "momentum"
        view.vm.strategy_mgr.get_strategy.return_value = None
        view.vm.get_strategy_desc.return_value = "Momentum strategy"
        e = MagicMock()
        view._on_strategy_change(e)
        assert view.strategy_desc_text.value == "Momentum strategy"

    def test_on_strategy_change_with_strategy_obj(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = "momentum"
        mock_strategy = MagicMock()
        mock_strategy.get_parameters.return_value = []
        mock_strategy.get_dynamic_description.return_value = "Dynamic desc"
        view.vm.strategy_mgr.get_strategy.return_value = mock_strategy
        e = MagicMock()
        view._on_strategy_change(e)
        assert view.strategy_desc_text.value == "Dynamic desc"

    @pytest.mark.asyncio
    async def test_on_run_click_disables_btn(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.run_strategy = MagicMock(return_value=_asyncio_dummy())
        await view._on_run_click(None)
        assert view.run_btn.disabled is True

    @pytest.mark.asyncio
    async def test_on_run_click_skips_when_no_strategy(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = None
        await view._on_run_click(None)
        view.vm.run_strategy.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_export_click_no_data(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.get_export_data.return_value = None
        await view._on_export_click(None)
        mock_page.show_toast.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_export_click_with_data_opens_picker(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.get_export_data.return_value = pd.DataFrame({"a": [1]})
        view.save_file_picker.save_file = MagicMock()
        with patch("ui.views.screener_view.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 1, 1, 12, 0, 0)
            await view._on_export_click(None)
        view.save_file_picker.save_file.assert_called_once()

    def test_on_save_file_result_no_path(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.path = None
        view._on_save_file_result(e)

    def test_on_save_file_result_with_path(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.path = "/tmp/test.csv"
        view._on_save_file_result(e)
        mock_page.run_task.assert_called()

    def test_on_mode_change_to_history(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.control.selected = {"HISTORY"}
        view._on_mode_change(e)
        mock_page.run_task.assert_called()

    def test_on_mode_change_to_realtime(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.control.selected = {"REALTIME"}
        view._on_mode_change(e)
        mock_page.run_task.assert_called()

    def test_on_mode_change_empty_selection(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.control.selected = set()
        view._on_mode_change(e)
        mock_page.run_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_switch_to_history_mode(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_dummy())
        await view._switch_to_history_mode()
        assert view.history_tree_container.visible is True
        assert view.history_tree_container.width == 250
        assert view.realtime_controls.visible is False
        assert view.log_card.visible is False
        assert view.run_btn.visible is False
        view.vm.switch_to_history.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_to_realtime_mode(self, mock_page):
        view = self._make_view(mock_page)
        await view._switch_to_realtime_mode()
        assert view.history_tree_container.visible is False
        assert view.history_tree_container.width == 0
        assert view.realtime_controls.visible is True
        assert view.log_card.visible is True
        assert view.run_btn.visible is True
        view.vm.switch_to_realtime.assert_called_once()

    def test_on_page_size_change(self, mock_page):
        view = self._make_view(mock_page)
        view.page_size_dropdown.value = "100"
        view._on_page_size_change(None)
        view.vm.change_page_size.assert_called_once_with(100)

    def test_on_page_size_change_invalid(self, mock_page):
        view = self._make_view(mock_page)
        view.page_size_dropdown.value = "abc"
        view._on_page_size_change(None)
        view.vm.change_page_size.assert_not_called()

    def test_toggle_progress_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        view._toggle_progress(True)

    def test_toggle_progress_with_page(self, mock_page):
        view = self._make_view(mock_page)
        view._toggle_progress(True)
        mock_page.run_task.assert_called()

    def test_on_virtual_sort(self, mock_page):
        view = self._make_view(mock_page)
        view._on_virtual_sort("close", True)
        mock_page.run_task.assert_called()

    def test_did_mount_sets_flag(self, mock_page):
        view = self._make_view(mock_page)
        view.did_mount()
        assert view._mounted is True

    def test_did_mount_skips_if_already_mounted(self, mock_page):
        view = self._make_view(mock_page)
        view._mounted = True
        view.did_mount()
        mock_page.run_task.assert_not_called()

    def test_will_unmount_resets_flag(self, mock_page):
        view = self._make_view(mock_page)
        view._mounted = True
        view.will_unmount()
        assert view._mounted is False

    def test_will_unmount_disposes_vm(self, mock_page):
        view = self._make_view(mock_page)
        view.will_unmount()
        view.vm.dispose.assert_called_once()

    def test_collect_params_empty(self, mock_page):
        view = self._make_view(mock_page)
        params = view._collect_params()
        assert params == {}

    def test_collect_params_with_slider(self, mock_page):
        view = self._make_view(mock_page)
        slider = ft.Slider(min=0, max=100, value=50)
        slider.data = "test_param"
        view.params_container.controls.append(slider)
        params = view._collect_params()
        assert params["test_param"] == 50

    def test_collect_params_with_text_field(self, mock_page):
        view = self._make_view(mock_page)
        tf = ft.TextField(value="10.5")
        tf.data = "num_param"
        view.params_container.controls.append(tf)
        params = view._collect_params()
        assert params["num_param"] == 10.5

    def test_collect_params_with_dropdown(self, mock_page):
        view = self._make_view(mock_page)
        dd = ft.Dropdown(value="option_a")
        dd.data = "choice_param"
        view.params_container.controls.append(dd)
        params = view._collect_params()
        assert params["choice_param"] == "option_a"

    def test_on_tasks_updated_unlocks_ui(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.run_btn.disabled = True
        view.progress_ring.visible = True
        view._on_tasks_updated([])
        mock_page.run_task.assert_called()

    def test_on_tasks_updated_no_action_without_strategy(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = None
        view._on_tasks_updated([])
        mock_page.run_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_select_and_run_strategy_queues_when_not_loaded(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.options = []
        await view.select_and_run_strategy("momentum")
        assert view._pending_strategy_key == "momentum"

    @pytest.mark.asyncio
    async def test_select_and_run_strategy_validates_existence(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.options = [ft.dropdown.Option("other")]
        await view.select_and_run_strategy("nonexistent")
        assert view.selected_strategy is None

    @pytest.mark.asyncio
    async def test_select_and_run_strategy_selects_and_runs(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.options = [ft.dropdown.Option("momentum")]
        view.vm.get_strategy_desc.return_value = "desc"
        view.vm.run_strategy = MagicMock(return_value=_asyncio_dummy())
        view._render_strategy_params = MagicMock()
        view._collect_params = MagicMock(return_value={})
        await view.select_and_run_strategy("momentum")
        assert view.selected_strategy == "momentum"
        assert view.run_btn.disabled is False

    def test_update_theme(self, mock_page):
        view = self._make_view(mock_page)
        view.update_theme()
        assert view.strategy_dropdown.bgcolor == self.mock_ac.INPUT_BG
        assert view.status_text.color == self.mock_ac.TEXT_SECONDARY

    def test_render_table_with_none(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.get_current_page_data.return_value = None
        view._render_table()
        assert view._raw_row_lookup == {}

    def test_render_table_with_empty_df(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.get_current_page_data.return_value = pd.DataFrame()
        view._render_table()
        assert view._raw_row_lookup == {}

    def test_render_table_with_data(self, mock_page):
        view = self._make_view(mock_page)
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["test"]})
        view.vm.get_current_page_data.return_value = df
        with patch("ui.views.screener_view._build_table_data") as mock_build:
            mock_build.return_value = ([{"id": "ts_code", "label": "Code", "width": 100}], [{"ts_code": "000001.SZ"}])
            view._render_table()
        assert "000001.SZ" in view._raw_row_lookup

    def test_render_strategy_params_no_strategy(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = None
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            view._render_strategy_params()
        assert len(view.params_container.controls) == 0

    def test_render_strategy_params_no_params_def(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.get_strategy_params.return_value = None
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            view._render_strategy_params()
        assert len(view.params_container.controls) == 0

    def test_render_strategy_params_with_default_group(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.get_strategy_params.return_value = [
            {"name": "threshold", "type": "number", "default": 0.5, "group": "default", "label_key": "param_threshold"},
        ]
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            with patch("ui.theme.DEFAULT_GROUP_LABELS", {"default": "Basic", "advanced": "Advanced"}):
                view._render_strategy_params()
        assert len(view.params_container.controls) > 0

    def test_render_strategy_params_with_advanced_group(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.get_strategy_params.return_value = [
            {
                "name": "ai_system_prompt",
                "type": "textarea",
                "default": "prompt text",
                "group": "advanced",
                "label_key": "param_prompt",
            },
        ]
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            with patch("ui.theme.DEFAULT_GROUP_LABELS", {"default": "Basic", "advanced": "Advanced"}):
                view._render_strategy_params()
        assert len(view.params_container.controls) > 0

    def test_render_strategy_params_with_custom_group(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.get_strategy_params.return_value = [
            {
                "name": "custom_param",
                "type": "number",
                "default": 10,
                "group": "custom_group",
                "group_label_key": "custom_label",
            },
        ]
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            with patch("ui.theme.DEFAULT_GROUP_LABELS", {"default": "Basic", "advanced": "Advanced"}):
                view._render_strategy_params()
        assert len(view.params_container.controls) > 0

    def test_render_strategy_params_with_slider(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.get_strategy_params.return_value = [
            {
                "name": "score_weight",
                "type": "slider",
                "default": 50,
                "min": 0,
                "max": 100,
                "step": 1,
                "group": "default",
                "label_key": "param_weight",
            },
        ]
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            with patch("ui.theme.DEFAULT_GROUP_LABELS", {"default": "Basic", "advanced": "Advanced"}):
                view._render_strategy_params()
        assert len(view.params_container.controls) > 0

    def test_render_strategy_params_with_dropdown_param(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.get_strategy_params.return_value = [
            {
                "name": "mode",
                "type": "dropdown",
                "default": "fast",
                "options": ["fast", "slow"],
                "group": "default",
                "label_key": "param_mode",
            },
        ]
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            with patch("ui.theme.DEFAULT_GROUP_LABELS", {"default": "Basic", "advanced": "Advanced"}):
                view._render_strategy_params()
        assert len(view.params_container.controls) > 0

    def test_render_strategy_params_with_named_groups(self, mock_page):
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view.vm.get_strategy_params.return_value = [
            {
                "name": "signal_param",
                "type": "number",
                "default": 1,
                "group": "core_signal",
                "label_key": "param_signal",
            },
            {"name": "risk_param", "type": "number", "default": 2, "group": "risk_control", "label_key": "param_risk"},
        ]
        with patch(
            "ui.theme.PARAM_GROUP_ORDER",
            ["core_signal", "volume_confirm", "fundamental", "risk_control", "default", "advanced"],
        ):
            with patch(
                "ui.theme.DEFAULT_GROUP_LABELS",
                {"core_signal": "Signal", "risk_control": "Risk", "default": "Basic", "advanced": "Advanced"},
            ):
                view._render_strategy_params()
        assert len(view.params_container.controls) >= 2

    def test_resolve_group_title_with_label_key(self, mock_page):
        view = self._make_view(mock_page)
        view._resolve_group_title("some_group", "label_key_xyz")
        self.mock_i18n.get.assert_called_with("label_key_xyz")

    def test_resolve_group_title_with_default_label(self, mock_page):
        view = self._make_view(mock_page)
        with patch("ui.theme.DEFAULT_GROUP_LABELS", {"default": "Basic Settings"}):
            result = view._resolve_group_title("default", None)
        assert result == "Basic Settings"

    def test_resolve_group_title_fallback_to_name(self, mock_page):
        view = self._make_view(mock_page)
        with patch("ui.theme.DEFAULT_GROUP_LABELS", {}):
            result = view._resolve_group_title("unknown_group", None)
        assert result == "unknown_group"

    def test_on_row_click_creates_dialog(self, mock_page):
        view = self._make_view(mock_page)
        view._raw_row_lookup = {"000001.SZ": {"ts_code": "000001.SZ", "name": "test"}}
        view._on_row_click({"ts_code": "000001.SZ"})
        assert view.detail_dialog is not None

    def test_on_row_click_updates_existing_dialog(self, mock_page):
        view = self._make_view(mock_page)
        view._raw_row_lookup = {"000001.SZ": {"ts_code": "000001.SZ", "name": "test"}}
        view._on_row_click({"ts_code": "000001.SZ"})
        first_dialog = view.detail_dialog
        view._raw_row_lookup = {"000002.SZ": {"ts_code": "000002.SZ", "name": "test2"}}
        view._on_row_click({"ts_code": "000002.SZ"})
        assert view.detail_dialog is first_dialog
        view.detail_dialog.update_data.assert_called_once()

    def test_on_row_click_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        view._raw_row_lookup = {"000001.SZ": {"ts_code": "000001.SZ"}}
        view._on_row_click({"ts_code": "000001.SZ"})
        assert view.detail_dialog is None

    def test_on_row_click_falls_back_to_row_data(self, mock_page):
        view = self._make_view(mock_page)
        view._raw_row_lookup = {}
        view._on_row_click({"ts_code": "000001.SZ", "name": "fallback"})
        assert view.detail_dialog is not None

    def test_update_status_with_page(self, mock_page):
        view = self._make_view(mock_page)
        view._update_status("Running...", "blue")
        mock_page.run_task.assert_called()

    def test_update_status_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        view._update_status("Running...", "blue")

    def test_on_load_more_history(self, mock_page):
        view = self._make_view(mock_page)
        view._on_load_more_history(None)
        mock_page.run_task.assert_called()

    def test_on_load_more_history_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        view._on_load_more_history(None)

    @pytest.mark.asyncio
    async def test_load_history_for_date_with_run_id(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_data = MagicMock(return_value=_asyncio_dummy())
        await view._load_history_for_date("20240115", run_id="abc12345")
        view.vm.load_history_data.assert_called_once()
        assert view.progress_ring.visible is False

    @pytest.mark.asyncio
    async def test_load_history_for_date_with_strategy_name(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_data = MagicMock(return_value=_asyncio_dummy())
        with patch("ui.views.screener_view.translate_strategy_name", return_value="Momentum"):
            await view._load_history_for_date("20240115", strategy_name="momentum")
        view.vm.load_history_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_history_for_date_with_date_object(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_data = MagicMock(return_value=_asyncio_dummy())
        dt = datetime.date(2024, 1, 15)
        await view._load_history_for_date(dt)
        view.vm.load_history_data.assert_called_once_with("2024-01-15", None, None)

    def test_collect_params_with_text_field_multiline(self, mock_page):
        view = self._make_view(mock_page)
        tf = ft.TextField(value="some text", multiline=True)
        tf.data = "prompt_param"
        view.params_container.controls.append(tf)
        params = view._collect_params()
        assert params["prompt_param"] == "some text"

    def test_collect_params_with_text_field_invalid_number(self, mock_page):
        view = self._make_view(mock_page)
        tf = ft.TextField(value="not_a_number")
        tf.data = "bad_param"
        view.params_container.controls.append(tf)
        params = view._collect_params()
        assert params["bad_param"] == "not_a_number"

    def test_collect_params_skips_no_data(self, mock_page):
        view = self._make_view(mock_page)
        tf = ft.Text("just a label")
        view.params_container.controls.append(tf)
        params = view._collect_params()
        assert params == {}

    def test_collect_params_with_container_wrapper(self, mock_page):
        view = self._make_view(mock_page)
        slider = ft.Slider(min=0, max=100, value=75)
        slider.data = "wrapped_param"
        container = ft.Container(content=slider)
        view.params_container.controls.append(container)
        params = view._collect_params()
        assert params["wrapped_param"] == 75

    def test_collect_params_with_expansion_tile(self, mock_page):
        view = self._make_view(mock_page)
        tf = ft.TextField(value="42.0")
        tf.data = "advanced_param"
        tile = ft.ExpansionTile(title=ft.Text("test"), controls=[tf])
        view.params_container.controls.append(tile)
        params = view._collect_params()
        assert params["advanced_param"] == 42.0

    def test_on_strategy_change_calls_render_params(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = "momentum"
        view.vm.strategy_mgr.get_strategy.return_value = None
        view.vm.get_strategy_desc.return_value = "desc"
        view._render_strategy_params = MagicMock()
        e = MagicMock()
        view._on_strategy_change(e)
        view._render_strategy_params.assert_called_once()

    @pytest.mark.asyncio
    async def test_toggle_progress_inner_async(self, mock_page):
        view = self._make_view(mock_page)
        view._toggle_progress(True)
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.progress_ring.visible is True
        assert view.run_btn.disabled is True
        assert view.strategy_dropdown.disabled is True

    @pytest.mark.asyncio
    async def test_toggle_progress_inner_async_false(self, mock_page):
        view = self._make_view(mock_page)
        view._toggle_progress(False)
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.progress_ring.visible is False
        assert view.run_btn.disabled is False
        assert view.strategy_dropdown.disabled is False

    def test_on_save_file_result_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        e = MagicMock()
        e.path = "/tmp/test.csv"
        view._on_save_file_result(e)
        assert view.export_btn.disabled is True

    @pytest.mark.asyncio
    async def test_on_save_file_result_export_success(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.path = "/tmp/test.csv"
        view.vm.export_results = MagicMock(return_value=_asyncio_result(("/tmp/test.csv", None)))
        view._on_save_file_result(e)
        inner_fn = mock_page.run_task.call_args[0][0]
        filepath = mock_page.run_task.call_args[0][1]
        await inner_fn(filepath)
        assert view.export_btn.disabled is False

    @pytest.mark.asyncio
    async def test_on_save_file_result_export_fail(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.path = "/tmp/test.csv"
        view.vm.export_results = MagicMock(return_value=_asyncio_result((None, "disk full")))
        view._on_save_file_result(e)
        inner_fn = mock_page.run_task.call_args[0][0]
        filepath = mock_page.run_task.call_args[0][1]
        await inner_fn(filepath)
        assert view.export_btn.disabled is False

    @pytest.mark.asyncio
    async def test_on_save_file_result_export_exception(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.path = "/tmp/test.csv"
        view.vm.export_results = MagicMock(side_effect=RuntimeError("boom"))
        view._on_save_file_result(e)
        inner_fn = mock_page.run_task.call_args[0][0]
        filepath = mock_page.run_task.call_args[0][1]
        await inner_fn(filepath)
        assert view.export_btn.disabled is False

    @pytest.mark.asyncio
    async def test_update_ui_inner_async(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.page_no = 2
        view.vm.total_pages = 5
        view.vm.total_items = 100
        view._update_ui()
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.prev_btn.disabled is False
        assert view.next_btn.disabled is False
        assert view.export_btn.disabled is False

    @pytest.mark.asyncio
    async def test_update_ui_inner_async_first_page(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.page_no = 1
        view.vm.total_pages = 5
        view.vm.total_items = 100
        view._update_ui()
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.prev_btn.disabled is True
        assert view.next_btn.disabled is False

    @pytest.mark.asyncio
    async def test_update_ui_inner_async_last_page(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.page_no = 5
        view.vm.total_pages = 5
        view.vm.total_items = 100
        view._update_ui()
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.prev_btn.disabled is False
        assert view.next_btn.disabled is True

    @pytest.mark.asyncio
    async def test_update_ui_inner_async_no_data(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.page_no = 1
        view.vm.total_pages = 0
        view.vm.total_items = 0
        view._update_ui()
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.export_btn.disabled is True

    def test_update_ui_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        view._update_ui()

    @pytest.mark.asyncio
    async def test_append_log_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        view._append_log("test", 90, "thinking text")

    @pytest.mark.asyncio
    async def test_append_log_with_page(self, mock_page):
        view = self._make_view(mock_page)
        view._append_log("stock_a", 90, "x" * 100)
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert len(view.log_view.controls) == 1

    @pytest.mark.asyncio
    async def test_append_log_high_score_color(self, mock_page):
        view = self._make_view(mock_page)
        view._append_log("stock_a", 90, "x" * 100)
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.log_view.controls[0].color == self.mock_ac.ACCENT

    @pytest.mark.asyncio
    async def test_append_log_medium_score_color(self, mock_page):
        view = self._make_view(mock_page)
        view._append_log("stock_a", 60, "x" * 100)
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.log_view.controls[0].color == "#FFB86C"

    @pytest.mark.asyncio
    async def test_append_log_low_score_color(self, mock_page):
        view = self._make_view(mock_page)
        view._append_log("stock_a", 30, "x" * 100)
        inner_fn = mock_page.run_task.call_args[0][0]
        await inner_fn()
        assert view.log_view.controls[0].color == "#FF5555"

    @pytest.mark.asyncio
    async def test_append_log_trims_at_max(self, mock_page):
        view = self._make_view(mock_page)
        for i in range(12):
            view._append_log(f"stock_{i}", 80, "x" * 100)
            inner_fn = mock_page.run_task.call_args[0][0]
            await inner_fn()
        assert len(view.log_view.controls) <= 11

    def test_on_log_stream_start_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        result = view._on_log_stream_start("stock_a")
        assert result is None

    @pytest.mark.asyncio
    async def test_on_log_stream_start_returns_chunk_receiver(self, mock_page):
        view = self._make_view(mock_page)
        result = view._on_log_stream_start("stock_a")
        assert result is not None
        assert callable(result)
        assert hasattr(result, "final_flush")

    @pytest.mark.asyncio
    async def test_on_log_stream_start_chunk_and_flush(self, mock_page):
        view = self._make_view(mock_page)
        chunk_fn = view._on_log_stream_start("stock_a")
        add_fn = mock_page.run_task.call_args[0][0]
        await add_fn()
        chunk_fn("reasoning text", is_reasoning=True)
        chunk_fn("content text", is_reasoning=False)
        chunk_fn.final_flush()
        flush_fn = mock_page.run_task.call_args[0][0]
        await flush_fn()

    @pytest.mark.asyncio
    async def test_on_log_stream_start_throttled_chunk(self, mock_page):
        view = self._make_view(mock_page)
        chunk_fn = view._on_log_stream_start("stock_a")
        add_fn = mock_page.run_task.call_args[0][0]
        await add_fn()
        chunk_fn("reasoning", is_reasoning=True)
        chunk_fn("more reasoning", is_reasoning=True)

    @pytest.mark.asyncio
    async def test_on_log_stream_start_chunk_without_page(self, mock_page):
        view = self._make_view(mock_page)
        chunk_fn = view._on_log_stream_start("stock_a")
        add_fn = mock_page.run_task.call_args[0][0]
        await add_fn()
        view._Control__page = None
        chunk_fn("text", is_reasoning=False)

    @pytest.mark.asyncio
    async def test_load_history_tree_with_data(self, mock_page):
        view = self._make_view(mock_page)
        tree_data = {
            "20240115": [
                {"strategy_name": "momentum", "run_id": "abc123456789", "cnt": 5},
            ],
        }
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result(tree_data))
        await view._load_history_tree(append=False)
        assert len(view.history_tree_list.controls) > 0

    @pytest.mark.asyncio
    async def test_load_history_tree_with_date_obj_keys(self, mock_page):
        view = self._make_view(mock_page)
        tree_data = {
            datetime.date(2024, 1, 15): [
                {"strategy_name": "momentum", "run_id": "abc123456789", "cnt": 3},
            ],
        }
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result(tree_data))
        await view._load_history_tree(append=False)
        assert len(view.history_tree_list.controls) > 0

    @pytest.mark.asyncio
    async def test_load_history_tree_empty(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result({}))
        await view._load_history_tree(append=False)
        assert len(view.history_tree_list.controls) > 0
        assert view.history_load_more_btn.visible is False

    @pytest.mark.asyncio
    async def test_load_history_tree_exception(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_tree = MagicMock(side_effect=RuntimeError("db error"))
        await view._load_history_tree(append=False)

    @pytest.mark.asyncio
    async def test_load_history_tree_append_mode(self, mock_page):
        view = self._make_view(mock_page)
        tree_data = {
            "20240115": [
                {"strategy_name": "momentum", "run_id": "abc123456789", "cnt": 5},
            ],
        }
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result(tree_data))
        await view._load_history_tree(append=True)
        assert len(view.history_tree_list.controls) > 0

    @pytest.mark.asyncio
    async def test_load_history_tree_multiple_strategies(self, mock_page):
        view = self._make_view(mock_page)
        tree_data = {
            "20240115": [
                {"strategy_name": "momentum", "run_id": "abc123456789", "cnt": 5},
                {"strategy_name": "value", "run_id": "def987654321", "cnt": 3},
            ],
        }
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result(tree_data))
        await view._load_history_tree(append=False)
        assert len(view.history_tree_list.controls) > 0

    @pytest.mark.asyncio
    async def test_load_history_tree_show_load_more(self, mock_page):
        view = self._make_view(mock_page)
        tree_data = {}
        for i in range(5):
            tree_data[f"2024011{i}"] = [
                {"strategy_name": "momentum", "run_id": f"abc{i}123456789", "cnt": 5},
            ]
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result(tree_data))
        await view._load_history_tree(append=False)
        assert view.history_load_more_btn.visible is True

    @pytest.mark.asyncio
    async def test_load_history_tree_without_page(self, mock_page):
        view = self._make_view(mock_page)
        view._Control__page = None
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result({}))
        await view._load_history_tree(append=False)

    def test_on_tree_item_click_with_page(self, mock_page):
        view = self._make_view(mock_page)
        view._on_tree_item_click("20240115", run_id="abc12345")
        mock_page.run_task.assert_called()

    def test_on_tree_item_click_without_page(self):
        view = self._make_view(MagicMock())
        view._Control__page = None
        view._on_tree_item_click("20240115")

    @pytest.mark.asyncio
    async def test_load_history_for_date_no_run_id_no_strategy(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_data = MagicMock(return_value=_asyncio_dummy())
        with patch("ui.views.screener_view.translate_strategy_name", return_value="Momentum"):
            await view._load_history_for_date("20240115")
        view.vm.load_history_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_history_for_date_with_strategy_name_and_run_id(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.load_history_data = MagicMock(return_value=_asyncio_dummy())
        with patch("ui.views.screener_view.translate_strategy_name", return_value="Momentum"):
            await view._load_history_for_date("20240115", strategy_name="momentum")
        view.vm.load_history_data.assert_called_once()

    def test_collect_params_with_column_wrapper(self, mock_page):
        view = self._make_view(mock_page)
        slider = ft.Slider(min=0, max=100, value=30)
        slider.data = "col_param"
        col = ft.Column(controls=[slider])
        view.params_container.controls.append(col)
        params = view._collect_params()
        assert params["col_param"] == 30

    def test_collect_params_with_row_wrapper(self, mock_page):
        view = self._make_view(mock_page)
        tf = ft.TextField(value="99.5")
        tf.data = "row_param"
        row = ft.Row(controls=[tf])
        view.params_container.controls.append(row)
        params = view._collect_params()
        assert params["row_param"] == 99.5
