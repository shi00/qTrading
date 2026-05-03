import pytest
import datetime
from unittest.mock import patch, MagicMock
import pandas as pd

from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext


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
        assert ctx.auxiliary_data == {}


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


class TestComputeTechnicalStructure:
    def test_empty_df(self):
        result = AIStrategyMixin._compute_technical_structure(pd.DataFrame(), 1.5)
        assert result["ma_alignment"] == "数据不足"

    def test_insufficient_data(self):
        df = pd.DataFrame(
            {
                "trade_date": ["20240610"],
                "close": [10.0],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df, 1.5)
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


class TestGetLimitPct:
    def test_st_stock(self):
        assert AIStrategyMixin._get_limit_pct("000001.SZ", "ST某某") == 5.0

    def test_bse_stock(self):
        assert AIStrategyMixin._get_limit_pct("830001.BJ") == 30.0

    def test_gem_stock(self):
        assert AIStrategyMixin._get_limit_pct("300001.SZ") == 20.0

    def test_star_stock(self):
        assert AIStrategyMixin._get_limit_pct("688001.SH") == 20.0

    def test_main_board(self):
        assert AIStrategyMixin._get_limit_pct("000001.SZ") == 10.0


class TestBuildHistoryText:
    def test_empty_df(self):
        result = AIStrategyMixin._build_history_text(pd.DataFrame())
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
