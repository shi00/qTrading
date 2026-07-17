import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from ui.viewmodels.home_view_model import HomeViewModel
from ui.viewmodels.screener_view_model import ScreenerViewModel

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_processor():
    with patch("ui.viewmodels.home_view_model.DataProcessor") as cls:
        instance = MagicMock()
        instance.init_data = AsyncMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_news_service():
    with patch("ui.viewmodels.home_view_model.NewsSubscriptionService") as cls:
        instance = MagicMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_market_service():
    with patch("ui.viewmodels.home_view_model.MarketDataService") as cls:
        instance = MagicMock()
        instance.get_cached_data.return_value = None
        cls.return_value = instance
        yield instance


@pytest.fixture
def home_vm(mock_processor, mock_news_service, mock_market_service):
    return HomeViewModel()


class TestHomeViewModelInit:
    def test_init_registers_listeners(self, home_vm, mock_news_service, mock_market_service):
        home_vm.init()
        mock_news_service.add_listener.assert_called_once_with(home_vm._on_news_service_update)
        mock_market_service.add_listener.assert_called_once_with(home_vm._on_market_service_update)


class TestHomeViewModelDispose:
    def test_dispose_removes_listeners(self, home_vm, mock_news_service, mock_market_service):
        home_vm.dispose()
        mock_news_service.remove_listener.assert_called_once_with(home_vm._on_news_service_update)
        mock_market_service.remove_listener.assert_called_once_with(home_vm._on_market_service_update)

    def test_dispose_handles_exceptions(self, home_vm, mock_news_service):
        mock_news_service.remove_listener.side_effect = RuntimeError("boom")
        home_vm.dispose()
        mock_news_service.remove_listener.assert_called_once()


class TestHomeViewModelServiceHandlers:
    async def test_on_news_service_update_new_item(self, home_vm):
        from services.news_subscription_service import NewsUpdateType
        from ui.viewmodels.home_view_model import NewsRow

        snapshots: list = []
        home_vm.subscribe(lambda s: snapshots.append(s))

        with patch("ui.viewmodels.home_view_model._news_item_to_row") as mock_to_row:
            mock_to_row.return_value = NewsRow(content="test")
            await home_vm._on_news_service_update(NewsUpdateType.NEW_ITEM, [{"content": "test"}])

        assert len(home_vm.state.news_rows) == 1
        assert any(len(s.news_rows) == 1 for s in snapshots)

    async def test_on_news_service_update_without_subscribers(self, home_vm):
        from services.news_subscription_service import NewsUpdateType
        from ui.viewmodels.home_view_model import NewsRow

        with patch("ui.viewmodels.home_view_model._news_item_to_row") as mock_to_row:
            mock_to_row.return_value = NewsRow(content="test")
            await home_vm._on_news_service_update(NewsUpdateType.NEW_ITEM, [{"content": "test"}])

        assert len(home_vm.state.news_rows) == 1

    def test_on_market_service_update_emits_state(self, home_vm, mock_market_service):
        mock_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [{"name": "AI", "change": "+5%", "color": "red"}],
            "date": "2024-03-21",
            "stale": False,
        }
        mock_market_service.get_cached_data.return_value = mock_data

        snapshots: list = []
        home_vm.subscribe(lambda s: snapshots.append(s))
        home_vm._on_market_service_update()

        assert len(home_vm.state.market_indices) == 1
        assert any(len(s.market_indices) == 1 for s in snapshots)

    def test_on_market_service_update_without_subscribers(self, home_vm, mock_market_service):
        mock_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [],
            "date": "2024-03-21",
            "stale": False,
        }
        mock_market_service.get_cached_data.return_value = mock_data

        home_vm._on_market_service_update()

        assert len(home_vm.state.market_indices) == 1


class TestHomeViewModelInitData:
    async def test_init_data_calls_processor(self, home_vm, mock_processor):
        await home_vm.init_data()
        mock_processor.init_data.assert_awaited_once()


class TestHomeViewModelLoadMarketData:
    async def test_returns_cached_data_immediately(self, home_vm, mock_market_service):
        fake_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [],
            "date": "2024-03-21",
            "stale": False,
        }
        mock_market_service.get_cached_data.return_value = fake_data
        result = await home_vm.load_market_data()
        assert result is fake_data
        assert len(home_vm.state.market_indices) == 1

    async def test_retries_until_data(self, home_vm, mock_market_service):
        fake_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [],
            "date": "2024-03-21",
            "stale": False,
        }
        mock_market_service.get_cached_data.side_effect = [None, None, fake_data]
        with patch("ui.viewmodels.home_view_model.asyncio.sleep", new_callable=AsyncMock):
            result = await home_vm.load_market_data()
        assert result is fake_data

    async def test_returns_none_when_no_data(self, home_vm, mock_market_service):
        mock_market_service.get_cached_data.return_value = None
        with patch("ui.viewmodels.home_view_model.asyncio.sleep", new_callable=AsyncMock):
            result = await home_vm.load_market_data()
        assert result is None


class TestHomeViewModelGetCachedMarketData:
    async def test_returns_data(self, home_vm, mock_market_service):
        fake_data = {
            "indices": [{"name": "SH", "value": "3000", "change": "+10", "color": "RED"}],
            "hsgt": {"value": "100亿", "color": "RED", "sub": "净流入"},
            "hot_concepts": [],
            "date": "2024-03-21",
            "stale": False,
        }
        mock_market_service.get_cached_data.return_value = fake_data
        result = await home_vm.get_cached_market_data()
        assert result is fake_data
        assert len(home_vm.state.market_indices) == 1

    async def test_returns_none(self, home_vm, mock_market_service):
        mock_market_service.get_cached_data.return_value = None
        result = await home_vm.get_cached_market_data()
        assert result is None


class TestHomeViewModelRefreshNews:
    async def test_resets_page_and_increments_generation(self, home_vm, mock_processor):
        """refresh_news 递增 generation; 空数据时重置 news_page=0."""
        home_vm._load_generation = 5
        home_vm._set_state(news_page=3)
        with patch.object(home_vm, "_fetch_news_batch", new_callable=AsyncMock, return_value=pd.DataFrame()):
            await home_vm.refresh_news()
        assert home_vm.state.news_page == 0
        assert home_vm._load_generation == 6

    async def test_preserves_state_when_fetch_fails(self, home_vm, mock_processor):
        """batch 为 None (获取失败) 时保留当前 state, 不丢失已有数据."""
        home_vm._load_generation = 5
        home_vm._set_state(news_page=3)
        with patch.object(home_vm, "_fetch_news_batch", new_callable=AsyncMock, return_value=None):
            await home_vm.refresh_news()
        assert home_vm.state.news_page == 3
        assert home_vm._load_generation == 6


class TestHomeViewModelLoadNextPage:
    async def test_returns_none_when_loading(self, home_vm):
        home_vm._set_state(is_loading_more=True)
        result, has_more = await home_vm.load_next_page()
        assert result is None

    async def test_returns_none_when_no_more(self, home_vm):
        home_vm._set_state(is_loading_more=False, has_more_news=False)
        result, has_more = await home_vm.load_next_page()
        assert result is None

    async def test_aborts_on_generation_change(self, home_vm, mock_processor):
        home_vm._set_state(is_loading_more=False, has_more_news=True)
        home_vm._load_generation = 1

        async def _bump_gen(page):
            home_vm._load_generation = 99

        with patch.object(home_vm, "_fetch_news_batch", side_effect=_bump_gen):
            result, has_more = await home_vm.load_next_page()
        assert result is None
        assert has_more is False

    async def test_succeeds_with_data(self, home_vm, mock_processor):
        from ui.viewmodels.home_view_model import NewsRow

        home_vm._set_state(
            is_loading_more=False,
            has_more_news=True,
            news_rows=(NewsRow(content="old"),),
        )
        home_vm.PAGE_SIZE = 20
        batch = pd.DataFrame({"content": [f"news_{i}" for i in range(20)]})
        with patch.object(home_vm, "_fetch_news_batch", new_callable=AsyncMock, return_value=batch):
            result, has_more = await home_vm.load_next_page()
        assert result is batch
        assert has_more is True
        assert home_vm.state.news_page == 1
        assert home_vm.state.is_loading_more is False
        assert len(home_vm.state.news_rows) == 21


class TestHomeViewModelClearState:
    def test_clear_state_resets_all(self, home_vm):
        from ui.viewmodels.home_view_model import MarketIndexRow, NewsRow

        home_vm._set_state(
            has_more_news=True,
            news_page=5,
            news_rows=(NewsRow(content="test"),),
            market_indices=(MarketIndexRow(value="3000"),),
        )
        home_vm.clear_state()
        assert home_vm.state.has_more_news is False
        assert home_vm.state.news_page == 0
        assert home_vm.state.news_rows == ()
        assert home_vm.state.market_indices == ()


# ============================================================================
# ScreenerViewModel tests (state-based, §3.0.1 paradigm)
# ============================================================================


@pytest.fixture
def mock_dp():
    with patch("ui.viewmodels.screener_view_model.DataProcessor") as cls:
        instance = MagicMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_sm():
    with patch("ui.viewmodels.screener_view_model.StrategyManager") as cls:
        instance = MagicMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_rm():
    with patch("ui.viewmodels.screener_view_model.ReviewManager") as cls:
        instance = MagicMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_tm():
    with patch("ui.viewmodels.screener_view_model.TaskManager") as cls:
        instance = MagicMock()
        instance.submit_task.return_value = "task-1"
        cls.return_value = instance
        yield instance


@pytest.fixture
def screener_vm(mock_dp, mock_sm, mock_rm, mock_tm):
    return ScreenerViewModel()


class TestScreenerViewModelDispose:
    def test_dispose_clears_state_and_internal_refs(self, screener_vm):
        screener_vm._main_loop = MagicMock()
        screener_vm._full_results = pd.DataFrame({"a": [1]})
        screener_vm._ai_buffer = [{"x": 1}]
        screener_vm._realtime_snapshot = {"full_results": pd.DataFrame()}
        screener_vm.dispose()
        assert screener_vm._main_loop is None
        assert screener_vm._full_results is None
        assert screener_vm._ai_buffer == []
        assert screener_vm._realtime_snapshot is None
        # State reset to defaults
        assert screener_vm.state.loading is False
        assert screener_vm.state.mode == "REALTIME"
        assert screener_vm.state.logs == ()


class TestScreenerViewModelSortData:
    async def test_toggles_ascending(self, screener_vm):
        screener_vm._full_results = pd.DataFrame({"a": [3, 1, 2]})
        screener_vm._set_state(sort_column="a", sort_ascending=True)
        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as tp_cls:
            tp_instance = MagicMock()
            tp_instance.run_async = AsyncMock(return_value=pd.DataFrame({"a": [3, 2, 1]}))
            tp_cls.return_value = tp_instance
            await screener_vm.sort_data("a")
        assert screener_vm.state.sort_ascending is False

    async def test_noop_on_empty(self, screener_vm):
        screener_vm._full_results = None
        snapshots: list = []
        screener_vm.subscribe(lambda s: snapshots.append(s))
        await screener_vm.sort_data("a")
        # No state changes (noop before setting loading)
        assert len(snapshots) == 0

    async def test_sorts_data(self, screener_vm):
        screener_vm._full_results = pd.DataFrame({"a": [3, 1, 2]})
        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as tp_cls:
            tp_instance = MagicMock()
            sorted_df = pd.DataFrame({"a": [1, 2, 3]})
            tp_instance.run_async = AsyncMock(return_value=sorted_df)
            tp_cls.return_value = tp_instance
            await screener_vm.sort_data("a", ascending=True)
        assert screener_vm.state.sort_ascending is True
        assert screener_vm.state.page_no == 1


class TestScreenerViewModelSortHelper:
    def test_ascending(self):
        df = pd.DataFrame({"a": [3, 1, 2]})
        result = ScreenerViewModel._sort_helper(df, "a", True)
        assert list(result["a"]) == [1, 2, 3]

    def test_missing_column_returns_original(self):
        df = pd.DataFrame({"a": [3, 1, 2]})
        result = ScreenerViewModel._sort_helper(df, "z", True)
        assert list(result["a"]) == [3, 1, 2]


class TestScreenerViewModelChangePage:
    def test_valid_page(self, screener_vm):
        screener_vm._set_state(page_no=1, total_pages=3)
        screener_vm.change_page(1)
        assert screener_vm.state.page_no == 2

    def test_out_of_range(self, screener_vm):
        screener_vm._set_state(page_no=3, total_pages=3)
        screener_vm.change_page(1)
        assert screener_vm.state.page_no == 3


class TestScreenerViewModelChangePageSize:
    def test_updates_size(self, screener_vm):
        screener_vm._full_results = pd.DataFrame({"a": range(100)})
        screener_vm._update_pagination()
        screener_vm.change_page_size(25)
        assert screener_vm.state.page_size == 25
        assert screener_vm.state.page_no == 1

    def test_same_size_noop(self, screener_vm):
        screener_vm._set_state(page_size=50)
        snapshots: list = []
        screener_vm.subscribe(lambda s: snapshots.append(s))
        screener_vm.change_page_size(50)
        assert screener_vm.state.page_size == 50
        assert len(snapshots) == 0


class TestScreenerViewModelGetCurrentPageData:
    def test_returns_correct_slice(self, screener_vm):
        screener_vm._full_results = pd.DataFrame({"a": range(10)})
        screener_vm._set_state(page_no=2, page_size=3)
        result = screener_vm.get_current_page_data()
        assert list(result["a"]) == [3, 4, 5]

    def test_empty_when_none(self, screener_vm):
        screener_vm._full_results = None
        result = screener_vm.get_current_page_data()
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestScreenerViewModelSwitchToHistory:
    def test_snapshots_state(self, screener_vm):
        df = pd.DataFrame({"a": [1]})
        screener_vm._full_results = df
        screener_vm._set_state(page_no=3, sort_column="a", sort_ascending=False)
        screener_vm._ai_buffer = [{"x": 1}]
        screener_vm.switch_to_history()
        assert screener_vm.state.mode == "HISTORY"
        assert screener_vm._realtime_snapshot is not None
        assert screener_vm._realtime_snapshot["full_results"] is df
        assert screener_vm._realtime_snapshot["page_no"] == 3
        assert screener_vm._full_results is None
        assert screener_vm.state.page_no == 1

    def test_idempotent(self, screener_vm):
        screener_vm._set_state(mode="HISTORY")
        screener_vm._realtime_snapshot = None
        screener_vm.switch_to_history()
        assert screener_vm._realtime_snapshot is None


class TestScreenerViewModelSwitchToRealtime:
    def test_restores_snapshot(self, screener_vm):
        df = pd.DataFrame({"a": [1]})
        screener_vm._set_state(mode="HISTORY")
        screener_vm._realtime_snapshot = {
            "full_results": df,
            "page_no": 3,
            "sort_column": "a",
            "sort_ascending": False,
            "ai_buffer": [{"x": 1}],
        }
        screener_vm.switch_to_realtime()
        assert screener_vm.state.mode == "REALTIME"
        assert screener_vm._full_results is df
        assert screener_vm.state.page_no == 3
        assert screener_vm._realtime_snapshot is None

    def test_merges_discarded_buffer(self, screener_vm):
        screener_vm._set_state(mode="HISTORY")
        screener_vm._realtime_snapshot = {
            "full_results": None,
            "page_no": 1,
            "sort_column": None,
            "sort_ascending": True,
            "ai_buffer": [],
        }
        screener_vm._discarded_buffer = [{"y": 2}, {"y": 3}]
        screener_vm.switch_to_realtime()
        assert screener_vm._ai_buffer == [{"y": 2}, {"y": 3}]
        assert screener_vm._discarded_buffer == []

    def test_idempotent(self, screener_vm):
        screener_vm._set_state(mode="REALTIME")
        screener_vm.switch_to_realtime()
        assert screener_vm.state.mode == "REALTIME"


class TestScreenerViewModelGetExportData:
    def test_returns_df(self, screener_vm):
        df = pd.DataFrame({"a": [1, 2]})
        screener_vm._full_results = df
        assert screener_vm.get_export_data() is df

    def test_returns_none_when_empty(self, screener_vm):
        screener_vm._full_results = pd.DataFrame()
        assert screener_vm.get_export_data() is None

    def test_returns_none_when_none(self, screener_vm):
        screener_vm._full_results = None
        assert screener_vm.get_export_data() is None


class TestScreenerViewModelGetStrategies:
    async def test_delegates_to_strategy_mgr(self, screener_vm, mock_sm):
        mock_sm.get_all_names.return_value = {"s1": "Strategy 1"}
        result = await screener_vm.get_strategies()
        mock_sm.get_all_names.assert_called_once()
        assert result == {"s1": "Strategy 1"}


class TestScreenerViewModelGetStrategyParams:
    def test_injects_ai_system_prompt(self, screener_vm, mock_sm):
        mock_sm.get_strategy_params.return_value = [{"name": "threshold", "type": "float"}]
        result = screener_vm.get_strategy_params("s1")
        assert any(p["name"] == "ai_system_prompt" for p in result)
        mock_sm.get_strategy_params.assert_called_once_with("s1")

    def test_no_duplicate_ai_system_prompt(self, screener_vm, mock_sm):
        mock_sm.get_strategy_params.return_value = [
            {"name": "ai_system_prompt", "type": "textarea", "default": "custom"}
        ]
        result = screener_vm.get_strategy_params("s1")
        count = sum(1 for p in result if p["name"] == "ai_system_prompt")
        assert count == 1


class TestScreenerViewModelOnAiResultStream:
    def test_buffers_and_appends_log(self, screener_vm):
        """_on_ai_result_stream 将日志追加到 state.logs（替代 on_log 回调）。"""
        screener_vm._last_ai_update = 0
        screener_vm._flush_pending = False
        screener_vm._flush_ai_buffer = MagicMock()
        mock_loop = MagicMock()
        with patch.object(asyncio, "get_running_loop", return_value=mock_loop):
            row = {"name": "TestStock", "ai_score": 85, "thinking": "good"}
            screener_vm._on_ai_result_stream(row)
        assert len(screener_vm._ai_buffer) == 1
        assert len(screener_vm.state.logs) == 1
        assert screener_vm.state.logs[0].name == "TestStock"
        assert screener_vm.state.logs[0].score == 85
        assert screener_vm.state.logs[0].thinking == "good"

    def test_noop_on_empty_data(self, screener_vm):
        screener_vm._on_ai_result_stream(None)
        assert screener_vm._ai_buffer == []
        assert len(screener_vm.state.logs) == 0

    def test_noop_on_empty_dict(self, screener_vm):
        screener_vm._on_ai_result_stream({})
        assert screener_vm._ai_buffer == []
        assert len(screener_vm.state.logs) == 0


class TestScreenerViewModelRunStrategy:
    async def test_with_not_found_strategy(self, screener_vm, mock_sm):
        """策略不存在时，state.status_color 设为 error（替代 on_status 回调）。"""
        mock_sm.get_strategy.return_value = None
        await screener_vm.run_strategy("nonexistent")
        mock_sm.get_strategy.assert_called_once_with("nonexistent")
        assert screener_vm.state.status_color == "error"
        assert screener_vm.state.status_message is not None
        assert screener_vm.state.status_message.key == "screener_strategy_not_found"


class TestScreenerViewModelRunStrategyExecution:
    @pytest.fixture(autouse=True)
    def _setup(self, screener_vm, mock_sm, mock_dp, mock_rm, mock_tm):
        self.vm = screener_vm
        self.mock_sm = mock_sm
        self.mock_dp = mock_dp
        self.mock_rm = mock_rm
        self.mock_rm.save_results = AsyncMock()
        self.mock_tm = mock_tm

    def _make_strategy(self, name_key="strategy_name", is_async=True):
        strategy = MagicMock()
        strategy.name_key = name_key
        if is_async:
            strategy.filter = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["test"]}))
        else:
            strategy.filter = MagicMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["test"]}))
        return strategy

    # Capture the coroutine_factory that run_strategy submits to TaskManager,
    # then execute it manually to test the inner _execute_screening logic.
    async def _capture_and_execute(self, strategy_key, save_results=True, params=None, strategy=None):
        if strategy is None:
            strategy = self._make_strategy()
        self.mock_sm.get_strategy.return_value = strategy

        captured_factory = None

        def capture_factory(**kwargs):
            nonlocal captured_factory
            captured_factory = kwargs.get("coroutine_factory")
            return "task-123"

        self.mock_tm.submit_task = MagicMock(side_effect=capture_factory)
        self.mock_tm.update_progress = MagicMock()

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await self.vm.run_strategy(strategy_key, save_results=save_results, params=params)

        assert captured_factory is not None
        return captured_factory

    async def test_execute_screening_success_with_results(self):
        strategy = self._make_strategy()
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )
        self.mock_rm.save_results = AsyncMock()

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await captured_factory(task_id="task-123")

        assert self.vm._full_results is not None
        assert len(self.vm._full_results) > 0
        self.mock_rm.save_results.assert_awaited_once()

    async def test_execute_screening_success_no_save(self):
        strategy = self._make_strategy()
        captured_factory = await self._capture_and_execute("momentum", save_results=False, strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await captured_factory(task_id="task-123")

        assert self.vm._full_results is not None
        self.mock_rm.save_results.assert_not_called()

    async def test_execute_screening_no_screening_data(self):
        strategy = self._make_strategy()
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(return_value=None)
        self.mock_dp.init_data = AsyncMock()
        self.mock_dp.get_strategy_data = AsyncMock(return_value=None)

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            with pytest.raises(RuntimeError, match="Strategy execution crashed"):
                await captured_factory(task_id="task-123")

    async def test_execute_screening_empty_screening_data(self):
        strategy = self._make_strategy()
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={"screening_data": pd.DataFrame(), "trade_date": "2026-05-16"}
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            with pytest.raises(RuntimeError, match="Strategy execution crashed"):
                await captured_factory(task_id="task-123")

    async def test_execute_screening_cancelled_error(self):
        strategy = self._make_strategy()
        strategy.filter = AsyncMock(side_effect=asyncio.CancelledError())
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            with pytest.raises(asyncio.CancelledError):
                await captured_factory(task_id="task-123")

    async def test_execute_screening_quality_gate_error(self):
        from data.persistence.quality_gate import QualityGateError

        strategy = self._make_strategy()
        strategy.filter = AsyncMock(side_effect=QualityGateError("data quality too low"))
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            result = await captured_factory(task_id="task-123")

        assert result is not None

    async def test_execute_screening_generic_exception(self):
        strategy = self._make_strategy()
        strategy.filter = AsyncMock(side_effect=ValueError("something broke"))
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            with pytest.raises(RuntimeError, match="Strategy execution crashed"):
                await captured_factory(task_id="task-123")

    async def test_execute_screening_empty_results(self):
        strategy = self._make_strategy()
        strategy.filter = AsyncMock(return_value=pd.DataFrame())
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await captured_factory(task_id="task-123")

        assert isinstance(self.vm._full_results, pd.DataFrame)
        assert self.vm._full_results.empty

    async def test_execute_screening_none_results(self):
        strategy = self._make_strategy()
        strategy.filter = AsyncMock(return_value=None)
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await captured_factory(task_id="task-123")

        assert isinstance(self.vm._full_results, pd.DataFrame)
        assert self.vm._full_results.empty

    async def test_execute_screening_sync_filter(self):
        strategy = self._make_strategy(is_async=False)
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as MockTP:
                tp_instance = MagicMock()
                tp_instance.run_async = AsyncMock(
                    return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["test"]})
                )
                MockTP.return_value = tp_instance
                await captured_factory(task_id="task-123")

        assert self.vm._full_results is not None
        assert len(self.vm._full_results) > 0

    async def test_execute_screening_with_diagnostics_degraded(self):
        strategy = self._make_strategy()
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
                "_diagnostics": {
                    "strategy_ready": False,
                    "table_status": {
                        "table_a": {"ready": False},
                        "table_b": {"ready": True},
                    },
                },
            }
        )

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await captured_factory(task_id="task-123")

        assert self.vm._full_results is not None

    async def test_execute_screening_retries_on_empty_context(self):
        strategy = self._make_strategy()
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        call_count = 0

        async def _get_data():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None
            return {
                "screening_data": pd.DataFrame({"a": [1]}),
                "trade_date": "2026-05-16",
            }

        self.mock_dp.get_strategy_data = _get_data
        self.mock_dp.init_data = AsyncMock()

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await captured_factory(task_id="task-123")

        assert call_count == 2
        self.mock_dp.init_data.assert_awaited_once()

    async def test_run_strategy_task_rejected(self):
        """TaskManager 拒绝任务时，state.loading 为 False 且 status_color 为 warning。"""
        strategy = self._make_strategy()
        self.mock_sm.get_strategy.return_value = strategy
        self.mock_tm.submit_task.return_value = None

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await self.vm.run_strategy("momentum")

        assert self.vm.state.loading is False
        assert self.vm.state.status_color == "warning"

    async def test_run_strategy_resets_state(self):
        strategy = self._make_strategy()
        self.mock_sm.get_strategy.return_value = strategy

        self.vm._full_results = pd.DataFrame({"a": [1]})
        self.vm._set_state(page_no=5)
        self.vm._ai_buffer = [{"x": 1}]

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            await self.vm.run_strategy("momentum")

        assert self.vm._full_results is None
        assert self.vm.state.page_no == 1
        assert self.vm._ai_buffer == []

    async def test_execute_screening_missing_trade_date(self):
        strategy = self._make_strategy()
        captured_factory = await self._capture_and_execute("momentum", strategy=strategy)

        self.mock_dp.get_strategy_data = AsyncMock(return_value={"screening_data": pd.DataFrame({"a": [1]})})

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            with pytest.raises(RuntimeError, match="Strategy execution crashed"):
                await captured_factory(task_id="task-123")


class TestScreenerViewModelFlushAiBuffer:
    async def test_flush_with_data_realtime_no_existing_results(self, screener_vm):
        screener_vm._ai_buffer = [{"name": "Stock1", "ai_score": 85}]
        screener_vm._flush_pending = True

        await screener_vm._flush_ai_buffer()

        assert screener_vm._full_results is not None
        assert len(screener_vm._full_results) == 1
        assert screener_vm._ai_buffer == []
        assert screener_vm._flush_pending is False

    async def test_flush_with_data_realtime_existing_results(self, screener_vm):
        screener_vm._full_results = pd.DataFrame({"name": ["Stock0"], "ai_score": [70]})
        screener_vm._ai_buffer = [{"name": "Stock1", "ai_score": 85}]
        screener_vm._flush_pending = True

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as MockTP:
            tp_instance = MagicMock()
            tp_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *args, **kwargs: fn(*args, **kwargs))
            MockTP.return_value = tp_instance
            await screener_vm._flush_ai_buffer()

        assert len(screener_vm._full_results) == 2
        assert screener_vm._flush_pending is False

    async def test_flush_history_mode_saves_to_discarded_buffer(self, screener_vm):
        screener_vm._set_state(mode="HISTORY")
        screener_vm._ai_buffer = [{"name": "Stock1", "ai_score": 85}]
        screener_vm._flush_pending = True

        await screener_vm._flush_ai_buffer()

        assert len(screener_vm._discarded_buffer) == 1
        assert screener_vm._ai_buffer == []
        assert screener_vm._flush_pending is False

    async def test_flush_empty_buffer(self, screener_vm):
        screener_vm._ai_buffer = []
        screener_vm._flush_pending = True

        await screener_vm._flush_ai_buffer()

        assert screener_vm._flush_pending is False

    async def test_flush_with_ai_score_sorts_and_reorders_columns(self, screener_vm):
        screener_vm._full_results = pd.DataFrame(
            {"name": ["Stock0"], "ai_score": [70], "ai_reason": ["ok"], "other": [1]}
        )
        screener_vm._ai_buffer = [{"name": "Stock1", "ai_score": 95, "ai_reason": "great", "other": 2}]
        screener_vm._flush_pending = True

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as MockTP:
            tp_instance = MagicMock()
            tp_instance.run_async = AsyncMock(side_effect=lambda tt, fn, *args, **kwargs: fn(*args, **kwargs))
            MockTP.return_value = tp_instance
            await screener_vm._flush_ai_buffer()

        cols = list(screener_vm._full_results.columns)
        assert cols.index("ai_score") < cols.index("other")

    async def test_flush_exception_resets_pending_flag(self, screener_vm):
        screener_vm._ai_buffer = [{"name": "Stock1"}]
        screener_vm._flush_pending = True

        with patch(
            "ui.viewmodels.screener_view_model.pd.DataFrame",
            side_effect=Exception("boom"),
        ):
            await screener_vm._flush_ai_buffer()

        assert screener_vm._flush_pending is False


class TestScreenerViewModelLoadHistoryTree:
    async def test_with_data(self, screener_vm):
        df = pd.DataFrame(
            {
                "trade_date": ["2026-05-16", "2026-05-16", "2026-05-15"],
                "run_id": ["r1", "r2", "r3"],
                "strategy_name": ["momentum", "value", "momentum"],
                "cnt": [5, 3, 7],
            }
        )
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_tree = AsyncMock(return_value=df)
            MockCM.return_value = cm_instance
            await screener_vm.load_history_tree()

        rows = screener_vm.state.history_tree.rows
        assert len(rows) == 2
        row_0516 = next(r for r in rows if r.d_key == "2026-05-16")
        assert row_0516.total_cnt == 8
        assert len(row_0516.strategies) == 2
        assert row_0516.strategies[0]["run_id"] == "r1"
        assert row_0516.strategies[0]["strategy_name"] == "momentum"
        row_0515 = next(r for r in rows if r.d_key == "2026-05-15")
        assert row_0515.total_cnt == 7
        assert len(row_0515.strategies) == 1
        assert row_0515.strategies[0]["run_id"] == "r3"
        # offset = 0 + len(df) * 5 = 15, has_more = len(df) >= 5 is False
        assert screener_vm.state.history_tree.offset == 15
        assert screener_vm.state.history_tree.has_more is False

    async def test_with_empty_data(self, screener_vm):
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_tree = AsyncMock(return_value=pd.DataFrame())
            MockCM.return_value = cm_instance
            await screener_vm.load_history_tree()

        assert screener_vm.state.history_tree.rows == ()
        assert screener_vm.state.history_tree.offset == 0
        assert screener_vm.state.history_tree.has_more is False

    async def test_with_none_data(self, screener_vm):
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_tree = AsyncMock(return_value=None)
            MockCM.return_value = cm_instance
            await screener_vm.load_history_tree()

        assert screener_vm.state.history_tree.rows == ()
        assert screener_vm.state.history_tree.offset == 0
        assert screener_vm.state.history_tree.has_more is False

    async def test_with_append_uses_state_offset(self, screener_vm):
        # 预设 offset=10 模拟已加载过一页; append=True 时 VM 应将其透传到 cache
        screener_vm._set_state(history_tree=replace(screener_vm.state.history_tree, offset=10))
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_tree = AsyncMock(return_value=None)
            MockCM.return_value = cm_instance
            await screener_vm.load_history_tree(append=True)

        cm_instance.get_history_tree.assert_awaited_once_with(offset=10)


class TestScreenerViewModelLoadHistoryData:
    async def test_with_data_and_ai_score(self, screener_vm):
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["Test"], "ai_score": [85]})

        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_records = AsyncMock(return_value=df)
            MockCM.return_value = cm_instance
            await screener_vm.load_history_data("2026-05-16")

        assert screener_vm._full_results is not None
        assert screener_vm.state.sort_column == "ai_score"
        assert screener_vm.state.sort_ascending is False
        assert screener_vm.state.page_no == 1

    async def test_with_data_no_ai_score(self, screener_vm):
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["Test"]})
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_records = AsyncMock(return_value=df)
            MockCM.return_value = cm_instance
            await screener_vm.load_history_data("2026-05-16")

        assert screener_vm.state.sort_column is None

    async def test_with_empty_data(self, screener_vm):
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_records = AsyncMock(return_value=pd.DataFrame())
            MockCM.return_value = cm_instance
            await screener_vm.load_history_data("2026-05-16")

        assert isinstance(screener_vm._full_results, pd.DataFrame)
        assert screener_vm._full_results.empty
        assert screener_vm.state.sort_column is None

    async def test_with_none_data(self, screener_vm):
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_records = AsyncMock(return_value=None)
            MockCM.return_value = cm_instance
            await screener_vm.load_history_data("2026-05-16")

        assert isinstance(screener_vm._full_results, pd.DataFrame)
        assert screener_vm._full_results.empty

    async def test_with_strategy_name_and_run_id(self, screener_vm):
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["Test"]})
        with patch("ui.viewmodels.screener_view_model.CacheManager") as MockCM:
            cm_instance = MagicMock()
            cm_instance.get_history_records = AsyncMock(return_value=df)
            MockCM.return_value = cm_instance
            await screener_vm.load_history_data("2026-05-16", strategy_name="momentum", run_id="r1")

        cm_instance.get_history_records.assert_awaited_once_with("2026-05-16", "momentum", "r1")


class TestScreenerViewModelExportResults:
    async def test_successful_export(self, screener_vm):
        screener_vm._full_results = pd.DataFrame({"a": [1, 2], "b": [3, 4]})

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as MockTP:
            tp_instance = MagicMock()
            tp_instance.run_async = AsyncMock(return_value=None)
            MockTP.return_value = tp_instance
            filepath, error = await screener_vm.export_results("/tmp/test.csv")

        assert filepath == "/tmp/test.csv"
        assert error is None

    async def test_export_no_data(self, screener_vm):
        screener_vm._full_results = None
        filepath, error = await screener_vm.export_results("/tmp/test.csv")
        assert filepath is None
        assert error == "No data to export"

    async def test_export_empty_dataframe(self, screener_vm):
        screener_vm._full_results = pd.DataFrame()
        filepath, error = await screener_vm.export_results("/tmp/test.csv")
        assert filepath is None
        assert error == "No data to export"

    async def test_export_exception(self, screener_vm):
        screener_vm._full_results = pd.DataFrame({"a": [1]})

        with patch("ui.viewmodels.screener_view_model.ThreadPoolManager") as MockTP:
            tp_instance = MagicMock()
            tp_instance.run_async = AsyncMock(side_effect=PermissionError("denied"))
            MockTP.return_value = tp_instance
            filepath, error = await screener_vm.export_results("/tmp/test.csv")

        assert filepath is None
        assert "denied" in error


class TestScreenerViewModelGetStrategyDesc:
    def test_with_valid_strategy(self, screener_vm, mock_sm):
        mock_strategy = MagicMock()
        mock_strategy.desc_key = "strategy_desc_key"
        mock_sm.get_strategy.return_value = mock_strategy

        with patch("ui.viewmodels.screener_view_model.I18n") as mock_i18n:
            mock_i18n.get.return_value = "A strategy description"
            result = screener_vm.get_strategy_desc("momentum")

        assert result == "A strategy description"
        mock_sm.get_strategy.assert_called_once_with("momentum")

    def test_with_no_strategy(self, screener_vm, mock_sm):
        mock_sm.get_strategy.return_value = None
        result = screener_vm.get_strategy_desc("nonexistent")
        assert result == ""


class TestScreenerViewModelOnAiProgress:
    def test_updates_state_status(self, screener_vm):
        """_on_ai_progress 更新 state.status_message/status_color（替代 on_status 回调）。"""
        screener_vm._on_ai_progress(5, 10, "analyzing")

        assert screener_vm.state.status_color == "info"
        assert screener_vm.state.status_message is not None
        assert screener_vm.state.status_message.key == "screener_ai_analyzing"
        assert screener_vm.state.status_message.params == {"done": 5, "total": 10, "msg": "analyzing"}


class TestScreenerViewModelOnAiResultStreamFlush:
    def test_flush_via_main_loop_fallback(self, screener_vm):
        """无 running loop 时，通过 main_loop fallback 调度 flush。"""
        screener_vm._last_ai_update = 0
        screener_vm._flush_pending = False
        screener_vm._main_loop = MagicMock()
        screener_vm._main_loop.is_running.return_value = True
        screener_vm._flush_ai_buffer = MagicMock()

        with (
            patch.object(asyncio, "get_running_loop", side_effect=RuntimeError("no loop")),
            patch.object(asyncio, "run_coroutine_threadsafe") as mock_rcts,
        ):
            row = {"name": "TestStock", "ai_score": 85, "thinking": "good"}
            screener_vm._on_ai_result_stream(row)

        mock_rcts.assert_called_once()
        assert len(screener_vm._ai_buffer) == 1
        # Log still appended to state
        assert len(screener_vm.state.logs) == 1

    def test_flush_no_loop_available(self, screener_vm):
        """无可用 loop 时，flush_pending 重置为 False，但日志仍追加到 state。"""
        screener_vm._last_ai_update = 0
        screener_vm._flush_pending = False
        screener_vm._main_loop = None

        with patch.object(asyncio, "get_running_loop", side_effect=RuntimeError("no loop")):
            row = {"name": "TestStock", "ai_score": 85, "thinking": "good"}
            screener_vm._on_ai_result_stream(row)

        assert screener_vm._flush_pending is False
        assert len(screener_vm._ai_buffer) == 1
        assert len(screener_vm.state.logs) == 1

    def test_flush_already_pending_skips_scheduling(self, screener_vm):
        """flush_pending 为 True 时，跳过调度但日志仍追加到 state。"""
        screener_vm._last_ai_update = 0
        screener_vm._flush_pending = True

        with patch.object(asyncio, "get_running_loop") as mock_get_loop:
            row = {"name": "TestStock", "ai_score": 85, "thinking": "good"}
            screener_vm._on_ai_result_stream(row)

        mock_get_loop.assert_not_called()
        assert len(screener_vm._ai_buffer) == 1
        assert len(screener_vm.state.logs) == 1


# ============================================================================
# Message dataclass 测试 — VM state 中的 i18n 消息字段契约
# ============================================================================


from ui.viewmodels import Message  # noqa: E402


class TestMessageDataclass:
    """Message dataclass 契约测试：验证 i18n 消息字段的不变式。

    Message 是 VM state 中的 i18n 消息载体（方案 §3.1）：
    - VM 只产出 (key, params)，不感知 locale
    - View 渲染时调 I18n.get(msg.key, **msg.params)
    """

    def test_key_only_uses_default_empty_params(self) -> None:
        """仅 key：params 默认为空 dict（非 None）。"""
        msg = Message(key="screener_load_failed")

        assert msg.key == "screener_load_failed"
        assert msg.params == {}
        assert isinstance(msg.params, dict)

    def test_key_with_params(self) -> None:
        """key + params：完整字段。"""
        msg = Message(key="strategy_missing_apis", params={"api": "daily"})

        assert msg.key == "strategy_missing_apis"
        assert msg.params == {"api": "daily"}

    def test_default_params_not_shared_across_instances(self) -> None:
        """default_factory 保证每个 Message 的 params 是独立 dict。"""
        msg1 = Message(key="a")
        msg2 = Message(key="b")

        msg1.params["x"] = 1

        assert msg2.params == {}  # msg2.params 不受 msg1 修改影响

    def test_frozen_raises_on_attribute_assignment(self) -> None:
        """frozen=True：直接赋值抛 FrozenInstanceError。"""
        from dataclasses import FrozenInstanceError

        msg = Message(key="x")

        with pytest.raises(FrozenInstanceError):
            msg.key = "y"  # type: ignore[misc]

    def test_frozen_raises_on_params_reassignment(self) -> None:
        """frozen=True：params 字段不可重新赋值。"""
        from dataclasses import FrozenInstanceError

        msg = Message(key="x")

        with pytest.raises(FrozenInstanceError):
            msg.params = {"new": 1}  # type: ignore[misc]

    def test_equality_same_key_and_params(self) -> None:
        """相同 key + params 的两个 Message 相等。"""
        msg1 = Message(key="x", params={"a": 1})
        msg2 = Message(key="x", params={"a": 1})

        assert msg1 == msg2

    def test_inequality_different_key(self) -> None:
        """不同 key 的 Message 不等。"""
        msg1 = Message(key="x")
        msg2 = Message(key="y")

        assert msg1 != msg2

    def test_inequality_different_params(self) -> None:
        """相同 key 但不同 params 的 Message 不等。"""
        msg1 = Message(key="x", params={"a": 1})
        msg2 = Message(key="x", params={"a": 2})

        assert msg1 != msg2

    def test_params_accepts_arbitrary_value_types(self) -> None:
        """params 值可为任意类型（str/int/list/dict）。"""
        msg = Message(
            key="x",
            params={"name": "策略A", "count": 10, "tags": ["a", "b"], "meta": {"k": "v"}},
        )

        assert msg.params["name"] == "策略A"
        assert msg.params["count"] == 10
        assert msg.params["tags"] == ["a", "b"]
        assert msg.params["meta"] == {"k": "v"}

    def test_i18n_get_unpacks_params(self) -> None:
        """View 消费：I18n.get(msg.key, **msg.params) 解包 params。

        验证 Message 契约与 I18n.get(key, **params) 调用模式兼容。
        """
        from core.i18n import I18n

        msg = Message(key="home_northbound")
        # I18n.get(msg.key, **msg.params) 应不抛异常
        result = I18n.get(msg.key, **msg.params)
        assert isinstance(result, str)
        assert len(result) > 0
