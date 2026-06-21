import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data.persistence.quality_gate import QualityGateError, QualityTier
from strategies.ai_mixin import PreFetchedContext
from strategies.oversold_strategy import OversoldStrategy

pytestmark = pytest.mark.unit


class TestOversoldRequiredHistoryDays(unittest.TestCase):
    def test_required_history_days(self):
        s = OversoldStrategy()
        self.assertEqual(s.required_history_days, 120)


def _make_dp_for_filter_deps():
    dp = MagicMock()
    dp._quality_tier = QualityTier.GOLD
    dp.cache = MagicMock()
    dp.trade_calendar = MagicMock()
    dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
    return dp


async def test_filter_unready_dependencies():
    s = OversoldStrategy()
    dp = _make_dp_for_filter_deps()
    context = {"data_processor": dp, "params": {}}
    with patch.object(
        s,
        "check_dependencies",
        return_value={
            "status": "unready",
            "missing_keys": ["screening_data"],
            "missing_tables": [],
        },
    ):
        result = await s.filter(context)
        assert result.empty


async def test_filter_degraded_dependencies():
    s = OversoldStrategy()
    dp = _make_dp_for_filter_deps()
    data = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.0]})
    context = {"data_processor": dp, "screening_data": data, "params": {}}
    with patch.object(
        s,
        "check_dependencies",
        return_value={
            "status": "degraded",
            "empty_keys": ["northbound_data"],
            "missing_keys": [],
            "missing_tables": [],
        },
    ):
        with patch.object(s, "_math_filter", return_value=pd.DataFrame()):
            result = await s.filter(context)
            assert result.empty


def _make_dp_for_math_filter():
    dp = MagicMock()
    dp.cache = MagicMock()
    dp.trade_calendar = MagicMock()
    dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2024, 6, 14))
    dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=datetime.date(2024, 1, 15))
    return dp


async def test_no_snapshot_data():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    context = {"data_processor": dp, "params": {}}
    result = await s._math_filter(context, 14, 30, 1.5)
    assert result.empty


async def test_no_data_processor():
    s = OversoldStrategy()
    context = {"screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}), "params": {}}
    result = await s._math_filter(context, 14, 30, 1.5)
    assert result.empty


async def test_trade_date_string_parse():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": "20240614",
        "params": {},
    }
    await s._math_filter(context, 14, 30, 1.5)
    dp.trade_calendar.get_latest_trade_date.assert_not_called()


async def test_trade_date_dash_format():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": "2024-06-14",
        "params": {},
    }
    await s._math_filter(context, 14, 30, 1.5)


async def test_trade_date_invalid_string():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": "invalid",
        "params": {},
    }
    result = await s._math_filter(context, 14, 30, 1.5)
    assert result.empty


async def test_trade_date_date_object():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": datetime.date(2024, 6, 14),
        "params": {},
    }
    await s._math_filter(context, 14, 30, 1.5)


async def test_no_trade_date_auto_resolve():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {"screening_data": snapshot, "data_processor": dp, "params": {}}
    await s._math_filter(context, 14, 30, 1.5)
    dp.trade_calendar.get_latest_trade_date.assert_called_once()


async def test_no_end_date_returns_empty():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {"screening_data": snapshot, "data_processor": dp, "params": {}}
    result = await s._math_filter(context, 14, 30, 1.5)
    assert result.empty


async def test_no_start_date_fallback():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=None)
    dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": datetime.date(2024, 6, 14),
        "params": {},
    }
    await s._math_filter(context, 14, 30, 1.5)


async def test_no_historical_data():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": datetime.date(2024, 6, 14),
        "params": {},
    }
    result = await s._math_filter(context, 14, 30, 1.5)
    assert result.empty


async def test_quality_gate_error_reraises():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.cache.get_daily_quotes = AsyncMock(side_effect=QualityGateError("quality too low"))
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": datetime.date(2024, 6, 14),
        "params": {},
    }
    with pytest.raises(QualityGateError):
        await s._math_filter(context, 14, 30, 1.5)


async def test_generic_exception_reraises():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    dp.cache.get_daily_quotes = AsyncMock(side_effect=ValueError("test error"))
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": datetime.date(2024, 6, 14),
        "params": {},
    }
    with pytest.raises(RuntimeError):
        await s._math_filter(context, 14, 30, 1.5)


async def test_rsi_filter_with_data():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    n_days = 60
    dates = [datetime.date(2024, 6, 14) - datetime.timedelta(days=i) for i in range(n_days)]
    dates.reverse()
    history_data = {
        "ts_code": ["000001.SZ"] * n_days,
        "trade_date": [d.strftime("%Y%m%d") for d in dates],
        "open": [10.0] * n_days,
        "high": [10.5] * n_days,
        "low": [9.5] * n_days,
        "close": [10.0 - i * 0.05 for i in range(n_days)],
        "vol": [1000.0 + i * 50 for i in range(n_days)],
        "amount": [10000.0 + i * 500 for i in range(n_days)],
        "pct_chg": [-0.5] * n_days,
    }
    history_pdf = pd.DataFrame(history_data)
    dp.cache.get_daily_quotes = AsyncMock(return_value=history_pdf)
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["Test"], "close": [7.0]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": datetime.date(2024, 6, 14),
        "params": {},
    }
    result = await s._math_filter(context, 14, 30, 0.5)
    assert isinstance(result, pd.DataFrame)


async def test_rsi_filter_no_candidates():
    s = OversoldStrategy()
    dp = _make_dp_for_math_filter()
    n_days = 60
    dates = [datetime.date(2024, 6, 14) - datetime.timedelta(days=i) for i in range(n_days)]
    dates.reverse()
    history_data = {
        "ts_code": ["000001.SZ"] * n_days,
        "trade_date": [d.strftime("%Y%m%d") for d in dates],
        "open": [10.0] * n_days,
        "high": [10.5] * n_days,
        "low": [9.5] * n_days,
        "close": [10.0 + i * 0.05 for i in range(n_days)],
        "vol": [1000.0] * n_days,
        "amount": [10000.0] * n_days,
        "pct_chg": [0.5] * n_days,
    }
    history_pdf = pd.DataFrame(history_data)
    dp.cache.get_daily_quotes = AsyncMock(return_value=history_pdf)
    snapshot = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["Test"], "close": [13.0]})
    context = {
        "screening_data": snapshot,
        "data_processor": dp,
        "trade_date": datetime.date(2024, 6, 14),
        "params": {},
    }
    result = await s._math_filter(context, 14, 30, 1.5)
    assert result.empty


def _make_dp_for_prefetch():
    dp = MagicMock()
    dp.cache = MagicMock()
    dp.trade_calendar = MagicMock()
    dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=datetime.date(2024, 5, 15))
    return dp


async def test_no_dp_returns_early():
    s = OversoldStrategy()
    prefetched = PreFetchedContext()
    result = await s._prefetch_strategy_specific(pd.DataFrame(), {}, prefetched)
    assert result is prefetched


async def test_prefetch_indicators():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    dp.cache.get_daily_indicators_bulk = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    result = await s._prefetch_strategy_specific(candidates, context, prefetched)
    assert result.indicators is not None


async def test_prefetch_indicators_no_start_date():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=None)
    dp.cache.get_daily_indicators_bulk = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    await s._prefetch_strategy_specific(candidates, context, prefetched)


async def test_prefetch_indicators_exception():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    dp.cache.get_daily_indicators_bulk = AsyncMock(side_effect=Exception("db error"))
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    await s._prefetch_strategy_specific(candidates, context, prefetched)


async def test_prefetch_sector_stats():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    screening_data = pd.DataFrame({"ts_code": ["000001.SZ"], "industry": ["电子"], "pct_chg": [-2.0]})
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp, "screening_data": screening_data}
    result = await s._prefetch_strategy_specific(candidates, context, prefetched)
    assert result.sector_stats is not None


async def test_prefetch_market_data():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    idx_df = pd.DataFrame(
        {
            "ts_code": [
                "000001.SH",
                "000001.SH",
                "399001.SZ",
                "399001.SZ",
                "399006.SZ",
                "399006.SZ",
            ],
            "trade_date": [
                "20240613",
                "20240614",
                "20240613",
                "20240614",
                "20240613",
                "20240614",
            ],
            "close": [3100.0, 3120.0, 9500.0, 9520.0, 2100.0, 2110.0],
            "pct_chg": [0.5, 0.6, -0.3, 0.2, 1.0, 0.5],
        }
    )
    dp.cache.get_index_daily_range = AsyncMock(return_value=idx_df)
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    result = await s._prefetch_strategy_specific(candidates, context, prefetched)
    assert result.market_context is not None


async def test_prefetch_market_data_empty_index():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    dp.cache.get_index_daily_range = AsyncMock(return_value=pd.DataFrame())
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    await s._prefetch_strategy_specific(candidates, context, prefetched)


async def test_prefetch_market_data_exception():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    dp.cache.get_index_daily_range = AsyncMock(side_effect=Exception("db error"))
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    await s._prefetch_strategy_specific(candidates, context, prefetched)


async def test_prefetch_market_no_start_date():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    dp.trade_calendar.get_start_date_by_trade_days = AsyncMock(return_value=None)
    idx_df = pd.DataFrame(
        {
            "ts_code": ["000001.SH"],
            "trade_date": ["20240614"],
            "close": [3100.0],
            "pct_chg": [0.5],
        }
    )
    dp.cache.get_index_daily_range = AsyncMock(return_value=idx_df)
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    await s._prefetch_strategy_specific(candidates, context, prefetched)


async def test_prefetch_market_trend_bullish():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    idx_data = []
    for i in range(25):
        idx_data.append(
            {
                "ts_code": "000001.SH",
                "trade_date": f"202406{10 + i:02d}",
                "close": 3000.0 + i * 5,
                "pct_chg": 0.3,
            }
        )
    idx_df = pd.DataFrame(idx_data)
    dp.cache.get_index_daily_range = AsyncMock(return_value=idx_df)
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = datetime.date(2024, 6, 14)
    context = {"data_processor": dp}
    result = await s._prefetch_strategy_specific(candidates, context, prefetched)
    if result.market_context and "000001.SH" in result.market_context:
        assert result.market_context["000001.SH"]["trend"] in [
            "多头趋势",
            "空头趋势",
            "震荡整理",
            "未知",
        ]


async def test_prefetch_no_trade_date():
    s = OversoldStrategy()
    dp = _make_dp_for_prefetch()
    candidates = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prefetched = PreFetchedContext()
    prefetched.trade_date = None
    context = {"data_processor": dp}
    await s._prefetch_strategy_specific(candidates, context, prefetched)


class TestOversoldComputeSectorStats(unittest.TestCase):
    def test_normal(self):
        s = OversoldStrategy()
        data = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                "industry": ["电子", "电子", "银行"],
                "pct_chg": [1.0, -2.0, 0.5],
            }
        )
        result = s._compute_sector_stats(data)
        self.assertIn("电子", result)
        self.assertEqual(result["电子"]["up_count"], 1)
        self.assertEqual(result["电子"]["down_count"], 1)
        self.assertIn("银行", result)

    def test_missing_columns(self):
        s = OversoldStrategy()
        data = pd.DataFrame({"ts_code": ["000001.SZ"]})
        result = s._compute_sector_stats(data)
        self.assertEqual(result, {})


class TestOversoldBuildTurnoverContextBranches(unittest.TestCase):
    def test_shrink_trend(self):
        s = OversoldStrategy()
        indicators = []
        for i in range(20):
            indicators.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": f"202403{1 + i:02d}",
                    "turnover_rate": 1.0 + i * 0.1,
                }
            )
        indicators[-1]["turnover_rate"] = 0.5
        indicators_df = pd.DataFrame(indicators)
        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_turnover_context(row, prefetched)
        self.assertIn("缩量下跌", result[0])

    def test_expand_trend(self):
        s = OversoldStrategy()
        indicators = []
        for i in range(20):
            indicators.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": f"202403{1 + i:02d}",
                    "turnover_rate": 1.0 + i * 0.1,
                }
            )
        indicators[-1]["turnover_rate"] = 5.0
        indicators_df = pd.DataFrame(indicators)
        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_turnover_context(row, prefetched)
        self.assertIn("放量下跌", result[0])

    def test_stable_turnover(self):
        s = OversoldStrategy()
        indicators = []
        for i in range(20):
            indicators.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": f"202403{1 + i:02d}",
                    "turnover_rate": 3.0,
                }
            )
        indicators_df = pd.DataFrame(indicators)
        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_turnover_context(row, prefetched)
        self.assertIn("相对平稳", result[0])

    def test_nan_values(self):
        s = OversoldStrategy()
        indicators_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": ["20240301", "20240302", "20240303"],
                "turnover_rate": [float("nan"), float("nan"), float("nan")],
            }
        )
        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_turnover_context(row, prefetched)
        self.assertIn("无效值", result[0])

    def test_no_stock_data(self):
        s = OversoldStrategy()
        indicators_df = pd.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "trade_date": ["20240301"],
                "turnover_rate": [3.0],
            }
        )
        prefetched = PreFetchedContext()
        prefetched.indicators = indicators_df
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_turnover_context(row, prefetched)
        self.assertIn("当日无记录", result[0])


class TestOversoldBuildSupportContextExtended(unittest.TestCase):
    def test_full_120_day_support(self):
        s = OversoldStrategy()
        history_data = []
        base = datetime.date(2024, 1, 1)
        for i in range(130):
            d = base + datetime.timedelta(days=i)
            history_data.append(
                {
                    "trade_date": d.strftime("%Y%m%d"),
                    "open": 10.0 + i * 0.01,
                    "high": 10.5 + i * 0.01,
                    "low": 9.5 + i * 0.01,
                    "close": 10.0 + i * 0.01,
                    "vol": 1000.0 + i * 10,
                }
            )
        history_df = pd.DataFrame(history_data)
        prefetched = PreFetchedContext()
        prefetched.history = {"000001.SZ": history_df}
        row = {"ts_code": "000001.SZ", "close": 11.5}
        result = s._build_support_context(row, prefetched)
        self.assertIn("布林下轨", result[0])

    def test_max_volume_support(self):
        s = OversoldStrategy()
        history_data = []
        for i in range(65):
            vol = 5000.0 if i == 30 else 1000.0
            history_data.append(
                {
                    "trade_date": f"202403{str(i % 28 + 1).zfill(2)}",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.0,
                    "vol": vol,
                }
            )
        history_df = pd.DataFrame(history_data)
        prefetched = PreFetchedContext()
        prefetched.history = {"000001.SZ": history_df}
        row = {"ts_code": "000001.SZ", "close": 10.5}
        result = s._build_support_context(row, prefetched)
        self.assertIsInstance(result, tuple)

    def test_empty_history_dict(self):
        s = OversoldStrategy()
        prefetched = PreFetchedContext()
        prefetched.history = {}
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_support_context(row, prefetched)
        self.assertIn("暂不可用", result[0])

    def test_close_zero_or_negative(self):
        s = OversoldStrategy()
        history_df = pd.DataFrame(
            {
                "trade_date": [f"202403{i:02d}" for i in range(1, 26)],
                "open": [10.0] * 25,
                "high": [10.5] * 25,
                "low": [9.5] * 25,
                "close": [10.0] * 25,
                "vol": [1000.0] * 25,
            }
        )
        prefetched = PreFetchedContext()
        prefetched.history = {"000001.SZ": history_df}
        row = {"ts_code": "000001.SZ", "close": 0}
        result = s._build_support_context(row, prefetched)
        self.assertIn("无效", result[0])

    def test_data_less_than_20(self):
        s = OversoldStrategy()
        history_df = pd.DataFrame(
            {
                "trade_date": [f"202403{i:02d}" for i in range(1, 10)],
                "open": [10.0] * 9,
                "high": [10.5] * 9,
                "low": [9.5] * 9,
                "close": [10.0] * 9,
                "vol": [1000.0] * 9,
            }
        )
        prefetched = PreFetchedContext()
        prefetched.history = {"000001.SZ": history_df}
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_support_context(row, prefetched)
        self.assertIsInstance(result, tuple)


class TestOversoldBuildMarketContextBranches(unittest.TestCase):
    def test_market_context_str_precedence(self):
        s = OversoldStrategy()
        prefetched = PreFetchedContext()
        prefetched.market_context_str = "大盘环境: 自定义大盘文本"
        prefetched.market_context = {"000001.SH": {"pct_chg": 1.0, "trend": "多头趋势"}}
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_market_context(row, prefetched)
        self.assertIn("自定义大盘文本", result[0])

    def test_market_context_non_dict_entry(self):
        s = OversoldStrategy()
        prefetched = PreFetchedContext()
        prefetched.market_context = {"000001.SH": "not a dict"}
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_market_context(row, prefetched)
        self.assertIn("大盘环境", result)

    def test_market_context_down_trend(self):
        s = OversoldStrategy()
        prefetched = PreFetchedContext()
        prefetched.market_context = {
            "000001.SH": {"pct_chg": -1.5, "trend": "空头趋势"},
        }
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_market_context(row, prefetched)
        self.assertIn("下跌", result[0])

    def test_market_context_flat(self):
        s = OversoldStrategy()
        prefetched = PreFetchedContext()
        prefetched.market_context = {
            "000001.SH": {"pct_chg": 0.0, "trend": "震荡整理"},
        }
        row = {"ts_code": "000001.SZ", "close": 10.0}
        result = s._build_market_context(row, prefetched)
        self.assertIn("平盘", result[0])


class TestOversoldGetDynamicDescription(unittest.TestCase):
    def test_default_params(self):
        s = OversoldStrategy()
        result = s.get_dynamic_description({})
        self.assertIn("RSI", result)

    def test_custom_params(self):
        s = OversoldStrategy()
        result = s.get_dynamic_description({"rsi_period": 7, "rsi_threshold": 25})
        self.assertIn("7", result)
        self.assertIn("25", result)


class TestOversoldSortForAI(unittest.TestCase):
    def test_empty_df(self):
        s = OversoldStrategy()
        result = s._sort_for_ai(pd.DataFrame())
        self.assertTrue(result.empty)

    def test_sort_with_rsi_and_amount(self):
        s = OversoldStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "rsi_14": [25, 20],
                "amount": [5000, 10000],
            }
        )
        result = s._sort_for_ai(df)
        self.assertEqual(result.iloc[0]["ts_code"], "000002.SZ")

    def test_sort_with_vol_no_amount(self):
        s = OversoldStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "rsi_14": [25, 20],
                "vol": [5000, 10000],
            }
        )
        result = s._sort_for_ai(df)
        self.assertEqual(len(result), 2)

    def test_sort_with_total_mv(self):
        s = OversoldStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "rsi_14": [25, 25],
                "amount": [5000, 5000],
                "total_mv": [1000, 2000],
            }
        )
        result = s._sort_for_ai(df)
        self.assertEqual(len(result), 2)

    def test_sort_no_sort_cols(self):
        s = OversoldStrategy()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "close": [10.0, 20.0],
            }
        )
        result = s._sort_for_ai(df)
        self.assertEqual(len(result), 2)


class TestOversoldGetAiContext(unittest.TestCase):
    def test_with_rsi_feature(self):
        s = OversoldStrategy()
        row = {
            "ts_code": "000001.SZ",
            "_rsi_period": 14,
            "rsi_14": 25,
            "_rsi_threshold": 30,
            "_vol_ratio_threshold": 1.5,
            "_rsi_feature_text": "连续超卖3天",
        }
        result = s.get_ai_context(row)
        self.assertIn("超跌", result)
        self.assertIn("形态反馈", result)

    def test_without_rsi_feature(self):
        s = OversoldStrategy()
        row = {
            "ts_code": "000001.SZ",
            "_rsi_period": 14,
            "rsi_14": 25,
            "_rsi_threshold": 30,
        }
        result = s.get_ai_context(row)
        self.assertIn("超跌", result)
        self.assertNotIn("形态反馈", result)

    def test_rsi_feature_excluded_text(self):
        s = OversoldStrategy()
        row = {
            "ts_code": "000001.SZ",
            "_rsi_period": 14,
            "rsi_14": 25,
            "_rsi_threshold": 30,
            "_rsi_feature_text": "暂不解读",
        }
        result = s.get_ai_context(row)
        self.assertNotIn("形态反馈", result)

    def test_rsi_feature_insufficient_history(self):
        s = OversoldStrategy()
        row = {
            "ts_code": "000001.SZ",
            "_rsi_period": 14,
            "rsi_14": 25,
            "_rsi_threshold": 30,
            "_rsi_feature_text": "历史数据不足",
        }
        result = s.get_ai_context(row)
        self.assertNotIn("形态反馈", result)


if __name__ == "__main__":
    unittest.main()
