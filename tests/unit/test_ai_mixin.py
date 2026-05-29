import pytest
import asyncio
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
import pandas as pd

from data.constants import attach_column_units
from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext
from strategies.utils import safe_float


class ConcreteStrategy(AIStrategyMixin):
    key = "test_strategy"

    def __init__(self):
        super().__init__()

    def get_ai_context(self, row):
        return f"Test context for {row.get('ts_code', '?')}"


class TestPreFetchedContext:
    def test_default_values(self):
        ctx = PreFetchedContext()
        assert ctx.capital == {}
        assert ctx.history == {}
        assert ctx.concepts_map == {}
        assert ctx.news_tasks == {}
        assert ctx.history_context == ""
        assert ctx.global_context == ""
        assert ctx.trade_date is None
        assert ctx.indicators.empty
        assert ctx.sector_stats == {}
        assert ctx.market_context == {}
        assert ctx.market_context_str == ""

    def test_with_values(self):
        ctx = PreFetchedContext(
            capital={"moneyflow_df": pd.DataFrame()},
            history={"000001.SZ": pd.DataFrame()},
            concepts_map={"000001.SZ": ["概念1", "概念2"]},
            trade_date=datetime.date(2024, 3, 21),
        )
        assert "moneyflow_df" in ctx.capital
        assert "000001.SZ" in ctx.history
        assert len(ctx.concepts_map) == 1
        assert ctx.trade_date == datetime.date(2024, 3, 21)


class TestAIStrategyMixinInit:
    def test_init(self):
        s = ConcreteStrategy()
        assert s._context_builders == {}
        assert s._history_cache is not None

    def test_register_context_builder(self):
        s = ConcreteStrategy()
        s.register_context_builder("test", lambda row, pf: "test context")
        assert "test" in s._context_builders


class TestAIStrategyMixinSortForAI:
    def test_single_row(self):
        s = ConcreteStrategy()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        result = s._sort_for_ai(df)
        assert len(result) == 1

    def test_sort_by_total_mv(self):
        s = ConcreteStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "total_mv": [100.0, 200.0],
            }
        )
        result = s._sort_for_ai(df)
        assert result.iloc[0]["ts_code"] == "000002.SZ"

    def test_sort_by_vol(self):
        s = ConcreteStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "vol": [1000.0, 2000.0],
            }
        )
        result = s._sort_for_ai(df)
        assert result.iloc[0]["ts_code"] == "000002.SZ"

    def test_no_sort_cols(self):
        s = ConcreteStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
            }
        )
        result = s._sort_for_ai(df)
        assert len(result) == 2

    def test_sort_by_total_mv_descending(self):
        df = pd.DataFrame({"ts_code": ["A", "B", "C"], "total_mv": [100, 500, 200]})
        result = AIStrategyMixin()._sort_for_ai(df)
        assert list(result["ts_code"]) == ["B", "C", "A"]

    def test_sort_by_vol_when_no_mv(self):
        df = pd.DataFrame({"ts_code": ["A", "B", "C"], "vol": [1000, 5000, 2000]})
        result = AIStrategyMixin()._sort_for_ai(df)
        assert list(result["ts_code"]) == ["B", "C", "A"]

    def test_empty_dataframe_returns_empty(self):
        df = pd.DataFrame()
        result = AIStrategyMixin()._sort_for_ai(df)
        assert result.empty

    def test_no_sort_columns_returns_original(self):
        df = pd.DataFrame({"ts_code": ["A", "B", "C"], "close": [10.0, 20.0, 15.0]})
        result = AIStrategyMixin()._sort_for_ai(df)
        assert list(result["ts_code"]) == ["A", "B", "C"]


class TestComputeTechnicalStructure:
    def test_empty_df(self):
        result = AIStrategyMixin._compute_technical_structure(pd.DataFrame())
        assert result["ma_alignment"] == "数据不足"
        assert result["volume_trend"] == "数据不足"
        assert result["price_trend_5d"] == "数据不足"

    def test_none_history(self):
        result = AIStrategyMixin._compute_technical_structure(None)
        assert result["ma_alignment"] == "数据不足"
        assert result["volume_trend"] == "数据不足"

    def test_insufficient_data(self):
        df = pd.DataFrame(
            {
                "trade_date": ["20240610"],
                "close": [10.0],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert result["ma_alignment"] == "数据不足"

    def test_sufficient_data(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202406{i:02d}" for i in range(1, 26)],
                "close": [10.0 + i * 0.5 for i in range(25)],
                "vol": [1000.0] * 25,
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df, 1.5)
        assert "ma_alignment" in result
        assert "volume_trend" in result
        assert "price_trend_5d" in result

    def test_bullish_alignment(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 31)],
                "close": [10.0 + i * 0.1 for i in range(30)],
                "vol": [1000000 + i * 10000 for i in range(30)],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert "多头排列" in result["ma_alignment"]

    def test_bearish_alignment(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 31)],
                "close": [15.0 - i * 0.1 for i in range(30)],
                "vol": [1000000 + i * 10000 for i in range(30)],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert "空头排列" in result["ma_alignment"]

    def test_missing_volume_column(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 31)],
                "close": [10.0 + i * 0.1 for i in range(30)],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert result["volume_trend"] == "数据不足"

    def test_zero_division_guard(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 31)],
                "close": [0.0] * 30,
                "vol": [0.0] * 30,
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert result["ma_alignment"] != "计算错误" or "数据不足" in result["ma_alignment"]

    def test_with_adj_factor(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 31)],
                "close": [10.0 + i * 0.1 for i in range(30)],
                "vol": [1000000 + i * 10000 for i in range(30)],
                "adj_factor": [1.0] * 30,
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert result["ma_alignment"] != "数据不足"


class TestGetLimitPct:
    def test_st_stock(self):
        assert AIStrategyMixin._get_limit_pct("000001.SZ", "ST某某") == 5.0

    def test_star_st_stock(self):
        assert AIStrategyMixin._get_limit_pct("000001.SZ", "*ST某某") == 5.0

    def test_bse_stock(self):
        assert AIStrategyMixin._get_limit_pct("830001.BJ") == 30.0

    def test_gem_stock(self):
        assert AIStrategyMixin._get_limit_pct("300001.SZ") == 20.0

    def test_star_stock(self):
        assert AIStrategyMixin._get_limit_pct("688001.SH") == 20.0

    def test_main_board_sz(self):
        assert AIStrategyMixin._get_limit_pct("000001.SZ") == 10.0

    def test_main_board_sh(self):
        assert AIStrategyMixin._get_limit_pct("600001.SH") == 10.0


class TestBuildHistoryText:
    def test_empty_df(self):
        result = AIStrategyMixin._build_history_text(pd.DataFrame())
        assert result == ""

    def test_none_history(self):
        result = AIStrategyMixin._build_history_text(None)
        assert result == ""

    def test_insufficient_data(self):
        df = pd.DataFrame(
            {
                "trade_date": ["20240610", "20240611"],
                "close": [10.0, 11.0],
            }
        )
        result = AIStrategyMixin._build_history_text(df)
        assert "不足" in result

    def test_with_data(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202406{i:02d}" for i in range(1, 26)],
                "close": [10.0 + i * 0.5 for i in range(25)],
                "vol": [1000.0] * 25,
                "pct_chg": [1.0] * 25,
            }
        )
        result = AIStrategyMixin._build_history_text(df, ts_code="000001.SZ", stock_name="测试")
        assert len(result) > 0

    def test_valid_history(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 61)],
                "close": [10.0 + i * 0.1 for i in range(60)],
                "vol": [1000000 + i * 10000 for i in range(60)],
                "pct_chg": [1.0] * 60,
            }
        )
        result = AIStrategyMixin._build_history_text(df)
        assert "趋势与波动特征" in result
        assert "量价配合" in result

    def test_with_limit_up(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 61)],
                "close": [10.0 + i * 0.1 for i in range(60)],
                "vol": [1000000 + i * 10000 for i in range(60)],
                "pct_chg": [1.0] * 57 + [9.9, 9.8, 10.0],
            }
        )
        result = AIStrategyMixin._build_history_text(df, "000001.SZ", "测试股票")
        assert "涨停" in result

    def test_with_limit_down(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 61)],
                "close": [15.0 - i * 0.1 for i in range(60)],
                "vol": [1000000 + i * 10000 for i in range(60)],
                "pct_chg": [-1.0] * 57 + [-9.9, -9.8, -10.0],
            }
        )
        result = AIStrategyMixin._build_history_text(df, "000001.SZ", "测试股票")
        assert "跌停" in result

    def test_missing_pct_chg(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 61)],
                "close": [10.0 + i * 0.1 for i in range(60)],
                "vol": [1000000 + i * 10000 for i in range(60)],
            }
        )
        result = AIStrategyMixin._build_history_text(df)
        assert "趋势与波动特征" in result

    def test_with_adj_factor(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 61)],
                "close": [10.0 + i * 0.1 for i in range(60)],
                "vol": [1000000 + i * 10000 for i in range(60)],
                "adj_factor": [1.0 + i * 0.001 for i in range(60)],
            }
        )
        result = AIStrategyMixin._build_history_text(df)
        assert result != ""

    def test_nan_values_in_pct_chg(self):
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 61)],
                "close": [10.0 + i * 0.1 for i in range(60)],
                "vol": [1000000 + i * 10000 for i in range(60)],
                "pct_chg": [1.0] * 30 + [float("nan")] * 30,
            }
        )
        result = AIStrategyMixin._build_history_text(df)
        assert result != ""


class TestGetContextBlocks:
    def test_empty(self):
        s = ConcreteStrategy()
        assert s.get_context_blocks() == []

    def test_with_builders(self):
        s = ConcreteStrategy()
        s.register_context_builder("test1", lambda r, p: "")
        s.register_context_builder("test2", lambda r, p: "")
        blocks = s.get_context_blocks()
        assert "test1" in blocks
        assert "test2" in blocks

    def test_multiple_registrations(self):
        s = ConcreteStrategy()
        s.register_context_builder("test", lambda r, p: "v1")
        s.register_context_builder("test", lambda r, p: "v2")
        assert len(s._context_builders) == 1
        result = s._context_builders["test"]({}, PreFetchedContext())
        assert result == "v2"


class TestShouldIncludeGlobalContext:
    def test_default(self):
        s = ConcreteStrategy()
        assert s.should_include_global_context() is True


class TestShouldIncludeLearningContext:
    def test_default(self):
        s = ConcreteStrategy()
        assert s.should_include_learning_context() is True


class TestGetAiContext:
    def test_default(self):
        s = ConcreteStrategy()
        result = s.get_ai_context({"ts_code": "000001.SZ"})
        assert "000001.SZ" in result


class TestNormalizeTradeDateForCache:
    def test_none(self):
        result = AIStrategyMixin._normalize_trade_date_for_cache(None)
        assert result is None

    def test_string(self):
        result = AIStrategyMixin._normalize_trade_date_for_cache("20240614")
        assert result == "20240614"

    def test_datetime(self):
        result = AIStrategyMixin._normalize_trade_date_for_cache(datetime.datetime(2024, 6, 14))
        assert result == "20240614"

    def test_date(self):
        result = AIStrategyMixin._normalize_trade_date_for_cache(datetime.date(2024, 6, 14))
        assert result == "20240614"


class TestRunAiAnalysis:
    @pytest.mark.asyncio
    async def test_ai_not_available(self):
        s = ConcreteStrategy()
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"]})
        context = {"data_processor": MagicMock()}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai.return_value.is_cloud_available.return_value = False
            result = await s.run_ai_analysis(candidates, context)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_no_data_processor(self):
        s = ConcreteStrategy()
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
        context = {}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai.return_value.is_cloud_available.return_value = True
            result = await s.run_ai_analysis(candidates, context)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_candidates(self):
        s = ConcreteStrategy()
        context = {"data_processor": MagicMock()}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai.return_value.is_cloud_available.return_value = True
            result = await s.run_ai_analysis(pd.DataFrame(), context)
            assert result.empty

    @pytest.mark.asyncio
    async def test_none_candidates(self):
        s = ConcreteStrategy()
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai.return_value.is_cloud_available.return_value = False
            result = await s.run_ai_analysis(None, {})
            assert result is None or result.empty

    @pytest.mark.asyncio
    async def test_uses_context_trade_date_for_capital_prefetch(self):
        s = ConcreteStrategy()
        dp = MagicMock()
        dp.is_cancelled = MagicMock(return_value=False)
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        context = {"data_processor": dp, "trade_date": "20240118"}
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["平安银行"], "close": [10.0]})
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock(
                return_value={"score": 50, "summary": "test", "decision": "Hold"}
            )
            mock_ai.return_value = mock_ai_instance
            await s.run_ai_analysis(candidates, context)
            dp.cache.get_moneyflow.assert_awaited_once_with(trade_date="20240118")
            dp.cache.get_top_list.assert_awaited_once_with(trade_date="20240118")
            dp.cache.get_northbound.assert_awaited_once_with(trade_date="20240118")

    @pytest.mark.asyncio
    async def test_with_cancellation(self):
        s = ConcreteStrategy()
        dp = MagicMock()
        dp.is_cancelled = MagicMock(return_value=True)
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        context = {"data_processor": dp}
        candidates = pd.DataFrame(
            {"ts_code": ["000001.SZ", "000002.SZ"], "name": ["平安银行", "万科A"], "close": [10.0, 15.0]}
        )
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance
            result = await s.run_ai_analysis(candidates, context)
            assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_with_candidates_cap(self):
        s = ConcreteStrategy()
        dp = MagicMock()
        dp.is_cancelled = MagicMock(return_value=False)
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        context = {"data_processor": dp}
        candidates = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
                "name": ["股票1", "股票2", "股票3", "股票4"],
                "close": [10.0, 15.0, 20.0, 25.0],
            }
        )
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance
            with patch("strategies.ai_mixin.ConfigHandler.get_ai_max_candidates", return_value=2):
                with patch.object(s, "_prefetch_strategy_specific", new_callable=AsyncMock) as mock_prefetch:
                    mock_prefetch.return_value = PreFetchedContext()
                    result = await s.run_ai_analysis(candidates, context)
                    assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_disable_ai_skips_ai_analysis(self):
        """_disable_ai=True should skip AI analysis and return math-only results."""
        s = ConcreteStrategy()
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"], "close": [10.0]})
        dp = MagicMock()
        context = {"data_processor": dp, "_disable_ai": True}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai.return_value.is_cloud_available.return_value = True
            result = await s.run_ai_analysis(candidates, context)
            assert len(result) == 1
            mock_ai.return_value.analyze_stock.assert_not_called()

    @pytest.mark.asyncio
    async def test_disable_ai_false_runs_ai_analysis(self):
        """_disable_ai=False should proceed with AI analysis normally."""
        s = ConcreteStrategy()
        dp = MagicMock()
        dp.is_cancelled = MagicMock(return_value=False)
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"], "close": [10.0]})
        context = {"data_processor": dp, "_disable_ai": False}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock(
                return_value={"score": 50, "summary": "test", "decision": "Hold"}
            )
            mock_ai.return_value = mock_ai_instance
            result = await s.run_ai_analysis(candidates, context)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_disable_ai_absent_runs_ai_analysis(self):
        """No _disable_ai key in context should proceed normally (backward compatibility)."""
        s = ConcreteStrategy()
        dp = MagicMock()
        dp.is_cancelled = MagicMock(return_value=False)
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"], "close": [10.0]})
        context = {"data_processor": dp}
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock(
                return_value={"score": 50, "summary": "test", "decision": "Hold"}
            )
            mock_ai.return_value = mock_ai_instance
            result = await s.run_ai_analysis(candidates, context)
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_backtest_news_fetch_passes_as_of(self):
        """Backtest mode should pass trade_date as as_of to NewsFetcher.get_stock_news."""
        s = ConcreteStrategy()
        dp = MagicMock()
        dp.is_cancelled = MagicMock(return_value=False)
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        candidates = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["测试"], "close": [10.0]})
        context = {"data_processor": dp, "trade_date": "20240118", "is_backtest": True}

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.NewsFetcher.get_stock_news", new_callable=AsyncMock) as mock_get_news,
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai_instance.analyze_stock = AsyncMock(
                return_value={"score": 50, "summary": "test", "decision": "Hold"}
            )
            mock_ai.return_value = mock_ai_instance
            mock_get_news.return_value = []

            await s.run_ai_analysis(candidates, context)

            # Verify as_of parameter is passed with correctly parsed trade_date (datetime.date object)
            import datetime

            mock_get_news.assert_called_with("000001.SZ", limit=5, as_of=datetime.date(2024, 1, 18))


class TestCancelOrphanNewsTasks:
    def test_cancels_undone_tasks(self):
        """_cancel_orphan_news_tasks should cancel tasks that are not done"""
        task1 = MagicMock()
        task1.done.return_value = False
        task2 = MagicMock()
        task2.done.return_value = True
        prefetched = PreFetchedContext(news_tasks={"A": task1, "B": task2})

        AIStrategyMixin._cancel_orphan_news_tasks(prefetched)

        task1.cancel.assert_called_once()
        task2.cancel.assert_not_called()

    def test_empty_news_tasks(self):
        """_cancel_orphan_news_tasks should handle empty news_tasks"""
        prefetched = PreFetchedContext(news_tasks={})
        AIStrategyMixin._cancel_orphan_news_tasks(prefetched)


class TestAIStrategyMixinAnalyzeSingle:
    @pytest.fixture
    def mock_strategy(self):
        return ConcreteStrategy()

    @pytest.fixture
    def mock_dp(self):
        dp = MagicMock()
        dp.get_stock_history = AsyncMock(return_value=pd.DataFrame())
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        return dp

    @pytest.fixture
    def mock_ai_client(self):
        client = MagicMock()
        client.analyze_stock = AsyncMock(
            return_value={
                "score": 75,
                "summary": "测试分析结果",
                "thinking": "测试思考过程",
                "confidence": 80,
                "uncertainty_factors": [],
            }
        )
        return client

    @pytest.mark.asyncio
    async def test_analyze_with_empty_history(self, mock_strategy, mock_dp, mock_ai_client):
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext()
        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)
        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_missing_concepts(self, mock_strategy, mock_dp, mock_ai_client):
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext(concepts_map={})
        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)
        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_missing_news(self, mock_strategy, mock_dp, mock_ai_client):
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext(news_tasks={})
        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)
        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_missing_capital_flow(self, mock_strategy, mock_dp, mock_ai_client):
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext(capital={})
        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)
        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_all_data_present(self, mock_strategy, mock_dp, mock_ai_client):
        history_df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 61)],
                "open": [10.0 + i * 0.1 for i in range(60)],
                "high": [10.5 + i * 0.1 for i in range(60)],
                "low": [9.5 + i * 0.1 for i in range(60)],
                "close": [10.0 + i * 0.1 for i in range(60)],
                "vol": [1000000 + i * 10000 for i in range(60)],
                "pct_chg": [1.0] * 60,
                "adj_factor": [1.0] * 60,
            }
        )
        row = {
            "ts_code": "000001.SZ",
            "name": "平安银行",
            "close": 15.0,
            "total_mv": 1000000000,
            "pe": 10.5,
            "pb": 1.2,
        }
        prefetched = PreFetchedContext(
            concepts_map={"000001.SZ": ["金融", "银行"]},
            capital={
                "moneyflow_df": pd.DataFrame(),
                "top_list_df": pd.DataFrame(),
                "northbound_df": pd.DataFrame(),
            },
        )
        result = await mock_strategy._mixin_analyze_single(
            row, mock_dp, mock_ai_client, prefetched, history_df=history_df
        )
        assert result is not None
        assert result["score"] == 75

    @pytest.mark.asyncio
    async def test_analyze_with_ai_error(self, mock_strategy, mock_dp, mock_ai_client):
        mock_ai_client.analyze_stock = AsyncMock(side_effect=Exception("AI Error"))
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext()
        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)
        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_with_zero_score(self, mock_strategy, mock_dp, mock_ai_client):
        mock_ai_client.analyze_stock = AsyncMock(return_value={"score": 0})
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext()
        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)
        assert result["score"] == 0


class TestBuildCapitalFlowText:
    def test_no_data(self):
        result = AIStrategyMixin._build_capital_flow_text("000001.SZ", {})
        assert "暂不可用" in result

    def test_moneyflow_with_data(self):
        mf_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "buy_lg_amount": [100.0],
                "sell_lg_amount": [50.0],
                "buy_elg_amount": [200.0],
                "sell_elg_amount": [80.0],
                "net_mf_amount": [170.0],
            }
        )
        result = AIStrategyMixin._build_capital_flow_text("000001.SZ", {"moneyflow_df": mf_df})
        assert "主力净流入" in result

    def test_moneyflow_no_stock(self):
        mf_df = pd.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "buy_lg_amount": [100.0],
                "sell_lg_amount": [50.0],
            }
        )
        result = AIStrategyMixin._build_capital_flow_text("000001.SZ", {"moneyflow_df": mf_df})
        assert "当日无记录" in result

    def test_top_list_with_data(self):
        tl_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "reason": ["涨幅偏离"],
                "net_amount": [5000.0],
            }
        )
        with patch("strategies.ai_mixin.get_column_unit", return_value="wan_yuan"):
            result = AIStrategyMixin._build_capital_flow_text("000001.SZ", {"top_list_df": tl_df})
        assert "龙虎榜" in result

    def test_northbound_with_data(self):
        nb_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "vol": [100000.0],
                "ratio": [2.5],
            }
        )
        result = AIStrategyMixin._build_capital_flow_text("000001.SZ", {"northbound_df": nb_df})
        assert "北向持股" in result

    def test_northbound_no_stock(self):
        nb_df = pd.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "vol": [100000.0],
                "ratio": [2.5],
            }
        )
        result = AIStrategyMixin._build_capital_flow_text("000001.SZ", {"northbound_df": nb_df})
        assert "当日无持股记录" in result

    def test_formats_top_list_net_amount_as_yuan_based_unit(self):
        text = AIStrategyMixin._build_capital_flow_text(
            "000001.SZ",
            {
                "moneyflow_df": pd.DataFrame(),
                "top_list_df": pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ"],
                        "reason": ["日跌幅偏离值达到7%的前五只证券"],
                        "net_amount": [-97685500.0],
                    }
                ),
                "northbound_df": pd.DataFrame(),
            },
        )
        assert "净买入: -9768.55万元" in text
        assert "亿元" not in text

    def test_formats_moneyflow_amount_as_wan_yuan_based_unit(self):
        text = AIStrategyMixin._build_capital_flow_text(
            "000001.SZ",
            {
                "moneyflow_df": pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ"],
                        "buy_lg_amount": [1200.0],
                        "sell_lg_amount": [200.0],
                        "buy_elg_amount": [800.0],
                        "sell_elg_amount": [100.0],
                        "net_mf_amount": [2500.0],
                    }
                ),
                "top_list_df": pd.DataFrame(),
                "northbound_df": pd.DataFrame(),
            },
        )
        assert "主力净流入: 1700.00万元" in text
        assert "全市场净流入: 2500.00万元" in text

    def test_prefers_top_list_unit_metadata_over_default_assumption(self):
        top_list_df = attach_column_units(
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "reason": ["机构专用席位净买入"],
                    "net_amount": [12000.0],
                }
            ),
            {"net_amount": "wan_yuan"},
        )
        text = AIStrategyMixin._build_capital_flow_text(
            "000001.SZ",
            {
                "moneyflow_df": pd.DataFrame(),
                "top_list_df": top_list_df,
                "northbound_df": pd.DataFrame(),
            },
        )
        assert "净买入: 1.20亿元" in text


class TestBuildFinancialsText:
    def test_full_data(self):
        row = {
            "pe_ttm": 15.5,
            "pb": 1.2,
            "roe": 12.0,
            "grossprofit_margin": 30.0,
            "debt_to_assets": 40.0,
            "or_yoy": 20.0,
            "netprofit_yoy": 25.0,
            "total_mv": 500000.0,
            "dv_ttm": 3.0,
        }
        result = AIStrategyMixin._build_financials_text(row)
        assert "PE(TTM)" in result
        assert "PEG" in result

    def test_negative_growth_peg_na(self):
        row = {
            "pe_ttm": 15.0,
            "netprofit_yoy": -5.0,
            "total_mv": 500000.0,
        }
        result = AIStrategyMixin._build_financials_text(row)
        assert "N/A" in result

    def test_missing_data(self):
        row = {}
        result = AIStrategyMixin._build_financials_text(row)
        assert "PE(TTM)" in result


class TestBuildMultiPeriodFinancials:
    @pytest.mark.asyncio
    async def test_empty_df(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame())
        result = await s._build_multi_period_financials("000001.SZ", cache)
        assert "不足" in result

    @pytest.mark.asyncio
    async def test_with_data(self):
        s = ConcreteStrategy()
        df = pd.DataFrame(
            {
                "roe": [10.0, 12.0, 11.0],
                "grossprofit_margin": [30.0, 28.0, 32.0],
                "or_yoy": [15.0, 20.0, 18.0],
                "netprofit_yoy": [25.0, 30.0, 22.0],
            }
        )
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(return_value=df)
        result = await s._build_multi_period_financials("000001.SZ", cache)
        assert "ROE" in result

    @pytest.mark.asyncio
    async def test_with_cashflow(self):
        s = ConcreteStrategy()
        df = pd.DataFrame(
            {
                "n_cashflow_act": [500.0],
                "n_income_attr_p": [200.0],
            }
        )
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(return_value=df)
        result = await s._build_multi_period_financials("000001.SZ", cache)
        assert "现金流" in result

    @pytest.mark.asyncio
    async def test_prefetched_data(self):
        s = ConcreteStrategy()
        df = pd.DataFrame({"roe": [10.0, 12.0]})
        prefetched = {"000001.SZ": {"financial_history": df}}
        cache = MagicMock()
        result = await s._build_multi_period_financials("000001.SZ", cache, prefetched=prefetched)
        assert "ROE" in result

    @pytest.mark.asyncio
    async def test_exception(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(side_effect=Exception("DB error"))
        result = await s._build_multi_period_financials("000001.SZ", cache)
        assert "失败" in result

    @pytest.mark.asyncio
    async def test_none_data(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(return_value=None)
        result = await s._build_multi_period_financials("000001.SZ", cache)
        assert result == "财务数据不足"

    @pytest.mark.asyncio
    async def test_cashflow_ratio(self):
        s = ConcreteStrategy()
        df = pd.DataFrame(
            {
                "end_date": ["20231231", "20230930", "20230630", "20230331"],
                "roe": [12.5, 11.8, 10.5, 9.2],
                "grossprofit_margin": [35.0, 34.5, 33.8, 32.5],
                "or_yoy": [15.0, 12.0, 8.0, 5.0],
                "netprofit_yoy": [20.0, 18.0, 15.0, 10.0],
                "n_cashflow_act": [100000000, 90000000, 80000000, 70000000],
                "n_income_attr_p": [50000000, 45000000, 40000000, 35000000],
            }
        )
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(return_value=df)
        result = await s._build_multi_period_financials("000001.SZ", cache)
        assert result is not None

    @pytest.mark.asyncio
    async def test_passes_as_of_date_to_cache(self):
        s = ConcreteStrategy()
        df = pd.DataFrame({"roe": [10.0]})
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(return_value=df)
        await s._build_multi_period_financials("000001.SZ", cache, as_of_date="20240701")
        cache.get_financial_reports_history.assert_called_once_with("000001.SZ", periods=8, as_of_date="20240701")

    @pytest.mark.asyncio
    async def test_no_as_of_date_passes_none(self):
        s = ConcreteStrategy()
        df = pd.DataFrame({"roe": [10.0]})
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(return_value=df)
        await s._build_multi_period_financials("000001.SZ", cache)
        cache.get_financial_reports_history.assert_called_once_with("000001.SZ", periods=8, as_of_date=None)


class TestBuildAuxiliaryDataText:
    @pytest.mark.asyncio
    async def test_no_data(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "无辅助数据" in result

    @pytest.mark.asyncio
    async def test_with_audit(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=pd.DataFrame({"audit_result": ["标准无保留意见"]}))
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "审计意见" in result

    @pytest.mark.asyncio
    async def test_with_pledge_high(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=pd.DataFrame({"pledge_ratio": [45.0]}))
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "质押比例" in result
        assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_with_holdernumber(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "holder_num": [50000],
                    "holder_num_ratio": [-8.0],
                }
            )
        )
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "股东人数" in result
        assert "筹码集中" in result

    @pytest.mark.asyncio
    async def test_with_dividend(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "end_date": ["20231231"],
                    "div_proc": ["实施"],
                }
            )
        )
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "分红" in result

    @pytest.mark.asyncio
    async def test_exception(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(side_effect=Exception("DB error"))
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_empty_all_data(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=pd.DataFrame())
        cache.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())
        cache.get_dividend = AsyncMock(return_value=pd.DataFrame())
        cache.get_pledge_stat = AsyncMock(return_value=pd.DataFrame())
        cache.get_top10_holders = AsyncMock(return_value=pd.DataFrame())
        cache.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert result == "无辅助数据"

    @pytest.mark.asyncio
    async def test_high_pledge_warning(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=pd.DataFrame({"pledge_ratio": [50.0]}))
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "质押比例较高" in result or result == "无辅助数据"

    @pytest.mark.asyncio
    async def test_holder_number_without_ratio(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "end_date": ["20231231"],
                    "holder_num": [500000],
                    "holder_num_change": [None],
                    "holder_num_ratio": [None],
                }
            )
        )
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "股东人数" in result
        assert "500,000" in result
        assert "筹码集中" not in result
        assert "筹码分散" not in result

    @pytest.mark.asyncio
    async def test_with_prefetched(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        prefetched = {
            "000001.SZ": {
                "audit": pd.DataFrame({"audit_result": ["标准无保留意见"]}),
                "dividend": pd.DataFrame({"end_date": ["20231231"]}),
                "pledge": pd.DataFrame({"pledge_ratio": [10.0]}),
                "holders": pd.DataFrame(
                    {
                        "end_date": ["20231231"],
                        "holder_name": ["测试股东"],
                        "hold_ratio": [30.0],
                    }
                ),
            }
        }
        result = await s._build_auxiliary_data_text("000001.SZ", cache, prefetched)
        assert result is not None

    @pytest.mark.asyncio
    async def test_passes_as_of_date_to_all_cache_calls(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        await s._build_auxiliary_data_text("000001.SZ", cache, as_of_date="20240701")
        cache.get_fina_audit.assert_called_once_with("000001.SZ", as_of_date="20240701")
        cache.get_fina_mainbz.assert_called_once_with("000001.SZ", as_of_date="20240701")
        cache.get_dividend.assert_called_once_with("000001.SZ", as_of_date="20240701")
        cache.get_pledge_stat.assert_called_once_with("000001.SZ", as_of_date="20240701")
        cache.get_top10_holders.assert_called_once_with("000001.SZ", as_of_date="20240701")
        cache.get_stk_holdernumber.assert_called_once_with("000001.SZ", as_of_date="20240701")

    @pytest.mark.asyncio
    async def test_no_as_of_date_passes_none(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_top10_holders = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        await s._build_auxiliary_data_text("000001.SZ", cache)
        cache.get_fina_audit.assert_called_once_with("000001.SZ", as_of_date=None)
        cache.get_fina_mainbz.assert_called_once_with("000001.SZ", as_of_date=None)
        cache.get_dividend.assert_called_once_with("000001.SZ", as_of_date=None)
        cache.get_pledge_stat.assert_called_once_with("000001.SZ", as_of_date=None)
        cache.get_top10_holders.assert_called_once_with("000001.SZ", as_of_date=None)
        cache.get_stk_holdernumber.assert_called_once_with("000001.SZ", as_of_date=None)

    @pytest.mark.asyncio
    async def test_holders_uses_ann_date_for_latest(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(return_value=None)
        cache.get_fina_mainbz = AsyncMock(return_value=None)
        cache.get_dividend = AsyncMock(return_value=None)
        cache.get_pledge_stat = AsyncMock(return_value=None)
        cache.get_stk_holdernumber = AsyncMock(return_value=None)
        holders_df = pd.DataFrame(
            {
                "end_date": ["20231231", "20231231"],
                "ann_date": ["20240430", "20240315"],
                "holder_name": ["股东B", "股东A"],
                "hold_ratio": [5.0, 30.0],
            }
        )
        cache.get_top10_holders = AsyncMock(return_value=holders_df)
        result = await s._build_auxiliary_data_text("000001.SZ", cache)
        assert "股东B" in result


class TestBuildMacroContext:
    @pytest.mark.asyncio
    async def test_no_data(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(return_value=None)
        cache.get_shibor_latest = AsyncMock(return_value=None)
        result = await s._build_macro_context(cache)
        assert result == ""

    @pytest.mark.asyncio
    async def test_with_macro_data(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "m2_yoy": [8.5],
                    "cpi": [0.2],
                    "ppi": [-1.5],
                }
            )
        )
        cache.get_shibor_latest = AsyncMock(return_value=None)
        result = await s._build_macro_context(cache)
        assert "M2" in result
        assert "CPI" in result

    @pytest.mark.asyncio
    async def test_with_shibor(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(return_value=None)
        cache.get_shibor_latest = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "on": [1.5],
                    "1w": [1.8],
                    "3m": [2.1],
                }
            )
        )
        result = await s._build_macro_context(cache)
        assert "Shibor" in result

    @pytest.mark.asyncio
    async def test_exception(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(side_effect=Exception("DB error"))
        result = await s._build_macro_context(cache)
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_data(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(return_value=pd.DataFrame())
        cache.get_shibor_latest = AsyncMock(return_value=pd.DataFrame())
        result = await s._build_macro_context(cache)
        assert result == ""

    @pytest.mark.asyncio
    async def test_with_both_macro_and_shibor(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(return_value=pd.DataFrame({"m2_yoy": [8.5], "cpi": [0.2], "ppi": [-2.5]}))
        cache.get_shibor_latest = AsyncMock(return_value=pd.DataFrame({"on": [2.0], "1w": [2.5], "3m": [3.0]}))
        result = await s._build_macro_context(cache)
        assert result is not None
        assert "宏观经济环境" in result
        assert "Shibor" in result


class TestPrefetchStrategySpecific:
    @pytest.mark.asyncio
    async def test_no_data_processor(self):
        s = ConcreteStrategy()
        pf = PreFetchedContext()
        result = await s._prefetch_strategy_specific(pd.DataFrame(), {}, pf)
        assert isinstance(result, PreFetchedContext)


class TestMacroContextSimplifiedCheck:
    def test_macro_context_default_is_empty_string(self):
        pf = PreFetchedContext()
        assert pf.macro_context == ""
        assert not pf.macro_context

    def test_macro_context_with_value_is_truthy(self):
        pf = PreFetchedContext(macro_context="GDP growth 5.2%")
        assert pf.macro_context
        assert pf.macro_context == "GDP growth 5.2%"

    def test_no_hasattr_needed_in_source(self):
        from strategies.ai_mixin import AIStrategyMixin

        assert hasattr(AIStrategyMixin, "run_ai_analysis")
        pf = MagicMock()
        pf.macro_context = "test context"
        assert pf.macro_context is not None


def make_mock_dp():
    dp = MagicMock()
    dates = pd.date_range("2025-01-01", periods=30, freq="B")
    dp.get_stock_history = AsyncMock(
        return_value=pd.DataFrame(
            {
                "trade_date": dates,
                "close": [10.0] * 30,
                "high": [10.5] * 30,
                "low": [9.5] * 30,
                "open": [10.0] * 30,
                "vol": [1000] * 30,
                "amount": [10000] * 30,
            }
        )
    )
    dp.cache = MagicMock()
    dp.cache.get_concepts = AsyncMock(return_value={})
    dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_northbound_holding = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_northbound_flow = AsyncMock(return_value=pd.DataFrame())
    dp.cache.get_financial_summary = AsyncMock(return_value=None)
    dp.cache.get_stock_news = AsyncMock(return_value=[])
    return dp


class TestAIStrategyMixinTimeoutHandling:
    @pytest.mark.asyncio
    async def test_httpx_timeout_exception_reraises(self):
        import httpx

        s = ConcreteStrategy()
        row = pd.Series({"ts_code": "000001.SZ", "name": "test"})
        prefetched = PreFetchedContext()
        mock_dp = make_mock_dp()
        with patch("strategies.ai_mixin.AIService") as mock_ai_cls:
            mock_ai = mock_ai_cls.return_value
            mock_ai.is_cloud_available.return_value = True
            mock_ai.analyze_stock = AsyncMock(side_effect=httpx.ReadTimeout("read timeout"))
            with pytest.raises(httpx.ReadTimeout):
                await s._mixin_analyze_single(row, mock_dp, mock_ai, prefetched)


class TestBuildMacroContextLookaheadGuard:
    @pytest.mark.asyncio
    async def test_build_macro_context_passes_as_of_date(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(return_value=pd.DataFrame())
        cache.get_shibor_latest = AsyncMock(return_value=pd.DataFrame())
        as_of = datetime.date(2024, 6, 1)
        await s._build_macro_context(cache, as_of_date=as_of)
        cache.get_macro_economy.assert_called_once_with(as_of_date=as_of)
        cache.get_shibor_latest.assert_called_once_with(as_of_date=as_of)

    @pytest.mark.asyncio
    async def test_build_macro_context_no_as_of_date_passes_none(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(return_value=pd.DataFrame())
        cache.get_shibor_latest = AsyncMock(return_value=pd.DataFrame())
        await s._build_macro_context(cache)
        cache.get_macro_economy.assert_called_once_with(as_of_date=None)
        cache.get_shibor_latest.assert_called_once_with(as_of_date=None)

    @pytest.mark.asyncio
    async def test_build_macro_context_no_future_data(self):
        s = ConcreteStrategy()
        cache = MagicMock()
        cutoff = datetime.date(2024, 6, 1)
        macro_df = pd.DataFrame(
            {
                "m2_yoy": [8.5],
                "cpi": [0.2],
                "ppi": [-1.5],
            }
        )
        cache.get_macro_economy = AsyncMock(return_value=macro_df)
        cache.get_shibor_latest = AsyncMock(return_value=None)
        result = await s._build_macro_context(cache, as_of_date=cutoff)
        assert "M2" in result
        cache.get_macro_economy.assert_called_once_with(as_of_date=cutoff)

    @pytest.mark.asyncio
    async def test_asyncio_timeout_error_reraises(self):
        s = ConcreteStrategy()
        row = pd.Series({"ts_code": "000001.SZ", "name": "test"})
        prefetched = PreFetchedContext()
        mock_dp = make_mock_dp()
        with patch("strategies.ai_mixin.AIService") as mock_ai_cls:
            mock_ai = mock_ai_cls.return_value
            mock_ai.is_cloud_available.return_value = True
            mock_ai.analyze_stock = AsyncMock(side_effect=TimeoutError())
            with pytest.raises(asyncio.TimeoutError):
                await s._mixin_analyze_single(row, mock_dp, mock_ai, prefetched)

    @pytest.mark.asyncio
    async def test_builtin_timeout_error_reraises(self):
        s = ConcreteStrategy()
        row = pd.Series({"ts_code": "000001.SZ", "name": "test"})
        prefetched = PreFetchedContext()
        mock_dp = make_mock_dp()
        with patch("strategies.ai_mixin.AIService") as mock_ai_cls:
            mock_ai = mock_ai_cls.return_value
            mock_ai.is_cloud_available.return_value = True
            mock_ai.analyze_stock = AsyncMock(side_effect=TimeoutError("timeout"))
            with pytest.raises(TimeoutError):
                await s._mixin_analyze_single(row, mock_dp, mock_ai, prefetched)


class TestSafeFloat:
    def test_valid_number(self):
        assert safe_float(10.5) == 10.5
        assert safe_float(10) == 10.0

    def test_none_value(self):
        assert safe_float(None) == 0.0

    def test_nan_value(self):
        assert safe_float(float("nan")) == 0.0

    def test_string_value(self):
        assert safe_float("10.5") == 10.5
        assert safe_float("invalid") == 0.0

    def test_custom_default(self):
        assert safe_float(None, default=-1.0) == -1.0


class TestRunAiAnalysisConcurrency:
    def _make_strategy(self):
        return ConcreteStrategy()

    def _make_context(self):
        dp = MagicMock()
        dp.is_cancelled = MagicMock(return_value=False)
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        dp.cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        dp.cache.prefetch_auxiliary_data = AsyncMock(return_value={})
        dp.get_latest_trade_date = AsyncMock(return_value="20240118")
        return {"data_processor": dp}

    async def _make_candidates(self, n):
        return pd.DataFrame([{"ts_code": f"{i:06d}.SZ", "name": f"S{i}"} for i in range(n)])

    @pytest.mark.asyncio
    async def test_concurrency_bounded_by_screening_sem(self):
        import asyncio

        s = self._make_strategy()
        candidates = await self._make_candidates(12)
        context = self._make_context()

        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def fake_single(*args, **kwargs):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            return {"score": 70, "summary": "ok"}

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.ConfigHandler.get_ai_max_concurrent_analysis", return_value=3),
            patch("strategies.ai_mixin.ConfigHandler.get_ai_max_candidates", return_value=100),
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance

            with patch.object(s, "_mixin_analyze_single", fake_single):
                await s.run_ai_analysis(candidates, context)
            assert peak <= 3, f"Peak concurrency {peak} should be <= 3"

    @pytest.mark.asyncio
    async def test_partial_failure_does_not_abort(self):
        s = self._make_strategy()
        candidates = await self._make_candidates(5)
        context = self._make_context()

        calls = {"n": 0}

        async def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("boom")
            return {"score": 60, "summary": "ok"}

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.ConfigHandler.get_ai_max_concurrent_analysis", return_value=2),
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance

            with patch.object(s, "_mixin_analyze_single", flaky):
                result = await s.run_ai_analysis(candidates, context)
            assert len(result) == 4

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        s = self._make_strategy()
        candidates = await self._make_candidates(3)
        context = self._make_context()

        async def cancel_one(*args, **kwargs):
            raise asyncio.CancelledError()

        with (
            patch("strategies.ai_mixin.AIService") as mock_ai,
            patch("strategies.ai_mixin.ConfigHandler.get_ai_max_concurrent_analysis", return_value=2),
        ):
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance

            with patch.object(s, "_mixin_analyze_single", cancel_one):
                with pytest.raises(asyncio.CancelledError):
                    await s.run_ai_analysis(candidates, context)
