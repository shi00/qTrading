import pytest
import datetime
from unittest.mock import patch, MagicMock, AsyncMock
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
        import inspect
        from strategies.ai_mixin import AIStrategyMixin

        source = inspect.getsource(AIStrategyMixin.run_ai_analysis)
        assert "hasattr" not in source or "macro_context" not in source.split("hasattr")[0].split("\n")[-1]
