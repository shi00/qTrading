"""
Tests for ReviewManager.

验证复盘管理器功能，包括预测结果复盘、学习上下文获取和结果保存。
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import datetime
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

from data.constants import DEFAULT_BENCHMARK_INDEX, REVIEW_STATUS_T1_DONE
from data.persistence.review_manager import ReviewManager
from utils.time_utils import to_date
import pytest


pytestmark = [pytest.mark.integration, pytest.mark.no_db]


def _index_daily_close_side_effect(t0_close: float, t5_close: float):
    """RV-01: 构造 quote_dao.get_index_daily 的 side_effect——按 trade_date 返回对应收盘点位。

    旧 mock 固定返回单日 ``pct_chg``；修复后基准侧取窗口累计收益，需要
    窗口首尾两点（T0 与 T+5）的 close。本 helper 区分两日返回，窗口收益 =
    (t5_close/t0_close - 1) * 100。
    """

    async def _side_effect(ts_code=None, trade_date=None, **kwargs):
        if trade_date is None:
            return pd.DataFrame()
        d = trade_date.strftime("%Y%m%d") if hasattr(trade_date, "strftime") else str(trade_date).replace("-", "")
        close = t0_close if d in {"20240315", "20240310"} else t5_close
        return pd.DataFrame({"close": [close], "pct_chg": [1.0]})

    return _side_effect


def _make_trade_cal_mock():
    """返回模拟真实 stock_dao.get_trade_cal 的 AsyncMock。

    真实 DAO 按 [start_date, end_date] 过滤并排除周末；mock 需同等行为，
    否则 T+N 锚定（TradeCalendarService.get_trade_dates）会错位到非交易日。
    """

    async def _side_effect(start_date=None, end_date=None, is_open=None, **kwargs):
        start = to_date(start_date) if start_date is not None else datetime.date(2024, 1, 1)
        end = to_date(end_date) if end_date is not None else datetime.date(2024, 12, 31)
        if start > end:
            return pd.DataFrame({"cal_date": [], "is_open": []})
        days = []
        d = start
        while d <= end:
            if d.weekday() < 5:  # 周一~周五为交易日
                days.append(d.strftime("%Y%m%d"))
            d += datetime.timedelta(days=1)
        return pd.DataFrame({"cal_date": days, "is_open": [1] * len(days)})

    return AsyncMock(side_effect=_side_effect)


def _make_engine():
    """返回支持 `async with engine.begin() as conn` 的 engine 替身（可断言 begin 被调用）。

    用 asynccontextmanager 而非 AsyncMock 模拟事务上下文，避免 AsyncMock 的
    __aenter__/__aexit__ 产生"coroutine never awaited"运行时警告。
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _begin():
        yield MagicMock()

    engine = MagicMock()
    engine.begin = MagicMock(side_effect=_begin)
    return engine


class TestReviewManagerInit(unittest.TestCase):
    """测试初始化"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_init(self, mock_config, mock_api, mock_cache):
        """正常初始化"""
        manager = ReviewManager()

        self.assertIsNotNone(manager.cache)
        self.assertIsNotNone(manager.api)
        self.assertIsNotNone(manager.config)


class TestGetPendingPredictions(unittest.TestCase):
    """测试获取待复盘预测"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_pending_success(self, mock_config, mock_api, mock_cache):
        """成功获取待复盘预测"""
        mock_df = pd.DataFrame(
            {
                "id": [1, 2],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": ["20240315", "20240314"],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(return_value=mock_df)

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.quote_dao.get_latest_trade_date = AsyncMock(return_value="20240320")
        mock_cache_instance.stock_dao.get_trade_cal = _make_trade_cal_mock()
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager._get_pending_predictions()
            self.assertEqual(len(result), 2)

        asyncio.run(run_test())


class TestReviewManagerIndexDailyType(unittest.TestCase):
    """H-4: cache.quote_dao.get_index_daily must receive datetime.date, not string."""

    def _make_manager_with_pending(self, mock_cache, mock_api):
        from data.persistence.review_manager import ReviewManager

        pending_df = pd.DataFrame(
            [
                {
                    "id": 1,
                    "ts_code": "000001.SZ",
                    "strategy_name": "test",
                    "trade_date": datetime.date(2024, 3, 15),
                    "prediction_result": "WIN",
                    "ai_score": 80.0,
                }
            ]
        )
        mock_cache.quote_dao.get_latest_trade_date = AsyncMock(return_value="20240318")
        mock_cache.stock_dao.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "cal_date": [
                        "20240308",
                        "20240311",
                        "20240312",
                        "20240313",
                        "20240314",
                        "20240315",
                        "20240318",
                        "20240319",
                        "20240320",
                        "20240321",
                        "20240322",
                    ],
                    "is_open": [1] * 11,
                }
            )
        )
        mock_cache.screener_dao = MagicMock()
        mock_cache.screener_dao.get_pending_predictions = AsyncMock(return_value=pending_df)
        mock_cache.screener_dao.update_prediction_result = AsyncMock()
        # D4-M4: 行情覆盖到 T+5（20240322），触发 T+5 成熟分支的基准指数解析。
        mock_cache.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                    "trade_date": [
                        datetime.date(2024, 3, 15),
                        datetime.date(2024, 3, 18),
                        datetime.date(2024, 3, 22),
                    ],
                    "close": [10.0, 10.3, 10.3],
                    "pct_chg": [1.0, 3.0, 3.0],
                }
            )
        )
        mock_cache.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )
        mock_cache.get_index_daily_range = AsyncMock(return_value=None)
        mock_api.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))
        manager = ReviewManager()
        manager.cache = mock_cache
        manager.api = mock_api
        return manager

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_cache_get_index_daily_receives_date_object(self, mock_config):
        mock_cache = MagicMock()
        mock_api = MagicMock()
        manager = self._make_manager_with_pending(mock_cache, mock_api)

        async def run_test():
            await manager.run_review()
            call_kwargs = mock_cache.quote_dao.get_index_daily.await_args.kwargs
            assert isinstance(call_kwargs["trade_date"], datetime.date), (
                f"H-4: cache.quote_dao.get_index_daily must receive datetime.date, got {type(call_kwargs['trade_date'])}"
            )

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_api_get_index_daily_receives_string(self, mock_config):
        mock_cache = MagicMock()
        mock_api = MagicMock()
        manager = self._make_manager_with_pending(mock_cache, mock_api)
        mock_cache.quote_dao.get_index_daily = AsyncMock(return_value=None)

        async def run_test():
            await manager.run_review()
            call_kwargs = mock_api.get_index_daily.await_args.kwargs
            assert isinstance(call_kwargs["start_date"], str), (
                f"H-4: api.get_index_daily must receive string, got {type(call_kwargs['start_date'])}"
            )
            # D4-M4: run_review 在 T+5 成熟分支查询基准收益（复单 label_date=T+5，非 T+1）。
            assert call_kwargs["start_date"] == "20240322"

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.ConfigHandler")
    def test_cache_index_lookup_failure_logs_warning(self, mock_config):
        mock_cache = MagicMock()
        mock_api = MagicMock()
        manager = self._make_manager_with_pending(mock_cache, mock_api)
        mock_cache.quote_dao.get_index_daily = AsyncMock(side_effect=RuntimeError("cache exploded"))

        async def run_test():
            with self.assertLogs("data.persistence.review_manager", level="WARNING") as cm:
                await manager.run_review()
            assert any("Cache index lookup failed" in message for message in cm.output), (
                "H-4: cache index lookup failure must emit warning log"
            )

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_pending_empty(self, mock_config, mock_api, mock_cache):
        """空结果"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(return_value=pd.DataFrame())

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.quote_dao.get_latest_trade_date = AsyncMock(return_value="20240320")
        mock_cache_instance.stock_dao.get_trade_cal = _make_trade_cal_mock()
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager._get_pending_predictions()
            self.assertTrue(result.empty)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_pending_error(self, mock_config, mock_api, mock_cache):
        """错误返回空 DataFrame"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_pending_predictions = AsyncMock(side_effect=Exception("DB error"))

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache_instance.quote_dao.get_latest_trade_date = AsyncMock(side_effect=Exception("DB error"))
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager._get_pending_predictions()
            self.assertTrue(result.empty)

        asyncio.run(run_test())


class TestReviewManagerUpdateResultStatusOverride(unittest.TestCase):
    """M-2: _update_result should allow explicit review_status passthrough."""

    def test_update_result_passes_explicit_review_status(self):
        manager = ReviewManager.__new__(ReviewManager)
        manager.cache = MagicMock()
        manager.cache.screener_dao = MagicMock()
        manager.cache.screener_dao.update_prediction_result = AsyncMock()

        async def run_test():
            await manager._update_result(
                record_id=1,
                pct=2.5,
                label="WIN",
                t1_price=10.5,
                review_status="NO_INDEX_DATA",
            )
            kwargs = manager.cache.screener_dao.update_prediction_result.await_args.kwargs
            self.assertEqual(kwargs["review_status"], "NO_INDEX_DATA")

        asyncio.run(run_test())


class TestGetLearningContext(unittest.TestCase):
    """测试获取学习上下文"""

    def setUp(self):
        """强制中文 locale, 避免完整集成测试套件中 I18n._locale 被其他测试污染为 en_US。

        ReviewManager.get_learning_context 用 I18n.get('review_ctx_positive') 等返回
        本地化文案, 本测试断言中文文案, 故需确保 locale=zh_CN。
        """
        from core.i18n import I18n

        self._saved_locale = I18n._locale
        self._saved_initialized = I18n._initialized
        I18n._locale = "zh_CN"
        I18n._initialized = True

    def tearDown(self):
        from core.i18n import I18n

        I18n._locale = self._saved_locale
        I18n._initialized = self._saved_initialized

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_learning_context_with_data(self, mock_config, mock_api, mock_cache):
        """有历史数据"""
        mock_wins = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "alpha": [4.2],
                "t1_pct": [5.5],
                "ai_score": [85],
                "ai_reason": ["技术突破"],
            }
        )

        mock_losses = pd.DataFrame(
            {
                "ts_code": ["000002.SZ"],
                "name": ["万科A"],
                "alpha": [-2.1],
                "t1_pct": [-3.2],
                "ai_score": [70],
                "ai_reason": ["市场下跌"],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.get_learning_context = AsyncMock(side_effect=[mock_wins, mock_losses])

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager.get_learning_context(limit=3)
            self.assertIn("history_context", result)
            self.assertIn("复盘参考 - 正向样本", result)
            self.assertIn("复盘参考 - 负向样本", result)
            self.assertIn("Alpha(相对基准超额) +4.2%", result)
            self.assertIn("Alpha(相对基准超额) -2.1%", result)
            self.assertNotIn("Learn from these", result)
            self.assertNotIn("Do NOT repeat", result)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_learning_context_empty(self, mock_config, mock_api, mock_cache):
        """无历史数据"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_learning_context = AsyncMock(return_value=pd.DataFrame())

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager.get_learning_context(limit=3)
            self.assertIn("暂无可用历史复盘样本", result)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_get_learning_context_error(self, mock_config, mock_api, mock_cache):
        """错误返回空上下文"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.get_learning_context = AsyncMock(side_effect=Exception("DB error"))

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            result = await manager.get_learning_context(limit=3)
            self.assertIn("暂无可用历史复盘样本", result)

        asyncio.run(run_test())


class TestSaveResults(unittest.TestCase):
    """测试保存筛选结果"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_with_data(self, mock_config, mock_api, mock_cache):
        """正常保存筛选结果"""
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [10.5],
                "pct_chg": [2.5],
                "industry_sw_l2": ["银行"],
                "vol": [1000000],
                "amount": [10500000],
                "turnover_rate": [1.5],
                "pe_ttm": [6.5],
                "pb": [0.8],
                "ps_ttm": [1.2],
                "dv_ttm": [3.5],
                "total_mv": [1000000],
                "circ_mv": [800000],
                "roe": [12.5],
                "grossprofit_margin": [45.0],
                "debt_to_assets": [60.0],
                "or_yoy": [10.0],
                "netprofit_yoy": [15.0],
                "ai_score": [85],
                "ai_reason": ["技术突破"],
                "thinking": ["看好后市"],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        analysis_date = datetime.date(2024, 12, 31)

        async def run_test():
            await manager.save_results("test_strategy", mock_df, trade_date=analysis_date)
            mock_screener_dao.save_screening_results.assert_called_once()
            call_args = mock_screener_dao.save_screening_results.call_args
            records = call_args[0][0]
            saved_date = records[0]["trade_date"]
            self.assertEqual(saved_date, analysis_date)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_empty(self, mock_config, mock_api, mock_cache):
        """空 DataFrame 不保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results("test_strategy", pd.DataFrame())
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_none(self, mock_config, mock_api, mock_cache):
        """None 不保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results("test_strategy", None)
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())


class TestReviewPredictionsCore(unittest.TestCase):
    """测试 run_review 核心复盘流程（P1 级）"""

    def _make_manager(self, mock_cache_instance, mock_api_instance=None):
        with (
            patch(
                "data.persistence.review_manager.CacheManager",
                return_value=mock_cache_instance,
            ),
            patch(
                "data.persistence.review_manager.TushareClient",
                return_value=mock_api_instance,
            ),
            patch("data.persistence.review_manager.ConfigHandler"),
        ):
            return ReviewManager()

    def _make_pending_df(self, ids=None, ts_codes=None, trade_dates=None):
        if ids is None:
            ids = [1]
        if ts_codes is None:
            ts_codes = ["000001.SZ"]
        if trade_dates is None:
            trade_dates = ["20240315"]
        return pd.DataFrame({"id": ids, "ts_code": ts_codes, "trade_date": trade_dates})

    def _setup_cache_with_pending(self, mock_cache_instance, pending_df=None):
        mock_cache_instance.quote_dao.get_latest_trade_date = AsyncMock(return_value="20240320")
        mock_cache_instance.stock_dao.get_trade_cal = _make_trade_cal_mock()
        if pending_df is None:
            pending_df = self._make_pending_df()
        mock_cache_instance.screener_dao.get_pending_predictions = AsyncMock(return_value=pending_df)
        mock_cache_instance.screener_dao.update_prediction_result = AsyncMock()

    def test_review_win_when_alpha_positive(self):
        """Alpha > 3.0 时标记为 WIN（D4-M4：以 T+5 成熟窗口定稿）"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                    "trade_date": ["20240315", "20240318", "20240322"],
                    "close": [10.0, 10.5, 10.5],
                    "pct_chg": [1.0, 5.0, 5.0],
                }
            )
        )
        # RV-01: 指数窗口 T0=100.0、T+5=101.0 → 窗口 +1%（个股 T+5 +5% → alpha=4.0 → WIN）
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            side_effect=_index_daily_close_side_effect(100.0, 101.0)
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.screener_dao.update_prediction_result.assert_called_once()
            call_args = mock_cache_instance.screener_dao.update_prediction_result.call_args
            self.assertEqual(call_args[0][2], "WIN")
            self.assertAlmostEqual(call_args.kwargs["alpha"], 4.0)

        asyncio.run(run_test())

    def test_review_loss_when_alpha_negative(self):
        """Alpha < -3.0 时标记为 LOSS（D4-M4：以 T+5 成熟窗口定稿）"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                    "trade_date": ["20240315", "20240318", "20240322"],
                    "close": [10.0, 9.8, 9.5],
                    "pct_chg": [1.0, -2.0, -5.0],
                }
            )
        )
        # RV-01: 指数窗口 T0=100.0、T+5=102.0 → 窗口 +2%（个股 T+5 -5% → alpha=-7.0 → LOSS）
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            side_effect=_index_daily_close_side_effect(100.0, 102.0)
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [2.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.screener_dao.update_prediction_result.assert_called_once()
            call_args = mock_cache_instance.screener_dao.update_prediction_result.call_args
            self.assertEqual(call_args[0][2], "LOSS")
            self.assertAlmostEqual(call_args.kwargs["alpha"], -7.0)

        asyncio.run(run_test())

    def test_review_draw_when_alpha_near_zero(self):
        """|Alpha| <= 3.0 时标记为 DRAW（D4-M4：以 T+5 成熟窗口定稿）"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                    "trade_date": ["20240315", "20240318", "20240322"],
                    "close": [10.0, 10.1, 10.1],
                    "pct_chg": [1.0, 1.0, 1.0],
                }
            )
        )
        # RV-01: 指数窗口 T0=100.0、T+5=100.8 → 窗口 +0.8%（个股 T+5 +1% → alpha=0.2 → DRAW）
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            side_effect=_index_daily_close_side_effect(100.0, 100.8)
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"pct_chg": [0.8]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.screener_dao.update_prediction_result.assert_called_once()
            call_args = mock_cache_instance.screener_dao.update_prediction_result.call_args
            self.assertEqual(call_args[0][2], "DRAW")
            self.assertAlmostEqual(call_args.kwargs["alpha"], 0.2)

        asyncio.run(run_test())

    def test_review_persists_t5_metrics_when_available(self):
        """T+5 涨幅应使用分析日到第 5 个交易日的累计涨幅"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 6,
                    "trade_date": [
                        "20240315",
                        "20240318",
                        "20240319",
                        "20240320",
                        "20240321",
                        "20240322",
                    ],
                    "close": [10.0, 10.5, 10.7, 10.8, 10.9, 11.0],
                    "pct_chg": [1.0, 5.0, 1.9, 0.9, 0.9, 0.9],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            side_effect=_index_daily_close_side_effect(100.0, 101.0)
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            kwargs = mock_cache_instance.screener_dao.update_prediction_result.call_args.kwargs
            self.assertAlmostEqual(kwargs["t5_pct"], 10.0)
            self.assertEqual(kwargs["t5_price"], 11.0)
            # RV-01: index_pct 为窗口累计收益（T0=100 → T+5=101，窗口 +1%）
            self.assertAlmostEqual(kwargs["index_pct"], 1.0)
            # D4-M4: alpha = t5_pct - index_pct = 10.0 - 1.0 = 9.0（T+5 窗口）。
            self.assertAlmostEqual(kwargs["alpha"], 9.0)

        asyncio.run(run_test())

    def test_review_no_t1_data(self):
        """T+1 数据缺失时不更新"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240315"],
                    "close": [10.0],
                    "pct_chg": [1.0],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.screener_dao.update_prediction_result.assert_not_called()

        asyncio.run(run_test())

    def test_review_no_quotes_at_all(self):
        """无行情数据时跳过"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.screener_dao.update_prediction_result.assert_not_called()

        asyncio.run(run_test())

    def test_review_index_data_failure_stages_numeric_only(self):
        """RV-04: T+5 成熟但基准指数不可得时数值-only 落库（不整条丢弃）。

        t5_pct/t5_price/t1 数值照写，label/alpha/index_pct 置 NULL（R21 缺失值
        语义），review_status=T1_DONE 留在补标签通道（get_unlabeled_predictions），
        基准恢复后由 backfill 补定稿。
        """
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                    "trade_date": ["20240315", "20240318", "20240322"],
                    "close": [10.0, 10.3, 10.3],
                    "pct_chg": [1.0, 3.0, 3.0],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(return_value=None)
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(side_effect=Exception("API Error"))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            dao = mock_cache_instance.screener_dao
            assert dao.update_prediction_result.await_count == 1
            call_args = dao.update_prediction_result.call_args
            assert call_args[0][2] is None  # label 未定稿（R21 NULL 不伪造）
            assert call_args.kwargs["t5_pct"] == pytest.approx(3.0)  # (10.3/10.0 - 1) × 100
            assert call_args.kwargs["t5_price"] == pytest.approx(10.3)
            assert call_args.kwargs["index_pct"] is None
            assert call_args.kwargs["alpha"] is None
            assert call_args.kwargs["benchmark_code"] is None  # 保持已有基准不覆盖（D2-5）
            assert call_args.kwargs["review_status"] == REVIEW_STATUS_T1_DONE

        asyncio.run(run_test())

    def test_review_empty_pending(self):
        """无待复盘预测时直接返回"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance, pending_df=pd.DataFrame())
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.quote_dao.get_daily_quotes.assert_not_called()

        asyncio.run(run_test())

    def test_review_t0_not_found_in_quotes(self):
        """预测日期不在行情数据中时跳过"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(
            mock_cache_instance,
            pending_df=self._make_pending_df(trade_dates=["20240310"]),
        )
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "trade_date": ["20240311", "20240312"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.screener_dao.update_prediction_result.assert_not_called()

        asyncio.run(run_test())

    def test_review_multiple_predictions(self):
        """多条预测逐一复盘"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(
            mock_cache_instance,
            pending_df=self._make_pending_df(
                ids=[1, 2],
                ts_codes=["000001.SZ", "000002.SZ"],
                trade_dates=["20240315", "20240315"],
            ),
        )
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ", "000002.SZ"],
                    "trade_date": ["20240315", "20240318", "20240315", "20240318"],
                    "close": [10.0, 10.5, 20.0, 21.0],
                    "pct_chg": [1.0, 5.0, 1.0, 5.0],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            self.assertEqual(mock_cache_instance.screener_dao.update_prediction_result.call_count, 2)

        asyncio.run(run_test())

    def test_bulk_prefetch_calls_get_index_daily_range(self):
        """run_review 应调用 get_index_daily_range 进行批量预获取"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "trade_date": ["20240315", "20240318"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SH", "000001.SH"],
                    "trade_date": ["20240315", "20240318"],
                    "pct_chg": [1.0, 1.0],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            mock_cache_instance.get_index_daily_range.assert_called_once()
            call_kwargs = mock_cache_instance.get_index_daily_range.call_args.kwargs
            assert call_kwargs["ts_code_list"] == [DEFAULT_BENCHMARK_INDEX]
            # D3-m2: get_index_daily_range 边界亦改为 date 对象（与 run_review 全程 date 方向统一）
            assert call_kwargs["start_date"] == datetime.date(2024, 3, 15)

        asyncio.run(run_test())

    def test_bulk_prefetch_avoids_per_day_index_queries(self):
        """批量预获取成功后，循环内不应再逐日调用 get_index_daily。

        RV-04 降级链的探针对批量最老记录日（probe_date=min_pred_date）做一次
        本地探测命中配置基准——这是唯一的逐日查询；批量预取成功后循环内
        （本例 T+5 未成熟，不进入窗口收益解析）不再有额外逐日查询。
        """
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "trade_date": ["20240315", "20240318"],
                    "close": [10.0, 10.5],
                    "pct_chg": [1.0, 5.0],
                }
            )
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SH", "000001.SH"],
                    "trade_date": ["20240315", "20240318"],
                    "pct_chg": [1.0, 1.0],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            # 仅 RV-04 探针 1 次本地探测（probe_date=批量最老记录日），无 API 兜底、无循环内逐日查询。
            mock_cache_instance.quote_dao.get_index_daily.assert_called_once_with(
                ts_code=DEFAULT_BENCHMARK_INDEX,
                trade_date=datetime.date(2024, 3, 15),
            )
            mock_api_instance.get_index_daily.assert_not_called()

        asyncio.run(run_test())

    def test_bulk_prefetch_failure_falls_back_to_per_day_query(self):
        """批量预获取失败时，应降级到逐日查询"""
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                    "trade_date": ["20240315", "20240318", "20240322"],
                    "close": [10.0, 10.5, 10.5],
                    "pct_chg": [1.0, 5.0, 5.0],
                }
            )
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(side_effect=RuntimeError("bulk fetch failed"))
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            side_effect=_index_daily_close_side_effect(100.0, 101.0)
        )

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            # RV-04 探针（probe_date=批量最老记录日 20240315）+ RV-01 窗口首尾两点
            # （T0 与 T+5）close = 共 3 次逐日查询；批量预取失败不产生额外调用。
            calls = mock_cache_instance.quote_dao.get_index_daily.call_args_list
            assert len(calls) == 3
            assert calls[0].kwargs["trade_date"] == datetime.date(2024, 3, 15)  # RV-04 探针
            assert calls[1].kwargs["trade_date"] == datetime.date(2024, 3, 15)  # 窗口 T0
            assert calls[2].kwargs["trade_date"] == datetime.date(2024, 3, 22)  # 窗口 T+5
            assert mock_cache_instance.screener_dao.update_prediction_result.await_count == 1

        asyncio.run(run_test())

    def test_review_all_candidates_missing_t1_does_not_use_t2(self):
        """D3-M1 测试缺口（场景 1）：2 只候选股 T+1 全缺行时不得把 T+2 价格记为 t1_pct。

        行情并集中 T+1（20240318）整体缺行、T+2（20240319）有行：若以个股行序锚定
        T+1 会滑到 T+2 误标（10.0→10.3）；日历锚定下 t1_date=20240318 无行情 → 悬空跳过。
        """
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(
            mock_cache_instance,
            pending_df=self._make_pending_df(
                ids=[1, 2],
                ts_codes=["000001.SZ", "000002.SZ"],
                trade_dates=["20240315", "20240315"],
            ),
        )
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ", "000002.SZ"],
                    "trade_date": ["20240315", "20240319", "20240315", "20240319"],
                    "close": [10.0, 10.3, 20.0, 20.5],
                    "pct_chg": [1.0, 3.0, 1.0, 2.5],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)

        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            await manager.run_review()
            # 日历数据源确实被调用（区分"锚定跳过"与"日历退化静默返回空"两种形态）
            mock_cache_instance.stock_dao.get_trade_cal.assert_awaited()
            mock_cache_instance.screener_dao.update_prediction_result.assert_not_called()

        asyncio.run(run_test())

    def test_three_paths_resolve_same_t1_t5_anchors(self):
        """D3-M1 测试缺口（场景 3）：三条复盘通路对同一 t0 解出相同的 T+1/T+5 锚点。

        行情仅覆盖 t0（20240315）、T+1（20240318）、T+5（20240322），中间 3 个交易日
        缺行（个股停牌）。三路均以全市场交易日历锚定：run_review 与 backfill_t1_returns
        解出相同 T+1（t1_price=11.0 唯一指纹）、run_review 与 backfill_horizon_returns
        解出相同 T+5（t5_price=12.0）；若某路退化为个股行序锚定，T+5=iloc[5] 越界无法
        产出，本测试即失败。
        """
        quotes = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"] * 3,
                "trade_date": ["20240315", "20240318", "20240322"],
                "close": [10.0, 11.0, 12.0],
                "pct_chg": [1.0, 10.0, 9.1],
            }
        )
        index_df = pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        candidate = [{"id": 1, "ts_code": "000001.SZ", "trade_date": datetime.date(2024, 3, 15)}]

        # ① run_review：T+1=20240318（+10.0%）、T+5=20240322（+20.0%）
        mock_cache_instance = MagicMock()
        self._setup_cache_with_pending(mock_cache_instance)
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(return_value=quotes)
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(return_value=index_df)
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)
        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=index_df)
        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        asyncio.run(manager.run_review())
        mock_cache_instance.screener_dao.update_prediction_result.assert_called_once()
        call_args = mock_cache_instance.screener_dao.update_prediction_result.call_args
        self.assertEqual(call_args[0][0], 1)
        self.assertAlmostEqual(call_args[0][1], 10.0)
        self.assertEqual(call_args.kwargs["t1_price"], 11.0)
        self.assertAlmostEqual(call_args.kwargs["t5_pct"], 20.0)
        self.assertEqual(call_args.kwargs["t5_price"], 12.0)

        # ② backfill_t1_returns：同一 t0 → 相同 T+1 锚点（t1_price=11.0、pct=10.0）
        mock_cache_instance = MagicMock()
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(return_value=quotes)
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(return_value=index_df)
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)
        mock_cache_instance.stock_dao.get_trade_cal = _make_trade_cal_mock()
        mock_cache_instance.screener_dao.get_unfilled_t1_predictions = AsyncMock(return_value=candidate)
        mock_cache_instance.screener_dao.update_prediction_result = AsyncMock()
        mock_cache_instance.engine = _make_engine()
        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        result = asyncio.run(manager.backfill_t1_returns())
        self.assertEqual(result, 1)
        mock_cache_instance.engine.begin.assert_called_once()  # noqa: weak-assertion begin 无参事务上下文，次数断言即验证批量事务路径
        call_args = mock_cache_instance.screener_dao.update_prediction_result.call_args
        self.assertEqual(call_args[0][0], 1)
        self.assertAlmostEqual(call_args[0][1], 10.0)
        self.assertEqual(call_args.kwargs["t1_price"], 11.0)

        # ③ backfill_horizon_returns：同一 t0 → 相同 T+5 锚点（t5_price=12.0、t5_pct=20.0%）
        mock_cache_instance = MagicMock()
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(return_value=quotes)
        mock_cache_instance.stock_dao.get_trade_cal = _make_trade_cal_mock()
        mock_cache_instance.screener_dao.get_unfilled_horizon_predictions = AsyncMock(return_value=candidate)
        # RV-04: B 类池（数值已齐、标签未定稿）同批取数，空池 = 无补定稿记录。
        mock_cache_instance.screener_dao.get_unlabeled_predictions = AsyncMock(return_value=[])
        mock_cache_instance.screener_dao.backfill_t5_prediction = AsyncMock()
        mock_cache_instance.engine = _make_engine()
        # D4-M4: backfill_horizon_returns 解析 T+5 基准指数以定稿标签。
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(return_value=index_df)
        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        result = asyncio.run(manager.backfill_horizon_returns(horizon=5))
        self.assertEqual(result, 1)
        mock_cache_instance.engine.begin.assert_called_once()  # noqa: weak-assertion begin 无参事务上下文，次数断言即验证批量事务路径
        call_args = mock_cache_instance.screener_dao.backfill_t5_prediction.call_args
        self.assertEqual(call_args[0][0], 1)
        self.assertAlmostEqual(call_args[0][1], 20.0)
        self.assertEqual(call_args[0][2], 12.0)

    def test_backfill_t1_missing_t1_skips_not_misuses_t2(self):
        """D3-M1 测试缺口（场景 3 补充）：backfill_t1_returns 对 T+1 缺行记录不误用 T+2。

        行情 [20240315(t0), 20240319(T+2), 20240322(T+5)] 中 T+1=20240318 缺行：日历锚定下
        t1_date 无行情 → 跳过（返回 0、不更新）；若退化为个股行序锚定会把 20240319 当 T+1
        （错价 +120%）→ 测试失败。与 run_review 的 T+1 缺行测试共同证明两条 T+1 通路对
        同一 t0 的锚定一致。
        """
        mock_cache_instance = MagicMock()
        mock_cache_instance.quote_dao.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"] * 3,
                    "trade_date": ["20240315", "20240319", "20240322"],
                    "close": [10.0, 22.0, 12.0],
                    "pct_chg": [1.0, 120.0, 9.1],
                }
            )
        )
        mock_cache_instance.quote_dao.get_index_daily = AsyncMock(
            return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]})
        )
        mock_cache_instance.get_index_daily_range = AsyncMock(return_value=None)
        mock_cache_instance.stock_dao.get_trade_cal = _make_trade_cal_mock()
        mock_cache_instance.screener_dao.get_unfilled_t1_predictions = AsyncMock(
            return_value=[{"id": 1, "ts_code": "000001.SZ", "trade_date": datetime.date(2024, 3, 15)}]
        )
        mock_cache_instance.screener_dao.update_prediction_result = AsyncMock()
        mock_cache_instance.engine = _make_engine()
        mock_api_instance = MagicMock()
        mock_api_instance.get_index_daily = AsyncMock(return_value=pd.DataFrame({"close": [100.0], "pct_chg": [1.0]}))

        manager = self._make_manager(mock_cache_instance, mock_api_instance)

        async def run_test():
            result = await manager.backfill_t1_returns()
            self.assertEqual(result, 0)
            mock_cache_instance.screener_dao.update_prediction_result.assert_not_called()
            mock_cache_instance.engine.begin.assert_not_called()

        asyncio.run(run_test())


class TestSaveResultsEdgeCases(unittest.TestCase):
    """测试保存结果边界条件"""

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_missing_ts_code(self, mock_config, mock_api, mock_cache):
        """缺少 ts_code 的行被跳过"""
        mock_df = pd.DataFrame(
            {
                "name": ["平安银行"],
                "close": [10.5],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results(
                "test_strategy",
                mock_df,
                trade_date=datetime.date(2024, 12, 31),
            )
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_save_results_with_nan_values(self, mock_config, mock_api, mock_cache):
        """NaN 值被正确处理"""
        import numpy as np

        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [np.nan],
                "pct_chg": [2.5],
                "ai_score": [np.nan],
                "ai_reason": [np.nan],
                "thinking": [np.nan],
            }
        )

        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            await manager.save_results(
                "test_strategy",
                mock_df,
                trade_date=datetime.date(2024, 12, 31),
            )
            mock_screener_dao.save_screening_results.assert_called_once()
            call_args = mock_screener_dao.save_screening_results.call_args[0][0]
            self.assertEqual(len(call_args), 1)

        asyncio.run(run_test())


class TestSaveResultsTradeDateSemantics(unittest.TestCase):
    """测试 save_results 的 trade_date 语义：必须使用分析交易日而非当前自然日"""

    def _make_mock_df(self):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["平安银行"],
                "close": [10.5],
                "pct_chg": [2.5],
                "industry_sw_l2": ["银行"],
                "vol": [1000000],
                "amount": [10500000],
                "turnover_rate": [1.5],
                "pe_ttm": [6.5],
                "pb": [0.8],
                "ps_ttm": [1.2],
                "dv_ttm": [3.5],
                "total_mv": [1000000],
                "circ_mv": [800000],
                "roe": [12.5],
                "grossprofit_margin": [45.0],
                "debt_to_assets": [60.0],
                "or_yoy": [10.0],
                "netprofit_yoy": [15.0],
                "ai_score": [85],
                "ai_reason": ["技术突破"],
                "thinking": ["看好后市"],
            }
        )

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_explicit_trade_date_used(self, mock_config, mock_api, mock_cache):
        """显式传入 trade_date 时，使用该日期而非当前自然日"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        analysis_date = datetime.date(2024, 12, 31)

        async def run_test():
            await manager.save_results("test_strategy", self._make_mock_df(), trade_date=analysis_date)
            mock_screener_dao.save_screening_results.assert_called_once()
            records = mock_screener_dao.save_screening_results.call_args[0][0]
            saved_date = records[0]["trade_date"]
            self.assertEqual(saved_date, analysis_date)
            self.assertNotEqual(saved_date, datetime.date.today())

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_missing_trade_date_raises(self, mock_config, mock_api, mock_cache):
        """未传入 trade_date 且结果不带 trade_date 时，应拒绝保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()

        async def run_test():
            with self.assertRaises(ValueError):
                await manager.save_results("test_strategy", self._make_mock_df())
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_weekend_analysis_date_preserved(self, mock_config, mock_api, mock_cache):
        """周五盘后分析时，trade_date 应为周五而非周六"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        friday_date = datetime.date(2024, 12, 27)

        async def run_test():
            await manager.save_results("test_strategy", self._make_mock_df(), trade_date=friday_date)
            records = mock_screener_dao.save_screening_results.call_args[0][0]
            saved_date = records[0]["trade_date"]
            self.assertEqual(saved_date, friday_date)
            self.assertEqual(saved_date.weekday(), 4)

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_df_trade_date_used_when_arg_missing(self, mock_config, mock_api, mock_cache):
        """未显式传参时，可从结果集中唯一 trade_date 推导分析日"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        df = self._make_mock_df().copy()
        df["trade_date"] = ["20241231"]

        async def run_test():
            await manager.save_results("test_strategy", df)
            records = mock_screener_dao.save_screening_results.call_args[0][0]
            self.assertEqual(records[0]["trade_date"], datetime.date(2024, 12, 31))

        asyncio.run(run_test())

    @patch("data.persistence.review_manager.CacheManager")
    @patch("data.persistence.review_manager.TushareClient")
    @patch("data.persistence.review_manager.ConfigHandler")
    def test_trade_date_mismatch_raises(self, mock_config, mock_api, mock_cache):
        """显式 trade_date 与结果中的 trade_date 冲突时拒绝保存"""
        mock_screener_dao = MagicMock()
        mock_screener_dao.save_screening_results = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.screener_dao = mock_screener_dao
        mock_cache.return_value = mock_cache_instance

        manager = ReviewManager()
        df = self._make_mock_df().copy()
        df["trade_date"] = ["20241230"]

        async def run_test():
            with self.assertRaises(ValueError):
                await manager.save_results("test_strategy", df, trade_date=datetime.date(2024, 12, 31))
            mock_screener_dao.save_screening_results.assert_not_called()

        asyncio.run(run_test())
