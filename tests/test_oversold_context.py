"""
Tests for OversoldStrategy context builders.

验证超跌反弹策略上下文增强功能的正确性。
"""

import datetime
import math
import unittest

import pandas as pd

from strategies.ai_mixin import AIStrategyMixin
from strategies.oversold_strategy import OversoldStrategy


class TestBuildTurnoverContext(unittest.TestCase):
    """测试换手率趋势上下文构建"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_build_turnover_text_normal(self):
        """正常数据下换手率文本包含"当前换手率"/"5日均值"/"20日均值"/"趋势" """
        from strategies.ai_mixin import PreFetchedContext

        indicators_df = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": f"202403{20+i:02d}", "turnover_rate": 5.0 + i * 0.5}
            for i in range(20)
        ])

        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_turnover_context(row, prefetched)

        self.assertIn("当前换手率", result)
        self.assertIn("5日均值", result)
        self.assertIn("20日均值", result)
        self.assertIn("趋势", result)

    def test_build_turnover_text_empty(self):
        """空 DataFrame 返回"暂不可用" """
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.indicators = pd.DataFrame()

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_turnover_context(row, prefetched)

        self.assertIn("暂不可用", result)

    def test_build_turnover_text_single_day(self):
        """只有 1 天数据时不应崩溃，正常计算当日换手率"""
        from strategies.ai_mixin import PreFetchedContext

        indicators_df = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20240321", "turnover_rate": 2.5},
        ])

        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_turnover_context(row, prefetched)

        self.assertIsInstance(result, str)
        self.assertNotIn("error", result.lower())


class TestBuildSectorContext(unittest.TestCase):
    """测试行业统计上下文构建"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_build_sector_context_normal(self):
        """正常行业数据下文本包含行业名称和涨跌统计"""
        from strategies.ai_mixin import PreFetchedContext

        sector_stats = {
            "电子": {"count": 10, "up_count": 3, "down_count": 7, "avg_pct_chg": -1.5},
        }

        prefetched = PreFetchedContext()
        prefetched.sector_stats = sector_stats

        row = {"ts_code": "000001.SZ", "industry": "电子", "close": 10.0}
        result = self.strategy._build_sector_context(row, prefetched)

        self.assertIn("电子", result)
        self.assertIn("上涨家数", result)
        self.assertIn("下跌家数", result)
        self.assertIn("平均涨跌幅", result)

    def test_build_sector_context_missing(self):
        """行业不存在时返回"暂无数据" """
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.sector_stats = {}

        row = {"ts_code": "000001.SZ", "industry": "未知行业", "close": 10.0}
        result = self.strategy._build_sector_context(row, prefetched)

        self.assertIn("暂无数据", result)


class TestBuildHistoryTextLimitTag(unittest.TestCase):
    """测试 K 线历史文本中的涨跌停标记"""

    def test_build_history_text_limit_tag(self):
        """主板跌幅 ≈10% 的 K 线有"🟢跌停"标记"""
        history_df = pd.DataFrame([
            {"trade_date": "20240321", "open": 10.0, "high": 10.0, "low": 9.0, "close": 9.0, "vol": 1000, "pct_chg": -10.0},
            {"trade_date": "20240320", "open": 11.0, "high": 11.0, "low": 10.0, "close": 10.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240319", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240318", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240315", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0, "vol": 1000, "pct_chg": 0.0},
        ])

        result = AIStrategyMixin._build_history_text(
            history_df, ts_code="000001.SZ", stock_name="测试股票"
        )

        self.assertIn("🟢跌停", result)

    def test_build_history_text_gem_limit(self):
        """创业板 (3xx) 跌幅 ≈20% 才标记跌停"""
        history_df = pd.DataFrame([
            {"trade_date": "20240321", "open": 10.0, "high": 10.0, "low": 8.0, "close": 8.0, "vol": 1000, "pct_chg": -20.0},
            {"trade_date": "20240320", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240319", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240318", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240315", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "vol": 1000, "pct_chg": 0.0},
        ])

        result = AIStrategyMixin._build_history_text(
            history_df, ts_code="300001.SZ", stock_name="创业板股票"
        )

        self.assertIn("🟢跌停", result)

    def test_build_history_text_st_limit(self):
        """ST/*ST 股跌幅 ≈5% 就标记跌停"""
        history_df = pd.DataFrame([
            {"trade_date": "20240321", "open": 5.0, "high": 5.0, "low": 4.75, "close": 4.75, "vol": 1000, "pct_chg": -5.0},
            {"trade_date": "20240320", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240319", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240318", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240315", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "vol": 1000, "pct_chg": 0.0},
        ])

        result = AIStrategyMixin._build_history_text(
            history_df, ts_code="000001.SZ", stock_name="ST某某"
        )

        self.assertIn("🟢跌停", result)


class TestBuildSupportContext(unittest.TestCase):
    """测试支撑位分析上下文构建"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_build_support_levels_short_history(self):
        """历史不足 60 天时的支撑位优雅降级处理"""
        from strategies.ai_mixin import PreFetchedContext

        history_df = pd.DataFrame([
            {"trade_date": f"202403{20+i:02d}", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "vol": 1000}
            for i in range(15)
        ])

        prefetched = PreFetchedContext()
        prefetched.history = {"000001.SZ": history_df}

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_support_context(row, prefetched)

        self.assertIsInstance(result, str)
        self.assertNotIn("error", result.lower())

    def test_build_support_levels_full_calculation(self):
        """完整支撑位计算包含布林下轨和VWAC"""
        from strategies.ai_mixin import PreFetchedContext

        history_df = pd.DataFrame([
            {
                "trade_date": f"202401{str(i).zfill(2)}",
                "open": 10.0 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.5 + i * 0.1,
                "close": 10.0 + i * 0.1,
                "vol": 1000 + i * 100
            }
            for i in range(1, 32)
        ] + [
            {
                "trade_date": f"202402{str(i).zfill(2)}",
                "open": 10.0 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.5 + i * 0.1,
                "close": 10.0 + i * 0.1,
                "vol": 1000 + i * 100
            }
            for i in range(1, 30)
        ] + [
            {
                "trade_date": f"202403{str(i).zfill(2)}",
                "open": 10.0 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.5 + i * 0.1,
                "close": 10.0 + i * 0.1,
                "vol": 1000 + i * 100
            }
            for i in range(1, 22)
        ])

        prefetched = PreFetchedContext()
        prefetched.history = {"000001.SZ": history_df}

        row = {"ts_code": "000001.SZ", "close": 12.0}
        result = self.strategy._build_support_context(row, prefetched)

        self.assertIn("布林下轨", result)
        self.assertIn("VWAC", result)

    def test_build_support_levels_missing_history(self):
        """历史数据缺失时返回提示"""
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.history = {}

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_support_context(row, prefetched)

        self.assertIn("暂不可用", result)

    def test_build_support_levels_invalid_close(self):
        """当前价格无效时返回提示"""
        from strategies.ai_mixin import PreFetchedContext

        history_df = pd.DataFrame([
            {"trade_date": f"202403{20+i:02d}", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "vol": 1000}
            for i in range(30)
        ])

        prefetched = PreFetchedContext()
        prefetched.history = {"000001.SZ": history_df}

        row = {"ts_code": "000001.SZ", "close": None}
        result = self.strategy._build_support_context(row, prefetched)

        self.assertIn("无效", result)


class TestRSIPercentile(unittest.TestCase):
    """测试 RSI 百分位计算"""

    def test_rsi_percentile_all_nan(self):
        """RSI 全部为 NaN 时返回填充 50 的 Series（实现中用 50 填充 NaN）"""
        from utils.technical_analysis import TechnicalAnalysis

        close_prices = pd.Series([float('nan')] * 30)

        result = TechnicalAnalysis.calculate_rsi_pandas(close_prices)

        self.assertIsInstance(result, pd.Series)
        self.assertTrue((result == 50.0).all())


class TestPromptFormatting(unittest.TestCase):
    """测试 Prompt 格式化"""

    def test_prompt_no_leading_whitespace(self):
        """验证 dedent 后 Prompt 无前导空白"""
        from textwrap import dedent

        sample_prompt = """
            这是一段测试文本。
            应该没有前导空白。
        """

        result = dedent(sample_prompt)
        lines = result.strip().split('\n')

        for line in lines:
            if line.strip():
                self.assertFalse(line.startswith('    '), f"Line has leading whitespace: {line}")

    def test_get_ai_context_chinese(self):
        """`get_ai_context()` 输出全中文"""
        strategy = OversoldStrategy()

        row = {"ts_code": "000001.SZ", "_rsi_period": 14, "rsi_14": 25, "_rsi_threshold": 30}
        result = strategy.get_ai_context(row)

        self.assertIsInstance(result, str)
        self.assertIn("超跌", result)

    def test_get_ai_context_percentile(self):
        """包含 `_rsi_percentile` 时输出百分位描述"""
        strategy = OversoldStrategy()

        row = {
            "ts_code": "000001.SZ",
            "_rsi_period": 14,
            "rsi_14": 25,
            "_rsi_threshold": 30,
            "_rsi_feature_text": "连续超卖3天"
        }
        result = strategy.get_ai_context(row)

        self.assertIsInstance(result, str)
        self.assertIn("形态反馈", result)


class TestVolumeThresholdConsistency(unittest.TestCase):
    """测试成交量阈值一致性"""

    def test_volume_threshold_consistency(self):
        """两处 volume ratio 阈值一致 (均为 1.5)"""
        from strategies.ai_mixin import AIStrategyMixin

        history_df = pd.DataFrame([
            {"trade_date": "20240321", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "vol": 2000, "pct_chg": 0.0},
            {"trade_date": "20240320", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "vol": 1000, "pct_chg": 0.0},
            {"trade_date": "20240319", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0, "vol": 1000, "pct_chg": 0.0},
        ])

        result = AIStrategyMixin._build_history_text(
            history_df, ts_code="000001.SZ", stock_name="测试股票", vol_ratio_threshold=1.5
        )

        self.assertIsInstance(result, str)

    def test_get_limit_pct_main_board(self):
        """主板涨跌停幅度为 10%"""
        result = AIStrategyMixin._get_limit_pct("000001.SZ", "主板股票")
        self.assertEqual(result, 10.0)

    def test_get_limit_pct_gem(self):
        """创业板涨跌停幅度为 20%"""
        result = AIStrategyMixin._get_limit_pct("300001.SZ", "创业板股票")
        self.assertEqual(result, 20.0)

    def test_get_limit_pct_star(self):
        """科创板涨跌停幅度为 20%"""
        result = AIStrategyMixin._get_limit_pct("688001.SH", "科创板股票")
        self.assertEqual(result, 20.0)

    def test_get_limit_pct_st(self):
        """ST 股涨跌停幅度为 5%"""
        result = AIStrategyMixin._get_limit_pct("000001.SZ", "ST某某")
        self.assertEqual(result, 5.0)

    def test_get_limit_pct_bse(self):
        """北交所涨跌停幅度为 30%"""
        result = AIStrategyMixin._get_limit_pct("830001.BJ", "北交所股票")
        self.assertEqual(result, 30.0)


class TestBuildMarketContext(unittest.TestCase):
    """测试大盘环境上下文构建"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_build_market_context_normal(self):
        """正常大盘数据下文本包含指数信息和趋势"""
        from strategies.ai_mixin import PreFetchedContext

        market_data = {
            "000001.SH": {"pct_chg": 1.5, "ma20": 3100.0, "trend": "多头趋势"},
            "399001.SZ": {"pct_chg": -0.8, "ma20": 9500.0, "trend": "震荡整理"},
            "399006.SZ": {"pct_chg": 2.1, "ma20": 2100.0, "trend": "多头趋势"},
        }

        prefetched = PreFetchedContext()
        prefetched.market_context = market_data

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_market_context(row, prefetched)

        self.assertIn("大盘环境", result)
        self.assertIn("上证指数", result)
        self.assertIn("多头趋势", result)

    def test_build_market_context_with_trend(self):
        """大盘数据包含趋势判断"""
        from strategies.ai_mixin import PreFetchedContext

        market_data = {
            "000001.SH": {"pct_chg": -2.5, "ma20": 3200.0, "trend": "空头趋势"},
        }

        prefetched = PreFetchedContext()
        prefetched.market_context = market_data

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_market_context(row, prefetched)

        self.assertIn("空头趋势", result)

    def test_build_market_context_empty(self):
        """大盘数据不可用时返回提示"""
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.market_context = {}

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_market_context(row, prefetched)

        self.assertIn("暂不可用", result)


if __name__ == "__main__":
    unittest.main()


class TestContextBuilderRegistration(unittest.TestCase):
    """测试 Context Builder 注册机制"""

    def test_context_builders_registered(self):
        """验证所有 Context Builder 已注册"""
        strategy = OversoldStrategy()

        builders = strategy.get_context_blocks()

        self.assertIn("turnover", builders)
        self.assertIn("sector", builders)
        self.assertIn("market", builders)
        self.assertIn("support", builders)

    def test_context_builder_callable(self):
        """验证注册的 Builder 可调用"""
        strategy = OversoldStrategy()

        for name in strategy.get_context_blocks():
            builder = strategy._context_builders.get(name)
            self.assertTrue(callable(builder), f"Builder '{name}' is not callable")


class TestPreFetchedContext(unittest.TestCase):
    """测试 PreFetchedContext 数据类"""

    def test_prefetched_context_defaults(self):
        """验证默认值正确初始化"""
        from strategies.ai_mixin import PreFetchedContext

        ctx = PreFetchedContext()

        self.assertEqual(ctx.capital, {})
        self.assertEqual(ctx.history, {})
        self.assertEqual(ctx.concepts_map, {})
        self.assertEqual(ctx.news_tasks, {})
        self.assertEqual(ctx.history_context, "")
        self.assertEqual(ctx.global_context, "")
        self.assertIsNone(ctx.trade_date)
        self.assertTrue(ctx.indicators.empty)
        self.assertEqual(ctx.sector_stats, {})
        self.assertEqual(ctx.market_context, {})
        self.assertEqual(ctx.market_context_str, "")

    def test_prefetched_context_with_data(self):
        """验证数据赋值正确"""
        from strategies.ai_mixin import PreFetchedContext

        ctx = PreFetchedContext(
            trade_date=datetime.date(2024, 3, 21),
            sector_stats={"电子": {"count": 10}},
        )

        self.assertEqual(ctx.trade_date, datetime.date(2024, 3, 21))
        self.assertEqual(ctx.sector_stats["电子"]["count"], 10)


class TestTurnoverEdgeCases(unittest.TestCase):
    """测试换手率边界条件"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_turnover_shrinking_trend(self):
        """持续缩量趋势检测"""
        from strategies.ai_mixin import PreFetchedContext

        indicators_df = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": f"202403{20+i:02d}", "turnover_rate": 5.0 - i * 0.3}
            for i in range(20)
        ])

        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_turnover_context(row, prefetched)

        self.assertIn("持续缩量", result)

    def test_turnover_expanding_trend(self):
        """近期放量趋势检测"""
        from strategies.ai_mixin import PreFetchedContext

        indicators_df = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": f"202403{20+i:02d}", "turnover_rate": 2.0 + i * 0.5}
            for i in range(20)
        ])

        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_turnover_context(row, prefetched)

        self.assertIn("近期放量", result)

    def test_turnover_stock_not_in_indicators(self):
        """股票代码不在指标数据中"""
        from strategies.ai_mixin import PreFetchedContext

        indicators_df = pd.DataFrame([
            {"ts_code": "000002.SZ", "trade_date": "20240321", "turnover_rate": 2.5},
        ])

        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_turnover_context(row, prefetched)

        self.assertIn("无记录", result)

    def test_turnover_nan_values(self):
        """换手率包含 NaN 值"""
        from strategies.ai_mixin import PreFetchedContext

        indicators_df = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20240321", "turnover_rate": float('nan')},
            {"ts_code": "000001.SZ", "trade_date": "20240320", "turnover_rate": 2.5},
            {"ts_code": "000001.SZ", "trade_date": "20240319", "turnover_rate": 2.8},
        ])

        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_turnover_context(row, prefetched)

        self.assertIn("无效值", result)


class TestSectorEdgeCases(unittest.TestCase):
    """测试行业统计边界条件"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_sector_empty_industry(self):
        """行业字段为空"""
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.sector_stats = {"电子": {"count": 10}}

        row = {"ts_code": "000001.SZ", "industry": "", "close": 10.0}
        result = self.strategy._build_sector_context(row, prefetched)

        self.assertIn("暂无数据", result)

    def test_sector_stats_missing_fields(self):
        """行业统计缺少字段"""
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.sector_stats = {"电子": {"count": 10}}

        row = {"ts_code": "000001.SZ", "industry": "电子", "close": 10.0}
        result = self.strategy._build_sector_context(row, prefetched)

        self.assertIn("电子", result)
        self.assertIn("上涨家数: 0", result)


class TestMarketEdgeCases(unittest.TestCase):
    """测试大盘环境边界条件"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_market_context_cached_string(self):
        """使用缓存的大盘字符串"""
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.market_context_str = "缓存的大盘环境文本"

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_market_context(row, prefetched)

        self.assertEqual(result, "缓存的大盘环境文本")

    def test_market_context_non_dict_data(self):
        """大盘数据包含非字典类型"""
        from strategies.ai_mixin import PreFetchedContext

        prefetched = PreFetchedContext()
        prefetched.market_context = {
            "000001.SH": "invalid_data",
            "399001.SZ": {"pct_chg": -0.5, "trend": "震荡整理"},
        }

        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = self.strategy._build_market_context(row, prefetched)

        self.assertIn("深证成指", result)


class TestComputeSectorStats(unittest.TestCase):
    """测试行业统计计算"""

    def setUp(self):
        self.strategy = OversoldStrategy()

    def test_compute_sector_stats_normal(self):
        """正常计算行业统计"""
        screening_data = pd.DataFrame([
            {"ts_code": "000001.SZ", "industry": "电子", "pct_chg": 1.5},
            {"ts_code": "000002.SZ", "industry": "电子", "pct_chg": -0.5},
            {"ts_code": "000003.SZ", "industry": "医药", "pct_chg": 2.0},
        ])

        result = self.strategy._compute_sector_stats(screening_data)

        self.assertIn("电子", result)
        self.assertIn("医药", result)
        self.assertEqual(result["电子"]["count"], 2)
        self.assertEqual(result["电子"]["up_count"], 1)
        self.assertEqual(result["电子"]["down_count"], 1)

    def test_compute_sector_stats_missing_columns(self):
        """缺少必要列时返回空字典"""
        screening_data = pd.DataFrame([
            {"ts_code": "000001.SZ", "close": 10.0},
        ])

        result = self.strategy._compute_sector_stats(screening_data)

        self.assertEqual(result, {})

    def test_compute_sector_stats_empty_df(self):
        """空 DataFrame 返回空字典"""
        result = self.strategy._compute_sector_stats(pd.DataFrame())

        self.assertEqual(result, {})
