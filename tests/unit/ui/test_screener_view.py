import asyncio
import contextlib
import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pandas as pd
import pytest


from ui.views.screener_view import (
    _COLUMN_WIDTHS,
    _build_table_data,
    _format_cell_value,
    ScreenerView,
)
from tests.unit.ui.conftest import wrap_mock_page

pytestmark = pytest.mark.unit


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

    def test_int_non_volume_returns_str(self):
        # 覆盖 96->98: int 值且非 volume 列时跳过 float 格式化，走 str(val)
        result = _format_cell_value("ai_score", 85)
        assert result == "85"


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
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_vm = MagicMock()
        self.mock_vm.strategy_mgr = MagicMock()
        self.mock_vm.strategy_mgr.get_all_with_dependencies.return_value = {}
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
            patch("ui.views.screener_view.UILogger"),
            patch("ui.views.screener_view.MetaDataManager"),
            patch("ui.views.screener_view.StockDetailDialog"),
            patch(
                "flet.controls.control.Control.update"
            ),  # no-op: Flet update() requires page binding; UI state tested via mock attributes
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            # Phase A.3: ResizableSplitter 是 @ft.component, 无 renderer 下会抛 RuntimeError;
            # mock 为 MagicMock 供 ScreenerView 命令式构造 (Phase F.3 声明式重写后移除)
            self.mock_splitter = stack.enter_context(patch("ui.views.screener_view.ResizableSplitter"))
            yield

    def _make_view(self, mock_page):
        view = ScreenerView(mock_page)
        view.page = wrap_mock_page(mock_page)
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

    def test_on_strategy_change_with_missing_apis(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = "northbound_flow"
        mock_strategy = MagicMock()
        mock_strategy.get_parameters.return_value = []
        mock_strategy.get_dynamic_description.return_value = "Northbound flow strategy"
        view.vm.strategy_mgr.get_strategy.return_value = mock_strategy
        view.vm.strategy_mgr.get_all_with_dependencies.return_value = {
            "northbound_flow": {
                "name": "北向资金流向",
                "missing_apis": ["moneyflow_hsgt", "hk_hold"],
            }
        }
        e = MagicMock()
        view._on_strategy_change(e)
        desc_value = view.strategy_desc_text.value or ""
        assert "moneyflow_hsgt" in desc_value
        assert "hk_hold" in desc_value

    def test_on_strategy_change_no_missing_apis(self, mock_page):
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = "momentum"
        mock_strategy = MagicMock()
        mock_strategy.get_parameters.return_value = []
        mock_strategy.get_dynamic_description.return_value = "Momentum strategy"
        view.vm.strategy_mgr.get_strategy.return_value = mock_strategy
        view.vm.strategy_mgr.get_all_with_dependencies.return_value = {
            "momentum": {
                "name": "动量策略",
                "missing_apis": [],
            }
        }
        e = MagicMock()
        view._on_strategy_change(e)
        desc_value = view.strategy_desc_text.value or ""
        assert desc_value == "Momentum strategy"
        assert "missing_apis" not in desc_value.lower()

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
        view.save_file_picker.save_file = AsyncMock(return_value=None)
        with patch("ui.views.screener_view.get_now") as mock_now:
            mock_now.return_value = datetime.datetime(2024, 1, 1, 12, 0, 0)
            await view._on_export_click(None)
        view.save_file_picker.save_file.assert_called_once()

    def test_on_mode_change_to_history(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.control.selected = ["HISTORY"]
        view._on_mode_change(e)
        mock_page.run_task.assert_called_once()

    def test_on_mode_change_to_realtime(self, mock_page):
        view = self._make_view(mock_page)
        e = MagicMock()
        e.control.selected = ["REALTIME"]
        view._on_mode_change(e)
        mock_page.run_task.assert_called_once()

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
        view._main_splitter.set_left_collapsed = MagicMock()
        await view._switch_to_history_mode()
        assert view.history_tree_container.visible is True
        assert view.realtime_controls.visible is False
        assert view.log_card.visible is False
        assert view.run_btn.visible is False
        view.vm.switch_to_history.assert_called_once()
        view._main_splitter.set_left_collapsed.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_switch_to_realtime_mode(self, mock_page):
        view = self._make_view(mock_page)
        view._main_splitter.set_left_collapsed = MagicMock()
        await view._switch_to_realtime_mode()
        assert view.realtime_controls.visible is True
        assert view.log_card.visible is True
        assert view.run_btn.visible is True
        view.vm.switch_to_realtime.assert_called_once()
        view._main_splitter.set_left_collapsed.assert_called_once_with(True)

    # --- §6.2 ResizableSplitter integration ---
    # Phase A.3: ResizableSplitter 是 @ft.component, 无 renderer 下被 mock;
    # 集成契约通过 mock 调用参数验证 (声明式组件实例由集成测试覆盖)

    def test_main_body_uses_resizable_splitter(self, mock_page):
        """§6.2: ScreenerView 构造期调用 ResizableSplitter。"""
        view = self._make_view(mock_page)
        assert hasattr(view, "_main_splitter")
        # mock_splitter 被调用 (ScreenerView 构造了 ResizableSplitter)
        assert self.mock_splitter.called, "ScreenerView 应调用 ResizableSplitter"

    def test_main_splitter_collapsible_true(self, mock_page):
        """§6.2: ResizableSplitter 构造参数 collapsible=True。"""
        self._make_view(mock_page)
        kwargs = self.mock_splitter.call_args.kwargs
        assert kwargs.get("collapsible") is True, "collapsible 应为 True"

    def test_main_splitter_on_resize_is_refresh_table_viewport(self, mock_page):
        """§6.2: on_resize 回调指向 _refresh_table_viewport。"""
        view = self._make_view(mock_page)
        kwargs = self.mock_splitter.call_args.kwargs
        assert kwargs.get("on_resize") == view._refresh_table_viewport, "on_resize 应指向 _refresh_table_viewport"

    def test_refresh_table_viewport_calls_result_table_refresh(self, mock_page):
        """§6.2: _refresh_table_viewport 触发 result_table.refresh_viewport。"""
        view = self._make_view(mock_page)
        view.result_table.refresh_viewport.reset_mock()
        view._refresh_table_viewport()
        view.result_table.refresh_viewport.assert_called_once()

    def test_no_direct_width_assignment_on_history_tree_container(self):
        """§6.2: history_tree_container.width 不再被直接设置（由 splitter 管理）。"""
        import inspect

        from ui.views import screener_view

        source = inspect.getsource(screener_view.ScreenerView)
        assert "history_tree_container.width = 250" not in source
        assert "history_tree_container.width = 0" not in source

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
        view._mock_page = None
        view._toggle_progress(True)

    def test_toggle_progress_with_page(self, mock_page):
        view = self._make_view(mock_page)
        view._toggle_progress(True)
        mock_page.run_task.assert_called_once()

    def test_on_virtual_sort(self, mock_page):
        view = self._make_view(mock_page)
        view._on_virtual_sort("close", True)
        mock_page.run_task.assert_called_once()

    def test_did_mount_sets_flag(self, mock_page):
        view = self._make_view(mock_page)
        view.did_mount()
        assert view._mounted is True

    def test_did_mount_subscribes_via_viewmodel(self, mock_page):
        view = self._make_view(mock_page)
        view.did_mount()
        view.vm.subscribe_task_manager.assert_called_once()

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

    def test_will_unmount_unsubscribes_via_viewmodel(self, mock_page):
        view = self._make_view(mock_page)
        view.will_unmount()
        view.vm.unsubscribe_task_manager.assert_called_once()

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

    def test_on_task_unlock_unlocks_ui(self, mock_page):
        view = self._make_view(mock_page)
        view.run_btn.disabled = True
        view.progress_ring.visible = True
        view._on_task_unlock()
        mock_page.run_task.assert_called_once()

    def test_on_task_unlock_no_action_without_page(self, mock_page):
        view = self._make_view(mock_page)
        view._mock_page = None  # type: ignore[attr-defined]
        view._on_task_unlock()
        # Should not raise and should not call run_task

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
        view._render_table_sync()
        assert view._raw_row_lookup == {}

    def test_render_table_with_empty_df(self, mock_page):
        view = self._make_view(mock_page)
        view.vm.get_current_page_data.return_value = pd.DataFrame()
        view._render_table_sync()
        assert view._raw_row_lookup == {}

    def test_render_table_with_data(self, mock_page):
        view = self._make_view(mock_page)
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["test"]})
        view.vm.get_current_page_data.return_value = df
        with patch("ui.views.screener_view._build_table_data") as mock_build:
            mock_build.return_value = (
                [{"id": "ts_code", "label": "Code", "width": 100}],
                [{"ts_code": "000001.SZ"}],
            )
            view._render_table_sync()
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
            {
                "name": "threshold",
                "type": "number",
                "default": 0.5,
                "group": "default",
                "label_key": "param_threshold",
            },
        ]
        with patch("ui.theme.PARAM_GROUP_ORDER", ["default", "advanced"]):
            with patch(
                "ui.theme.DEFAULT_GROUP_LABELS",
                {"default": "Basic", "advanced": "Advanced"},
            ):
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
            with patch(
                "ui.theme.DEFAULT_GROUP_LABELS",
                {"default": "Basic", "advanced": "Advanced"},
            ):
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
            with patch(
                "ui.theme.DEFAULT_GROUP_LABELS",
                {"default": "Basic", "advanced": "Advanced"},
            ):
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
            with patch(
                "ui.theme.DEFAULT_GROUP_LABELS",
                {"default": "Basic", "advanced": "Advanced"},
            ):
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
            with patch(
                "ui.theme.DEFAULT_GROUP_LABELS",
                {"default": "Basic", "advanced": "Advanced"},
            ):
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
            {
                "name": "risk_param",
                "type": "number",
                "default": 2,
                "group": "risk_control",
                "label_key": "param_risk",
            },
        ]
        with patch(
            "ui.theme.PARAM_GROUP_ORDER",
            [
                "core_signal",
                "volume_confirm",
                "fundamental",
                "risk_control",
                "default",
                "advanced",
            ],
        ):
            with patch(
                "ui.theme.DEFAULT_GROUP_LABELS",
                {
                    "core_signal": "Signal",
                    "risk_control": "Risk",
                    "default": "Basic",
                    "advanced": "Advanced",
                },
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

    def test_on_row_click_recreates_dialog_with_new_data(self, mock_page):
        """声明式范式：每次点击重新调用 StockDetailDialog（props 推送新数据），不复用实例。"""
        from ui.views import screener_view as sv

        view = self._make_view(mock_page)
        view._raw_row_lookup = {"000001.SZ": {"ts_code": "000001.SZ", "name": "test"}}
        view._on_row_click({"ts_code": "000001.SZ"})
        assert view.detail_dialog is not None
        assert sv.StockDetailDialog.call_count == 1

        # 第二次点击新股票：重新调用 StockDetailDialog（props 推送）
        view._raw_row_lookup = {"000002.SZ": {"ts_code": "000002.SZ", "name": "test2"}}
        view._on_row_click({"ts_code": "000002.SZ"})
        assert sv.StockDetailDialog.call_count == 2
        # 第二次调用应包含新 stock_data
        second_call = sv.StockDetailDialog.call_args
        assert second_call.kwargs["stock_data"]["ts_code"] == "000002.SZ"

    def test_on_row_click_without_page(self):
        view = self._make_view(MagicMock())
        view._mock_page = None
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
        mock_page.run_task.assert_called_once()

    def test_update_status_without_page(self):
        view = self._make_view(MagicMock())
        view._mock_page = None
        view._update_status("Running...", "blue")

    def test_on_load_more_history(self, mock_page):
        view = self._make_view(mock_page)
        view._on_load_more_history(None)
        mock_page.run_task.assert_called_once()

    def test_on_load_more_history_without_page(self):
        view = self._make_view(MagicMock())
        view._mock_page = None
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
        view._mock_page = None
        view._update_ui()

    @pytest.mark.asyncio
    async def test_append_log_without_page(self):
        view = self._make_view(MagicMock())
        view._mock_page = None
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
        view._mock_page = None
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
        chunk_fn("reasoning text", is_reasoning=True)  # type: ignore[optional-call]
        chunk_fn("content text", is_reasoning=False)  # type: ignore[optional-call]
        chunk_fn.final_flush()
        flush_fn = mock_page.run_task.call_args[0][0]
        await flush_fn()

    @pytest.mark.asyncio
    async def test_on_log_stream_start_throttled_chunk(self, mock_page):
        view = self._make_view(mock_page)
        chunk_fn = view._on_log_stream_start("stock_a")
        add_fn = mock_page.run_task.call_args[0][0]
        await add_fn()
        chunk_fn("reasoning", is_reasoning=True)  # type: ignore[optional-call]
        chunk_fn("more reasoning", is_reasoning=True)  # type: ignore[optional-call]

    @pytest.mark.asyncio
    async def test_on_log_stream_start_chunk_without_page(self, mock_page):
        view = self._make_view(mock_page)
        chunk_fn = view._on_log_stream_start("stock_a")
        add_fn = mock_page.run_task.call_args[0][0]
        await add_fn()
        view._mock_page = None
        chunk_fn("text", is_reasoning=False)  # type: ignore[optional-call]

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
        view._mock_page = None
        view.vm.load_history_tree = MagicMock(return_value=_asyncio_result({}))
        await view._load_history_tree(append=False)

    def test_on_tree_item_click_with_page(self, mock_page):
        view = self._make_view(mock_page)
        view._on_tree_item_click("20240115", run_id="abc12345")
        mock_page.run_task.assert_called_once()

    def test_on_tree_item_click_without_page(self):
        view = self._make_view(MagicMock())
        view._mock_page = None
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

    def test_refresh_locale_calls_invalidate_cache(self, mock_page):
        """§5.8 规范 8：refresh_locale 必须使 MetaDataManager 缓存失效，确保列别名用新 locale 重算"""
        import ui.views.screener_view as mod

        view = self._make_view(mock_page)
        # 清除 __init__ 期间的调用记录，仅观察 refresh_locale 的副作用
        mod.MetaDataManager.invalidate_cache.reset_mock()
        view.refresh_locale()
        mod.MetaDataManager.invalidate_cache.assert_called_once()

    def test_refresh_locale_does_not_call_invalidate_dependency_cache(self, mock_page):
        """§5.8 规范：refresh_locale 必须纯 UI，不应触发 invalidate_dependency_cache（M15 移除以避免 IO）"""
        view = self._make_view(mock_page)
        # 清除 __init__ 期间的调用记录，仅观察 refresh_locale 的副作用
        self.mock_vm.strategy_mgr.invalidate_dependency_cache.reset_mock()
        view.refresh_locale()
        self.mock_vm.strategy_mgr.invalidate_dependency_cache.assert_not_called()

    def test_load_strategies_uses_name_key_translation(self, mock_page):
        """_load_strategies 应通过 strategy_obj.name_key 重新翻译策略名称，不直接用缓存中的 info["name"]"""
        view = self._make_view(mock_page)

        # 构造 mock 策略数据
        mock_strategy = MagicMock()
        mock_strategy.name_key = "strategy_value_name"
        self.mock_vm.strategy_mgr.get_strategy.return_value = mock_strategy
        self.mock_vm.strategy_mgr.get_all_with_dependencies.return_value = {
            "value": {"name": "旧语言名称", "missing_apis": []},
        }

        # 运行 _load_strategies（需要 async）
        asyncio.run(view._load_strategies())

        # 验证 get_strategy 被调用（说明使用了 name_key 路径）
        self.mock_vm.strategy_mgr.get_strategy.assert_called_with("value")
        # 验证 I18n.get 被调用获取 name_key 的翻译
        self.mock_i18n.get.assert_any_call("strategy_value_name")

    def test_refresh_locale_preserves_dropdown_values(self, mock_page):
        """§5.8 规范 4：refresh_locale 重建 options 后 value 必须保留。"""
        view = self._make_view(mock_page)
        # 配置 strategy_mgr 返回有效数据，确保 strategy_dropdown.options 能重建
        mock_strategy = MagicMock()
        mock_strategy.name_key = "strategy_ma_cross_name"
        self.mock_vm.strategy_mgr.get_strategy.return_value = mock_strategy
        self.mock_vm.strategy_mgr.get_all_with_dependencies.return_value = {
            "ma_cross": {"name": "MA Cross", "missing_apis": []},
        }
        view.strategy_dropdown.value = "ma_cross"
        view.page_size_dropdown.value = "20"
        original_strategy = view.strategy_dropdown.value
        original_page_size = view.page_size_dropdown.value
        view.refresh_locale()
        assert view.strategy_dropdown.value == original_strategy
        assert view.page_size_dropdown.value == original_page_size
        assert view.strategy_dropdown.options is not None
        assert len(view.strategy_dropdown.options) > 0
        assert view.page_size_dropdown.options is not None
        assert len(view.page_size_dropdown.options) > 0

    def test_refresh_locale_preserves_value_when_strategy_rebuild_fails(self, mock_page):
        """§5.8 规范 4 异常路径：strategy_dropdown options 重建失败时 value 仍须恢复。

        生产代码 screener_view.py:371-387 内层 try/except 保护 options 重建，
        `value = saved_strategy` 在 except 之后（第 387 行），确保异常时 value 不丢失。
        page_size_dropdown 重建无 try 保护但 options 硬编码，value 同样保留。
        """
        view = self._make_view(mock_page)
        view.strategy_dropdown.value = "ma_cross"
        view.page_size_dropdown.value = "20"
        # 模拟 options 重建抛异常（命中内层 except）
        self.mock_vm.strategy_mgr.get_all_with_dependencies.side_effect = Exception("boom")
        view.refresh_locale()
        assert view.strategy_dropdown.value == "ma_cross"
        assert view.page_size_dropdown.value == "20"

    def test_refresh_locale_updates_ai_card_placeholder(self, mock_page):
        """§5.8 规范 5：refresh_locale 必须刷新 in-flight AI 占位卡的"分析中"文本。

        场景：异步分析期间用户切换语言，占位卡文本应跟随更新。
        """
        view = self._make_view(mock_page)
        content_md = ft.Markdown("旧语言文本")
        view._ai_cards["000001.SZ"] = {
            "card": MagicMock(),
            "content_md": content_md,
            "card_content": MagicMock(),
            "progress_ring": MagicMock(),
        }
        view.refresh_locale()
        assert content_md.value == "ai_card_analyzing"

    def test_refresh_locale_updates_multiple_ai_cards(self, mock_page):
        """§5.8 规范 5：多个并发 AI 占位卡时 refresh_locale 全部刷新。"""
        view = self._make_view(mock_page)
        md1 = ft.Markdown("旧1")
        md2 = ft.Markdown("旧2")
        view._ai_cards["000001.SZ"] = {
            "content_md": md1,
            "card": MagicMock(),
            "card_content": MagicMock(),
            "progress_ring": MagicMock(),
        }
        view._ai_cards["000002.SZ"] = {
            "content_md": md2,
            "card": MagicMock(),
            "card_content": MagicMock(),
            "progress_ring": MagicMock(),
        }
        view.refresh_locale()
        assert md1.value == "ai_card_analyzing"
        assert md2.value == "ai_card_analyzing"

    def test_refresh_locale_empty_ai_cards_safe(self, mock_page):
        """§5.8 规范 9：_ai_cards 为空时 refresh_locale 安全（无 in-flight 占位卡）。"""
        view = self._make_view(mock_page)
        assert view._ai_cards == {}
        # 不应抛异常
        view.refresh_locale()
        # 空字典场景下 refresh_locale 不产生副作用
        assert view._ai_cards == {}

    # --- handle_resize / refresh_locale branch coverage ---

    def test_handle_resize_calls_refresh_viewport(self, mock_page):
        """覆盖 333-335: handle_resize 调用 result_table.refresh_viewport。"""
        view = self._make_view(mock_page)
        view.result_table.refresh_viewport.reset_mock()
        view.handle_resize(1200, 800)
        view.result_table.refresh_viewport.assert_called_once()

    def test_handle_resize_no_result_table(self, mock_page):
        """覆盖 333-335 分支: 无 result_table 时安全跳过。"""
        view = self._make_view(mock_page)
        view.result_table = None
        view.handle_resize(1200, 800)  # should not raise

    def test_refresh_locale_strategy_dropdown_name_from_info_and_missing_apis(self, mock_page):
        """覆盖 386 (strategy_obj 无 name_key 走 info['name']) 与 388 (missing_apis 标记)。"""
        view = self._make_view(mock_page)
        view.selected_strategy = None
        self.mock_vm.strategy_mgr.get_all_with_dependencies.return_value = {
            "value": {"name": "Value", "missing_apis": ["api1"]},
        }
        self.mock_vm.strategy_mgr.get_strategy.return_value = None
        view.refresh_locale()
        opt = view.strategy_dropdown.options[0]
        assert opt.text and "Value" in opt.text
        assert opt.text and "⚠️" in opt.text

    def test_refresh_locale_selected_strategy_with_dynamic_desc(self, mock_page):
        """覆盖 397-400: selected_strategy 有 get_dynamic_description 路径。"""
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        mock_strategy = MagicMock()
        mock_strategy.get_parameters.return_value = []
        mock_strategy.get_dynamic_description.return_value = "Dynamic desc"
        self.mock_vm.strategy_mgr.get_strategy.return_value = mock_strategy
        view.refresh_locale()
        assert view.strategy_desc_text.value == "Dynamic desc"

    def test_refresh_locale_selected_strategy_without_dynamic_desc(self, mock_page):
        """覆盖 401-402: selected_strategy 但 strategy_obj 无 get_dynamic_description 走 get_strategy_desc。"""
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        self.mock_vm.strategy_mgr.get_strategy.return_value = None
        self.mock_vm.get_strategy_desc.return_value = "Fallback desc"
        view.refresh_locale()
        assert view.strategy_desc_text.value == "Fallback desc"

    def test_refresh_locale_no_title_text_attr(self, mock_page):
        """覆盖 370->374: 无 title_text 属性时跳过赋值。"""
        view = self._make_view(mock_page)
        del view.title_text
        view.refresh_locale()  # should not raise

    def test_refresh_locale_segments_not_ft_text(self, mock_page):
        """覆盖 429->431, 431->435 (segments label 非 ft.Text) 与 438->442, 442->446 (无标题属性)。"""
        view = self._make_view(mock_page)
        view.mode_toggle.segments = [MagicMock(label=ft.Container()), MagicMock(label=ft.Container())]
        del view.history_tree_title_text
        del view.log_title_text
        view.refresh_locale()  # should not raise

    def test_refresh_locale_segments_fewer_than_two(self, mock_page):
        """覆盖 428->435: segments 少于 2 时跳过 segment 标签更新。"""
        view = self._make_view(mock_page)
        view.mode_toggle.segments = [MagicMock(label=ft.Text("x"))]
        view.refresh_locale()  # should not raise

    def test_refresh_locale_table_rebuild_exception(self, mock_page, caplog):
        """覆盖 454-462: 表格重建抛异常时记 DEBUG 并跳过。"""
        view = self._make_view(mock_page)
        view.vm.get_current_page_data.return_value = pd.DataFrame({"ts_code": ["000001.SZ"]})
        with caplog.at_level(logging.DEBUG, logger="ui.views.screener_view"):
            with patch("ui.views.screener_view._build_table_data", side_effect=RuntimeError("boom")):
                view.refresh_locale()
        assert any("table rebuild skipped" in r.message for r in caplog.records)

    def test_refresh_locale_calls_render_strategy_params(self, mock_page):
        """覆盖 466: selected_strategy 非空时调用 _render_strategy_params。"""
        view = self._make_view(mock_page)
        view.selected_strategy = "momentum"
        view._render_strategy_params = MagicMock()
        view.refresh_locale()
        view._render_strategy_params.assert_called_once()

    def test_refresh_locale_no_page(self, mock_page):
        """覆盖 468->exit: page 为 None 时跳过 self.update。"""
        view = self._make_view(mock_page)
        view._mock_page = None  # type: ignore[attr-defined]
        view.refresh_locale()  # should not raise

    def test_refresh_locale_outer_exception(self, mock_page, caplog):
        """覆盖 470-471: refresh_locale 外层异常记 WARNING。"""
        view = self._make_view(mock_page)
        self.mock_i18n.get.side_effect = RuntimeError("i18n boom")
        with caplog.at_level(logging.WARNING, logger="ui.views.screener_view"):
            view.refresh_locale()
        assert any("refresh_locale error" in r.message for r in caplog.records)

    # --- _do_restore_default_async / _do_save_prompt_async coverage ---

    @pytest.mark.asyncio
    async def test_do_restore_default_async_success(self, mock_page):
        """覆盖 1317-1335: 恢复默认 prompt 成功路径。"""
        view = self._make_view(mock_page)
        ctrl_field = ft.TextField(value="old")
        with (
            patch("utils.config_handler.ConfigHandler.set_strategy_prompt") as mock_set,
            patch("strategies.strategy_prompts.get_base_prompt", return_value="new prompt"),
            patch("ui.views.screener_view.ThreadPoolManager") as mock_tpm,
        ):
            mock_tpm.return_value.run_async = MagicMock(side_effect=_tpm_passthrough)
            await view._do_restore_default_async("momentum", ctrl_field)
        assert ctrl_field.value == "new prompt"
        mock_set.assert_called_once_with("momentum", None)
        mock_page.show_toast.assert_called_once()
        assert mock_page.show_toast.call_args[0][1] == "info"

    @pytest.mark.asyncio
    async def test_do_restore_default_async_exception(self, mock_page):
        """覆盖 1332-1335: 恢复默认 prompt 异常路径。"""
        view = self._make_view(mock_page)
        ctrl_field = ft.TextField(value="old")
        with (
            patch("utils.config_handler.ConfigHandler.set_strategy_prompt", side_effect=RuntimeError("io")),
            patch("ui.views.screener_view.ThreadPoolManager") as mock_tpm,
        ):
            mock_tpm.return_value.run_async = MagicMock(side_effect=_tpm_passthrough)
            await view._do_restore_default_async("momentum", ctrl_field)
        mock_page.show_toast.assert_called_once()
        assert mock_page.show_toast.call_args[0][1] == "error"

    @pytest.mark.asyncio
    async def test_do_save_prompt_async_invalid_length(self, mock_page):
        """覆盖 1338-1353: prompt 校验失败 (prompt_err_length)。"""
        view = self._make_view(mock_page)
        ctrl_field = ft.TextField(value="x" * 200)
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(False, "prompt_err_length")),
            patch("utils.prompt_guard.MAX_PROMPT_LENGTH", 100),
            patch("utils.config_handler.ConfigHandler.set_strategy_prompt") as mock_set,
        ):
            await view._do_save_prompt_async("momentum", ctrl_field)
        mock_set.assert_not_called()
        mock_page.show_toast.assert_called_once()
        assert mock_page.show_toast.call_args[0][1] == "warning"

    @pytest.mark.asyncio
    async def test_do_save_prompt_async_invalid_other_key(self, mock_page):
        """覆盖 1346-1347: prompt 校验失败 (其他 key)。"""
        view = self._make_view(mock_page)
        ctrl_field = ft.TextField(value="bad")
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(False, "prompt_err_xxx")),
            patch("utils.config_handler.ConfigHandler.set_strategy_prompt") as mock_set,
        ):
            await view._do_save_prompt_async("momentum", ctrl_field)
        mock_set.assert_not_called()
        msg = mock_page.show_toast.call_args[0][0]
        assert "prompt_err_xxx" in msg

    @pytest.mark.asyncio
    async def test_do_save_prompt_async_success(self, mock_page):
        """覆盖 1355-1365: 保存 prompt 成功路径。"""
        view = self._make_view(mock_page)
        ctrl_field = ft.TextField(value="my prompt")
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("ui.views.screener_view.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler.set_strategy_prompt") as mock_set,
        ):
            mock_tpm.return_value.run_async = MagicMock(side_effect=_tpm_passthrough)
            await view._do_save_prompt_async("momentum", ctrl_field)
        mock_set.assert_called_once_with("momentum", "my prompt")
        mock_page.show_toast.assert_called_once()
        assert mock_page.show_toast.call_args[0][1] == "success"

    @pytest.mark.asyncio
    async def test_do_save_prompt_async_exception(self, mock_page):
        """覆盖 1366-1369: 保存 prompt 异常路径。"""
        view = self._make_view(mock_page)
        ctrl_field = ft.TextField(value="my prompt")
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("ui.views.screener_view.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler.set_strategy_prompt", side_effect=RuntimeError("io")),
        ):
            mock_tpm.return_value.run_async = MagicMock(side_effect=_tpm_passthrough)
            await view._do_save_prompt_async("momentum", ctrl_field)
        mock_page.show_toast.assert_called_once()
        assert mock_page.show_toast.call_args[0][1] == "error"

    # --- _render_table_async coverage ---

    @pytest.mark.asyncio
    async def test_render_table_async_df_none(self, mock_page):
        """覆盖 1510-1522: df 为 None 时清理表格。"""
        view = self._make_view(mock_page)
        view.vm.get_current_page_data.return_value = None
        await view._render_table_async()
        view.result_table.set_columns.assert_called_once_with([])
        view.result_table.set_rows.assert_called_once()
        assert view._raw_row_lookup == {}

    @pytest.mark.asyncio
    async def test_render_table_async_with_data(self, mock_page):
        """覆盖 1523-1538: df 非空时正常构建。"""
        view = self._make_view(mock_page)
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["test"]})
        view.vm.get_current_page_data.return_value = df
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm.return_value.run_async = MagicMock(side_effect=_tpm_passthrough)
            await view._render_table_async()
        view.result_table.set_columns.assert_called_once()
        assert "000001.SZ" in view._raw_row_lookup

    # --- _on_export_click / _on_row_click branch coverage ---

    @pytest.mark.asyncio
    async def test_on_export_click_no_show_toast(self, mock_page):
        """覆盖 1394->1396: df 为 None 且 page 无 show_toast 时安全跳过。"""
        view = self._make_view(mock_page)
        no_toast = _NoToastPage()
        view.page = no_toast
        view.vm.get_export_data.return_value = None
        await view._on_export_click(None)  # should not raise

    def test_on_row_click_empty_ts_code(self, mock_page):
        """覆盖 1476->exit: ts_code 为空时不调 run_task。"""
        view = self._make_view(mock_page)
        view._raw_row_lookup = {}
        view._on_row_click({"name": "test"})
        mock_page.run_task.assert_not_called()

    def test_collect_params_unknown_control_type_with_data(self, mock_page):
        """覆盖 1310->1281: 控件有 data 但非 Slider/TextField/Dropdown 时跳过。"""
        view = self._make_view(mock_page)
        t = ft.Text("label")
        t.data = "unknown_param"
        view.params_container.controls.append(t)
        params = view._collect_params()
        assert params == {}


# --- Helpers for branch coverage ---


def _tpm_passthrough(task_type, func, *args, **kwargs):
    """ThreadPoolManager.run_async passthrough: 同步调用 func，结果包装为 Future。"""
    return _asyncio_result(func(*args, **kwargs))


class _NoToastPage:
    """无 show_toast 的 page 桩，用于测试 hasattr(self.page, 'show_toast') False 分支。"""

    def __init__(self):
        self.overlay = []
        self.run_task = MagicMock(return_value=MagicMock())

    def update(self, *args, **kwargs):
        pass


class TestStrategyTierHint:
    """Phase 2A.1 Task 2A.1.13：screener_view 策略档位不足提示测试。

    覆盖 `_update_tier_hint_text` 三态行为：
    - 当前档位 < 策略所需档位 → 显示 ``sys_strategy_tier_hint`` 本地化提示
    - 当前档位 ≥ 策略所需档位 → 隐藏提示
    - locale 变更后提示文案重新读取（§5.8 规范：纯 UI 操作）
    """

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_vm = MagicMock()
        self.mock_vm.strategy_mgr = MagicMock()
        self.mock_vm.strategy_mgr.get_all_with_dependencies.return_value = {}
        self.mock_vm.dispose = MagicMock()
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
            patch("ui.views.screener_view.UILogger"),
            patch("ui.views.screener_view.MetaDataManager"),
            patch("ui.views.screener_view.StockDetailDialog"),
            # no-op: Flet update() requires page binding; UI state tested via mock attributes
            patch("flet.controls.control.Control.update"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            # Phase A.3: ResizableSplitter 是 @ft.component, 无 renderer 下会抛 RuntimeError
            stack.enter_context(patch("ui.views.screener_view.ResizableSplitter"))
            yield

    def _make_view(self, mock_page):
        view = ScreenerView(mock_page)
        view.page = wrap_mock_page(mock_page)
        return view

    def _patch_tier_deps(self, *, current_tier: str, min_tier: str):
        """Patch ``_update_tier_hint_text`` 延迟导入的三个模块级符号。

        ``_update_tier_hint_text`` 内部通过函数体 ``from ... import`` 延迟导入，
        因此 patch 必须针对真实模块路径（不是 ``ui.views.screener_view`` 别名）。
        """
        from data.external.tushare_client import TushareClient as _RealClient

        tier_order = _RealClient._TIER_ORDER  # ClassVar，直接读取避免实例化

        mock_client = MagicMock(spec=_RealClient)
        mock_client.get_tier_order.side_effect = lambda tier: tier_order.get(tier, 0)

        return (
            patch("data.external.tushare_client.TushareClient", return_value=mock_client),
            patch("services.ai_service.get_strategy_min_tier", return_value=min_tier),
            patch(
                "utils.config_handler.ConfigHandler.get_tushare_point_tier",
                return_value=current_tier,
            ),
        )

    def test_strategy_tier_hint_shown_when_insufficient(self, mock_page):
        """当前档位低于策略所需档位 → tier_hint_text 可见且 value 为本地化文案。"""
        view = self._make_view(mock_page)
        view.selected_strategy = "value"
        with contextlib.ExitStack() as stack:
            for p in self._patch_tier_deps(current_tier="points_120", min_tier="points_5000"):
                stack.enter_context(p)
            view._update_tier_hint_text()
        assert view.tier_hint_text.visible is True
        assert view.tier_hint_text.value == "sys_strategy_tier_hint"

    def test_strategy_tier_hint_hidden_when_sufficient(self, mock_page):
        """当前档位 ≥ 策略所需档位 → tier_hint_text 不可见。"""
        view = self._make_view(mock_page)
        view.selected_strategy = "value"
        with contextlib.ExitStack() as stack:
            for p in self._patch_tier_deps(current_tier="points_5000", min_tier="points_120"):
                stack.enter_context(p)
            view._update_tier_hint_text()
        assert view.tier_hint_text.visible is False

    def test_strategy_tier_hint_locale_change(self, mock_page):
        """§5.8 规范：locale 变更后 ``_update_tier_hint_text`` 重新读取本地化文案。

        场景：档位不足时提示文案随 locale 切换动态更新。
        """
        view = self._make_view(mock_page)
        view.selected_strategy = "value"

        # 第一次调用：模拟 zh_CN locale，提示文案为中文
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: (
            "当前档位不足以支持该策略的 AI 分析" if key == "sys_strategy_tier_hint" else key
        )
        with contextlib.ExitStack() as stack:
            for p in self._patch_tier_deps(current_tier="points_120", min_tier="points_5000"):
                stack.enter_context(p)
            view._update_tier_hint_text()
        assert view.tier_hint_text.visible is True
        assert view.tier_hint_text.value == "当前档位不足以支持该策略的 AI 分析"

        # 第二次调用：模拟 locale 切换到 en_US，提示文案应重新读取
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: (
            "Current tier insufficient for AI analysis" if key == "sys_strategy_tier_hint" else key
        )
        with contextlib.ExitStack() as stack:
            for p in self._patch_tier_deps(current_tier="points_120", min_tier="points_5000"):
                stack.enter_context(p)
            view._update_tier_hint_text()
        assert view.tier_hint_text.visible is True
        assert view.tier_hint_text.value == "Current tier insufficient for AI analysis"
