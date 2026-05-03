from unittest.mock import patch, MagicMock

from ui.viewmodels.screener_view_model import ScreenerViewModel, TASK_NAME_PREFIX


class TestScreenerViewModelConstants:
    def test_task_name_prefix(self):
        assert TASK_NAME_PREFIX == "strategy_screening"


class TestScreenerViewModelInit:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_initial_state(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm._full_results is None
        assert vm.page_no == 1
        assert vm.page_size == 50
        assert vm.total_pages == 0
        assert vm.total_items == 0
        assert vm.sort_column is None
        assert vm.sort_ascending is True

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_initial_mode_realtime(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.mode == "REALTIME"

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_ai_buffer_empty(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert len(vm._ai_buffer) == 0

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_callbacks_none(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.on_update is None
        assert vm.on_log is None
        assert vm.on_status is None
        assert vm.on_progress is None


class TestScreenerViewModelBind:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_bind_sets_callbacks(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        cb_update = MagicMock()
        cb_log = MagicMock()
        cb_status = MagicMock()
        cb_progress = MagicMock()
        vm.bind(cb_update, cb_log, cb_status, cb_progress)
        assert vm.on_update is cb_update
        assert vm.on_log is cb_log
        assert vm.on_status is cb_status
        assert vm.on_progress is cb_progress


class TestScreenerViewModelSortState:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_sort_column_default_none(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.sort_column is None

    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_sort_ascending_default_true(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.sort_ascending is True


class TestScreenerViewModelAiUpdateInterval:
    @patch("ui.viewmodels.screener_view_model.ReviewManager")
    @patch("ui.viewmodels.screener_view_model.StrategyManager")
    @patch("ui.viewmodels.screener_view_model.DataProcessor")
    def test_interval_value(self, mock_dp, mock_sm, mock_rm):
        vm = ScreenerViewModel()
        assert vm.AI_UPDATE_INTERVAL == 0.5
