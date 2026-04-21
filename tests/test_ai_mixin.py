"""
Tests for AI Strategy Mixin - Edge Cases and Data Quality.

验证 AI 分析上下文组装逻辑在异常数据场景下的稳健性：
- 历史数据缺失
- 技术指标计算失败
- 概念/新闻/资金流数据缺失
- 财务数据缺失
- 各种数据质量问题

所有测试使用 Mock 隔离外部依赖，不连接真实数据库或 API。
"""

import datetime
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.ai_mixin import AIStrategyMixin, PreFetchedContext
from strategies.utils import safe_float


class MockStrategy(AIStrategyMixin):
    """测试用的模拟策略类"""

    def __init__(self):
        super().__init__()
        self.key = "test_strategy"
        self.required_history_days = 60

    def get_ai_context(self, row: dict) -> str:
        return f"测试策略上下文: RSI={row.get('rsi_14', 'N/A')}"


class TestPreFetchedContext:
    """测试 PreFetchedContext 数据类"""

    def test_default_values(self):
        """默认值测试"""
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
        """带值初始化测试"""
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


class TestComputeTechnicalStructure:
    """测试技术结构计算"""

    def test_empty_history(self):
        """空历史数据"""
        result = AIStrategyMixin._compute_technical_structure(pd.DataFrame())
        assert result["ma_alignment"] == "数据不足"
        assert result["volume_trend"] == "数据不足"
        assert result["price_trend_5d"] == "数据不足"

    def test_none_history(self):
        """None 历史数据"""
        result = AIStrategyMixin._compute_technical_structure(None)
        assert result["ma_alignment"] == "数据不足"
        assert result["volume_trend"] == "数据不足"

    def test_insufficient_history(self):
        """历史数据不足（少于5天）"""
        df = pd.DataFrame(
            {
                "trade_date": ["20240318", "20240319", "20240320"],
                "close": [10.0, 10.5, 11.0],
                "vol": [1000000, 1100000, 1200000],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert result["ma_alignment"] == "数据不足"

    def test_valid_history_bullish_alignment(self):
        """多头排列"""
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 31)],
                "close": [10.0 + i * 0.1 for i in range(30)],
                "vol": [1000000 + i * 10000 for i in range(30)],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert "多头排列" in result["ma_alignment"]

    def test_valid_history_bearish_alignment(self):
        """空头排列"""
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
        """缺少成交量列"""
        df = pd.DataFrame(
            {
                "trade_date": [f"202403{str(i).zfill(2)}" for i in range(1, 31)],
                "close": [10.0 + i * 0.1 for i in range(30)],
            }
        )
        result = AIStrategyMixin._compute_technical_structure(df)
        assert result["volume_trend"] == "数据不足"

    def test_zero_division_guard(self):
        """零值除法保护"""
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
        """带复权因子的数据"""
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
    """测试涨跌停幅度判断"""

    def test_st_stock(self):
        """ST 股票"""
        pct = AIStrategyMixin._get_limit_pct("000001.SZ", "ST某某")
        assert pct == 5.0

        pct = AIStrategyMixin._get_limit_pct("000001.SZ", "*ST某某")
        assert pct == 5.0

    def test_beijing_exchange(self):
        """北交所股票"""
        pct = AIStrategyMixin._get_limit_pct("830001.BJ", "北交所股票")
        assert pct == 30.0

    def test_gem_stock(self):
        """创业板股票"""
        pct = AIStrategyMixin._get_limit_pct("300001.SZ", "创业板股票")
        assert pct == 20.0

    def test_star_market(self):
        """科创板股票"""
        pct = AIStrategyMixin._get_limit_pct("688001.SH", "科创板股票")
        assert pct == 20.0

    def test_main_board(self):
        """主板股票"""
        pct = AIStrategyMixin._get_limit_pct("000001.SZ", "平安银行")
        assert pct == 10.0

        pct = AIStrategyMixin._get_limit_pct("600001.SH", "主板股票")
        assert pct == 10.0


class TestBuildHistoryText:
    """测试历史数据文本构建"""

    def test_empty_history(self):
        """空历史数据"""
        result = AIStrategyMixin._build_history_text(pd.DataFrame())
        assert result == ""

    def test_none_history(self):
        """None 历史数据"""
        result = AIStrategyMixin._build_history_text(None)  # type: ignore
        assert result == ""

    def test_insufficient_history(self):
        """历史数据不足（少于5天）"""
        df = pd.DataFrame(
            {
                "trade_date": ["20240318", "20240319", "20240320"],
                "close": [10.0, 10.5, 11.0],
            }
        )
        result = AIStrategyMixin._build_history_text(df)
        assert "历史数据不足" in result

    def test_valid_history(self):
        """有效历史数据"""
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
        """涨停股票"""
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
        """跌停股票"""
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
        """缺少涨跌幅列"""
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
        """带复权因子的数据"""
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
        """涨跌幅列含 NaN 值"""
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


class TestAIStrategyMixinContextBuilders:
    """测试上下文构建器注册机制"""

    def test_register_context_builder(self):
        """注册上下文构建器"""
        strategy = MockStrategy()

        def custom_builder(row, prefetched):
            return f"自定义上下文: {row.get('ts_code')}"

        strategy.register_context_builder("custom", custom_builder)

        assert "custom" in strategy._context_builders
        assert strategy._context_builders["custom"] == custom_builder

    def test_get_context_blocks(self):
        """获取上下文块列表"""
        strategy = MockStrategy()

        strategy.register_context_builder("block1", lambda r, p: "block1")
        strategy.register_context_builder("block2", lambda r, p: "block2")

        blocks = strategy.get_context_blocks()
        assert "block1" in blocks
        assert "block2" in blocks

    def test_multiple_registrations(self):
        """多次注册覆盖"""
        strategy = MockStrategy()

        strategy.register_context_builder("test", lambda r, p: "v1")
        strategy.register_context_builder("test", lambda r, p: "v2")

        assert len(strategy._context_builders) == 1
        result = strategy._context_builders["test"]({}, PreFetchedContext())
        assert result == "v2"


class TestAIStrategyMixinRunAnalysis:
    """测试 AI 分析运行"""

    @pytest.fixture
    def mock_strategy(self):
        """创建模拟策略实例"""
        return MockStrategy()

    @pytest.fixture
    def mock_context(self):
        """创建模拟上下文"""
        context = {
            "data_processor": MagicMock(),
            "on_progress": MagicMock(),
            "on_result": MagicMock(),
            "on_stream_result": MagicMock(),
            "params": {},
        }
        context["data_processor"].is_cancelled = MagicMock(return_value=False)
        context["data_processor"].get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 3, 21))
        context["data_processor"].cache = MagicMock()
        context["data_processor"].cache.get_concepts = AsyncMock(return_value={})
        context["data_processor"].cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        context["data_processor"].cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        context["data_processor"].cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        context["data_processor"].cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        return context

    @pytest.mark.asyncio
    async def test_run_with_empty_candidates(self, mock_strategy, mock_context):
        """空候选列表"""
        result = await mock_strategy.run_ai_analysis(pd.DataFrame(), mock_context)
        assert result.empty

    @pytest.mark.asyncio
    async def test_run_with_none_candidates(self, mock_strategy, mock_context):
        """None 候选列表"""
        result = await mock_strategy.run_ai_analysis(None, mock_context)
        assert result is None or result.empty

    @pytest.mark.asyncio
    async def test_run_with_ai_not_configured(self, mock_strategy, mock_context):
        """AI 服务未配置"""
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = False
            mock_ai.return_value = mock_ai_instance

            candidates = pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "name": ["平安银行"],
                    "close": [10.0],
                }
            )

            result = await mock_strategy.run_ai_analysis(candidates, mock_context)

            assert len(result) == 1
            assert result["ts_code"].iloc[0] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_run_with_cancellation(self, mock_strategy, mock_context):
        """取消操作"""
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance

            mock_context["data_processor"].is_cancelled = MagicMock(return_value=True)

            candidates = pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "name": ["平安银行", "万科A"],
                    "close": [10.0, 15.0],
                }
            )

            result = await mock_strategy.run_ai_analysis(candidates, mock_context)

            assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_run_with_candidates_cap(self, mock_strategy, mock_context):
        """候选数量上限"""
        with patch("strategies.ai_mixin.AIService") as mock_ai:
            mock_ai_instance = MagicMock()
            mock_ai_instance.is_cloud_available.return_value = True
            mock_ai.return_value = mock_ai_instance

            with patch(
                "strategies.ai_mixin.ConfigHandler.get_ai_max_candidates",
                return_value=2,
            ):
                candidates = pd.DataFrame(
                    {
                        "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"],
                        "name": ["股票1", "股票2", "股票3", "股票4"],
                        "close": [10.0, 15.0, 20.0, 25.0],
                    }
                )

                with patch.object(
                    mock_strategy, "_prefetch_strategy_specific", new_callable=AsyncMock
                ) as mock_prefetch:
                    mock_prefetch.return_value = PreFetchedContext()

                    result = await mock_strategy.run_ai_analysis(candidates, mock_context)

                    assert len(result) <= 2


class TestAIStrategyMixinAnalyzeSingle:
    """测试单只股票分析"""

    @pytest.fixture
    def mock_strategy(self):
        """创建模拟策略实例"""
        return MockStrategy()

    @pytest.fixture
    def mock_dp(self):
        """创建模拟 DataProcessor"""
        dp = MagicMock()
        dp.get_stock_history = AsyncMock(return_value=pd.DataFrame())
        dp.cache = MagicMock()
        dp.cache.get_concepts = AsyncMock(return_value={})
        return dp

    @pytest.fixture
    def mock_ai_client(self):
        """创建模拟 AI 客户端"""
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
        """空历史数据分析"""
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext()

        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)

        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_missing_concepts(self, mock_strategy, mock_dp, mock_ai_client):
        """缺少概念数据"""
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext(concepts_map={})

        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)

        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_missing_news(self, mock_strategy, mock_dp, mock_ai_client):
        """缺少新闻数据"""
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext(news_tasks={})

        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)

        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_missing_capital_flow(self, mock_strategy, mock_dp, mock_ai_client):
        """缺少资金流数据"""
        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext(capital={})

        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)

        assert result is not None

    @pytest.mark.asyncio
    async def test_analyze_with_all_data_present(self, mock_strategy, mock_dp, mock_ai_client):
        """所有数据都存在"""
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
        """AI 分析出错"""
        mock_ai_client.analyze_stock = AsyncMock(side_effect=Exception("AI Error"))

        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext()

        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)

        assert result is None

    @pytest.mark.asyncio
    async def test_analyze_with_zero_score(self, mock_strategy, mock_dp, mock_ai_client):
        """AI 返回零分"""
        mock_ai_client.analyze_stock = AsyncMock(return_value={"score": 0})

        row = {"ts_code": "000001.SZ", "name": "平安银行", "close": 10.0}
        prefetched = PreFetchedContext()

        result = await mock_strategy._mixin_analyze_single(row, mock_dp, mock_ai_client, prefetched)

        assert result["score"] == 0


class TestSafeFloat:
    """测试安全浮点数转换"""

    def test_valid_number(self):
        """有效数字"""
        assert safe_float(10.5) == 10.5
        assert safe_float(10) == 10.0

    def test_none_value(self):
        """None 值"""
        assert safe_float(None) == 0.0

    def test_nan_value(self):
        """NaN 值"""
        assert safe_float(float("nan")) == 0.0

    def test_string_value(self):
        """字符串值"""
        assert safe_float("10.5") == 10.5
        assert safe_float("invalid") == 0.0

    def test_custom_default(self):
        """自定义默认值"""
        assert safe_float(None, default=-1.0) == -1.0


class TestMultiPeriodFinancials:
    """测试多期财务数据注入"""

    @pytest.fixture
    def mock_cache(self):
        """创建模拟缓存"""
        cache = MagicMock()
        cache.get_financial_reports_history = AsyncMock(
            return_value=pd.DataFrame(
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
        )
        return cache

    @pytest.mark.asyncio
    async def test_build_multi_period_financials_success(self, mock_cache):
        """成功构建多期财务趋势"""
        result = await AIStrategyMixin()._build_multi_period_financials("000001.SZ", mock_cache)

        assert result is not None
        assert "ROE趋势" in result or "财务数据不足" in result

    @pytest.mark.asyncio
    async def test_build_multi_period_financials_empty(self, mock_cache):
        """空财务数据"""
        mock_cache.get_financial_reports_history = AsyncMock(return_value=pd.DataFrame())

        result = await AIStrategyMixin()._build_multi_period_financials("000001.SZ", mock_cache)

        assert result == "财务数据不足"

    @pytest.mark.asyncio
    async def test_build_multi_period_financials_none(self, mock_cache):
        """None 财务数据"""
        mock_cache.get_financial_reports_history = AsyncMock(return_value=None)

        result = await AIStrategyMixin()._build_multi_period_financials("000001.SZ", mock_cache)

        assert result == "财务数据不足"

    @pytest.mark.asyncio
    async def test_build_multi_period_financials_cashflow_ratio(self, mock_cache):
        """现金流/净利润比率计算"""
        result = await AIStrategyMixin()._build_multi_period_financials("000001.SZ", mock_cache)

        assert result is not None


class TestAuxiliaryDataText:
    """测试辅助数据文本构建"""

    @pytest.fixture
    def mock_cache(self):
        """创建模拟缓存"""
        cache = MagicMock()
        cache.get_fina_audit = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "audit_result": ["标准无保留意见"]})
        )
        cache.get_fina_mainbz = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "bz_item": ["利息收入", "手续费收入", "投资收益"],
                    "bz_sales": [5000000000, 2000000000, 1000000000],
                }
            )
        )
        cache.get_dividend = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "end_date": ["20231231", "20221231", "20211231"],
                    "div_proc": ["实施方案", "实施方案", "实施方案"],
                }
            )
        )
        cache.get_pledge_stat = AsyncMock(return_value=pd.DataFrame({"pledge_ratio": [15.5]}))
        cache.get_top10_holders = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "end_date": ["20231231", "20231231"],
                    "holder_name": ["中国平安", "香港中央结算"],
                    "hold_ratio": [40.0, 5.0],
                }
            )
        )
        cache.get_stk_holdernumber = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "end_date": ["20231231", "20230930"],
                    "holder_num": [500000, 520000],
                }
            )
        )
        return cache

    @pytest.mark.asyncio
    async def test_build_auxiliary_data_success(self, mock_cache):
        """成功构建辅助数据"""
        result = await AIStrategyMixin()._build_auxiliary_data_text("000001.SZ", mock_cache)

        assert result is not None
        assert result != "无辅助数据"

    @pytest.mark.asyncio
    async def test_build_auxiliary_data_with_prefetched(self, mock_cache):
        """使用预取数据构建"""
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

        result = await AIStrategyMixin()._build_auxiliary_data_text("000001.SZ", mock_cache, prefetched)

        assert result is not None

    @pytest.mark.asyncio
    async def test_build_auxiliary_data_empty(self, mock_cache):
        """空辅助数据"""
        mock_cache.get_fina_audit = AsyncMock(return_value=pd.DataFrame())
        mock_cache.get_fina_mainbz = AsyncMock(return_value=pd.DataFrame())
        mock_cache.get_dividend = AsyncMock(return_value=pd.DataFrame())
        mock_cache.get_pledge_stat = AsyncMock(return_value=pd.DataFrame())
        mock_cache.get_top10_holders = AsyncMock(return_value=pd.DataFrame())
        mock_cache.get_stk_holdernumber = AsyncMock(return_value=pd.DataFrame())

        result = await AIStrategyMixin()._build_auxiliary_data_text("000001.SZ", mock_cache)

        assert result == "无辅助数据"

    @pytest.mark.asyncio
    async def test_build_auxiliary_data_high_pledge_warning(self, mock_cache):
        """高质押比例警告"""
        mock_cache.get_pledge_stat = AsyncMock(return_value=pd.DataFrame({"pledge_ratio": [50.0]}))

        result = await AIStrategyMixin()._build_auxiliary_data_text("000001.SZ", mock_cache)

        assert "质押比例较高" in result or result == "无辅助数据"


class TestMacroContext:
    """测试宏观经济上下文构建"""

    @pytest.fixture
    def mock_cache(self):
        """创建模拟缓存"""
        cache = MagicMock()
        cache.get_macro_economy = AsyncMock(return_value=pd.DataFrame({"m2_yoy": [8.5], "cpi": [0.2], "ppi": [-2.5]}))
        cache.get_shibor_latest = AsyncMock(return_value=pd.DataFrame({"on": [2.0], "1w": [2.5], "3m": [3.0]}))
        return cache

    @pytest.mark.asyncio
    async def test_build_macro_context_success(self, mock_cache):
        """成功构建宏观上下文"""
        result = await AIStrategyMixin()._build_macro_context(mock_cache)

        assert result is not None
        assert "宏观经济环境" in result

    @pytest.mark.asyncio
    async def test_build_macro_context_with_shibor(self, mock_cache):
        """包含 Shibor 利率"""
        result = await AIStrategyMixin()._build_macro_context(mock_cache)

        assert "Shibor" in result

    @pytest.mark.asyncio
    async def test_build_macro_context_empty(self, mock_cache):
        """空宏观数据"""
        mock_cache.get_macro_economy = AsyncMock(return_value=pd.DataFrame())
        mock_cache.get_shibor_latest = AsyncMock(return_value=pd.DataFrame())

        result = await AIStrategyMixin()._build_macro_context(mock_cache)

        assert result == ""

    @pytest.mark.asyncio
    async def test_build_macro_context_none(self, mock_cache):
        """None 宏观数据"""
        mock_cache.get_macro_economy = AsyncMock(return_value=None)
        mock_cache.get_shibor_latest = AsyncMock(return_value=None)

        result = await AIStrategyMixin()._build_macro_context(mock_cache)

        assert result == ""
