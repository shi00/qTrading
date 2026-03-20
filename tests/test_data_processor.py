import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
import datetime
import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd

from data.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.tushare_client import TushareClient
from utils.time_utils import get_now


class TestDataProcessor(unittest.TestCase):
    async def fake_run_async(self, task_type, func, *args, **kwargs):
        # Unwrap partial if present (simulating run_async logic simplified)
        import functools

        if isinstance(func, functools.partial):
            return func(*args, **kwargs)
        if kwargs:
            return func(*args, **kwargs)
        return func(*args)

    def setUp(self):
        # Patch ThreadPoolManager to run synchronously
        self.patcher_tpm = patch(
            "utils.thread_pool.ThreadPoolManager.run_async", new=self.fake_run_async,
        )
        self.patcher_tpm.start()

        # Mock TushareClient (Sync)
        self.mock_api = AsyncMock(spec=TushareClient)

        # Setup Patcher
        self.patcher_api = patch(
            "data.data_processor.TushareClient", return_value=self.mock_api,
        )
        self.patcher_api.start()

        # Patch ConfigHandler
        self.patcher_config = patch("data.data_processor.ConfigHandler")
        self.mock_config = self.patcher_config.start()
        self.mock_config.get_sync_max_concurrent_heavy.return_value = (
            5  # Configure ConfigHandler return value
        )

        # Reset Singleton State
        DataProcessor._instance = None
        DataProcessor._is_initialized = False  # Force re-init

        self.processor = DataProcessor()
        # Reset mocks
        self.mock_cache = AsyncMock(spec=CacheManager)

        # Inject mocks
        self.processor.api = self.mock_api
        self.processor.cache = self.mock_cache
        self.processor._cancel_event = asyncio.Event()  # Updated from _shutdown_event

        # CRITICAL: Propagate mocks to SyncContext used by Strategies
        if hasattr(self.processor, "context"):
            self.processor.context.api = self.processor.api
            self.processor.context.cache = self.processor.cache
            self.processor.context.processor = self.processor

        # Reset strategies if needed or rely on context propagation
        # (Strategies hold reference to self.context object)

        # Configure AsyncMocks for cache methods
        self.mock_cache.init_db = AsyncMock()
        self.mock_cache.get_latest_trade_date = AsyncMock()
        self.mock_cache.get_screening_data = AsyncMock()
        self.mock_cache.save_daily_quotes = AsyncMock()
        self.mock_cache.save_daily_indicators = AsyncMock()
        self.mock_cache.update_sync_status = AsyncMock()
        self.mock_cache.get_cached_trade_dates = AsyncMock()
        self.mock_cache.get_cached_indicator_dates = AsyncMock()
        self.mock_cache.save_financial_reports = AsyncMock()
        self.mock_cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "list_status": ["L"]}),
        )
        self.mock_cache.get_cached_financial_records = AsyncMock(return_value=set())
        self.mock_cache.get_trade_cal = AsyncMock()  # Added missing AsyncMock
        # FIX: Mock get_trade_cal_range (needed by CalendarMixin._ensure_trade_cal_impl)
        self.mock_cache.get_trade_cal_range = AsyncMock(
            return_value=("20200101", "20261231"),
        )
        # FIX: Mock save_trade_cal (needed by CalendarMixin._ensure_trade_cal_impl)
        self.mock_cache.save_trade_cal = AsyncMock()
        # FIX: Mock get_concept_count (needed by HealthCheckMixin.check_data_health)
        self.mock_cache.get_concept_count = AsyncMock(return_value=100)
        # FIX: Mock get_sync_status (needed by HealthCheckMixin._assign_basic_tier)
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["daily_quotes", "financial_reports"],
                    "last_data_date": ["20260305", "20260301"],
                    "record_count": [1000, 500],
                },
            ),
        )

        # Configure ConfigHandler return value
        self.mock_config.get_sync_max_concurrent_heavy.return_value = 5
        self.mock_config.get_sync_request_delay.return_value = 0  # Zero delay for tests

        # Configure check_comprehensive_health default for mocks
        # CRITICAL: Must include ALL tables marked 'critical' in TABLE_DEFINITIONS:
        # daily_quotes, financial_reports, daily_indicators, moneyflow_daily
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "tables": {
                    "daily_quotes": {"ratio": 1.0, "type": "stock"},
                    "daily_indicators": {"ratio": 1.0, "type": "stock"},
                    "financial_reports": {"ratio": 0.95, "type": "stock"},
                    "moneyflow_daily": {"ratio": 1.0, "type": "stock"},
                    "stock_basic": {"ratio": 1.0, "type": "global"},
                },
            },
        )

    def tearDown(self):
        self.patcher_tpm.stop()
        self.patcher_api.stop()
        self.patcher_config.stop()

    # ==========================================================
    # Section 1: Singleton & Mixin Integration Tests
    # ==========================================================

    def test_singleton(self):
        """Verify Singleton pattern"""
        p1 = DataProcessor()
        p2 = DataProcessor()
        self.assertIs(p1, p2)
        self.assertIs(p1.api, p2.api)

    def test_mixin_inheritance(self):
        """Verify DataProcessor correctly inherits from both Mixins"""
        from data.mixins.calendar_mixin import CalendarMixin
        from data.mixins.health_mixin import HealthCheckMixin

        self.assertIsInstance(self.processor, HealthCheckMixin)
        self.assertIsInstance(self.processor, CalendarMixin)

    def test_mro_resolution(self):
        """Verify MRO resolves Mixin methods correctly"""
        from data.mixins.calendar_mixin import CalendarMixin
        from data.mixins.health_mixin import HealthCheckMixin

        # check_data_health should come from HealthCheckMixin
        self.assertIs(
            DataProcessor.check_data_health, HealthCheckMixin.check_data_health,
        )
        # get_latest_trade_date should come from CalendarMixin
        self.assertIs(
            DataProcessor.get_latest_trade_date, CalendarMixin.get_latest_trade_date,
        )

    # ==========================================================
    # Section 2: CalendarMixin Tests (get_latest_trade_date, get_trade_dates, ensure_trade_cal)
    # ==========================================================

    async def async_test_get_latest_trade_date_weekday_pre_market(self):
        fixed_dt = datetime.datetime(2023, 10, 25, 10, 0, 0)  # Wed
        with patch("data.mixins.calendar_mixin.get_now", return_value=fixed_dt):
            # Reset TTL cache to force re-evaluation
            self.processor._trade_date_cache = {"ts": 0, "val": None}

            # Mock get_trade_cal to return trade days
            self.mock_cache.get_trade_cal = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "cal_date": [
                            "20231005",
                            "20231006",
                            "20231009",
                            "20231010",
                            "20231011",
                            "20231012",
                            "20231013",
                            "20231016",
                            "20231017",
                            "20231018",
                            "20231019",
                            "20231020",
                            "20231023",
                            "20231024",
                        ],
                        "is_open": [1] * 14,
                    },
                ),
            )

            date_str = await self.processor.get_latest_trade_date()
            # Pre-market Wednesday → should be Tuesday 20231024
            self.assertEqual(date_str, "20231024")

    def test_get_latest_trade_date_weekday_pre_market(self):
        asyncio.run(self.async_test_get_latest_trade_date_weekday_pre_market())

    async def async_test_get_latest_trade_date_weekday_post_market(self):
        fixed_dt = datetime.datetime(2023, 10, 25, 17, 0, 0)  # Wed post-market
        with patch("data.mixins.calendar_mixin.get_now", return_value=fixed_dt):
            self.processor._trade_date_cache = {"ts": 0, "val": None}

            self.mock_cache.get_trade_cal = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "cal_date": [
                            "20231005",
                            "20231006",
                            "20231009",
                            "20231010",
                            "20231011",
                            "20231012",
                            "20231013",
                            "20231016",
                            "20231017",
                            "20231018",
                            "20231019",
                            "20231020",
                            "20231023",
                            "20231024",
                            "20231025",
                        ],
                        "is_open": [1] * 15,
                    },
                ),
            )

            date_str = await self.processor.get_latest_trade_date()
            self.assertEqual(date_str, "20231025")

    def test_get_latest_trade_date_weekday_post_market(self):
        asyncio.run(self.async_test_get_latest_trade_date_weekday_post_market())

    async def async_test_get_latest_trade_date_weekend(self):
        """Test weekend -> should skip to Friday"""
        fixed_dt = datetime.datetime(2023, 10, 28, 12, 0, 0)  # Sat
        with patch("data.mixins.calendar_mixin.get_now", return_value=fixed_dt):
            self.processor._trade_date_cache = {"ts": 0, "val": None}

            self.mock_cache.get_trade_cal = AsyncMock(
                return_value=pd.DataFrame(
                    {
                        "cal_date": [
                            "20231013",
                            "20231016",
                            "20231017",
                            "20231018",
                            "20231019",
                            "20231020",
                            "20231023",
                            "20231024",
                            "20231025",
                            "20231026",
                            "20231027",
                        ],
                        "is_open": [1] * 11,
                    },
                ),
            )

            date_str = await self.processor.get_latest_trade_date()
            self.assertEqual(date_str, "20231027")

    def test_get_latest_trade_date_weekend(self):
        asyncio.run(self.async_test_get_latest_trade_date_weekend())

    async def async_test_get_latest_trade_date_ttl_cache(self):
        """Test that TTL cache returns cached value within 5 min"""
        self.processor._trade_date_cache = {
            "ts": __import__("time").time(),  # just now
            "val": "20230101",
        }
        result = await self.processor.get_latest_trade_date()
        self.assertEqual(result, "20230101")
        # No cache mock calls should have been made (cache hit)
        self.mock_cache.get_trade_cal.assert_not_called()

    def test_get_latest_trade_date_ttl_cache(self):
        asyncio.run(self.async_test_get_latest_trade_date_ttl_cache())

    async def async_test_get_trade_dates(self):
        """Test get_trade_dates returns sorted list"""
        mock_df = pd.DataFrame(
            {"cal_date": ["20230103", "20230101", "20230102"], "is_open": [1, 1, 1]},
        )
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_df)

        dates = await self.processor.get_trade_dates("20230101", "20230103")
        self.assertEqual(dates, ["20230101", "20230102", "20230103"])

    def test_get_trade_dates(self):
        asyncio.run(self.async_test_get_trade_dates())

    async def async_test_get_trade_dates_fallback(self):
        """Test get_trade_dates fallback when DB fails"""
        self.mock_cache.get_trade_cal_range = AsyncMock(
            side_effect=Exception("DB Error"),
        )
        self.mock_cache.get_trade_cal = AsyncMock(side_effect=Exception("DB Error"))

        dates = await self.processor.get_trade_dates("20230102", "20230106")
        # Fallback should return weekday-only dates (Mon-Fri)
        self.assertEqual(
            dates, ["20230102", "20230103", "20230104", "20230105", "20230106"],
        )

    def test_get_trade_dates_fallback(self):
        asyncio.run(self.async_test_get_trade_dates_fallback())

    async def async_test_ensure_trade_cal_memory_cache(self):
        """Test ensure_trade_cal memory cache prevents repeated DB/API calls"""
        # First call: should invoke _ensure_trade_cal_impl
        self.processor._trade_cal_cache = {}

        with patch.object(
            self.processor,
            "_ensure_trade_cal_impl",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_impl:
            result1 = await self.processor.ensure_trade_cal("20230101")
            self.assertTrue(result1)
            mock_impl.assert_called_once()

            # Second call with same date: should use memory cache
            mock_impl.reset_mock()
            result2 = await self.processor.ensure_trade_cal("20230101")
            self.assertTrue(result2)
            mock_impl.assert_not_called()

    def test_ensure_trade_cal_memory_cache(self):
        asyncio.run(self.async_test_ensure_trade_cal_memory_cache())

    # ==========================================================
    # Section 3: HealthCheckMixin Tests (_assign_basic_tier, check_data_health)
    # ==========================================================

    async def async_test_assign_basic_tier_gold(self):
        """Test _assign_basic_tier assigns GOLD (3) when both quotes and financials are fresh"""
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["daily_quotes", "financial_reports"],
                    "last_data_date": [
                        get_now().strftime("%Y%m%d"),
                        get_now().strftime("%Y%m%d"),
                    ],
                    "record_count": [1000, 500],
                },
            ),
        )

        await self.processor._assign_basic_tier()
        self.assertEqual(self.processor._quality_tier, 3)

    def test_assign_basic_tier_gold(self):
        asyncio.run(self.async_test_assign_basic_tier_gold())

    async def async_test_assign_basic_tier_critical(self):
        """Test _assign_basic_tier assigns CRITICAL (0) when no sync records"""
        self.mock_cache.get_sync_status = AsyncMock(return_value=pd.DataFrame())

        await self.processor._assign_basic_tier()
        self.assertEqual(self.processor._quality_tier, 0)

    def test_assign_basic_tier_critical(self):
        asyncio.run(self.async_test_assign_basic_tier_critical())

    async def async_test_assign_basic_tier_bronze(self):
        """Test _assign_basic_tier assigns BRONZE (1) when quotes are stale"""
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["daily_quotes"],
                    "last_data_date": ["20200101"],
                    "record_count": [1000],
                },
            ),
        )
        # Mock get_latest_trade_date to also return stale date
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20200101")

        await self.processor._assign_basic_tier()
        self.assertEqual(self.processor._quality_tier, 1)

    def test_assign_basic_tier_bronze(self):
        asyncio.run(self.async_test_assign_basic_tier_bronze())

    async def async_test_check_data_health(self):
        """Test health check logic"""
        # Scenario 1: Healthy (Green)
        # Mock trade dates matching local cache exactly
        mock_trade_dates = ["20230101", "20230102", "20230103"]
        mock_cal_df = pd.DataFrame({"cal_date": mock_trade_dates, "is_open": [1, 1, 1]})
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_cal_df)
        self.mock_cache.get_cached_trade_dates = AsyncMock(
            return_value={"20230101", "20230102", "20230103"},
        )

        with patch.object(
            self.processor,
            "get_latest_trade_date",
            new_callable=AsyncMock,
            return_value="20230103",
        ), patch.object(
            self.processor,
            "get_trade_dates",
            new_callable=AsyncMock,
            return_value=mock_trade_dates,
        ):
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "green")

        # Scenario 2: Lagging (Yellow)
        mock_trade_dates_2 = ["20230101", "20230102", "20230103", "20230104"]
        mock_cal_df_2 = pd.DataFrame(
            {"cal_date": mock_trade_dates_2, "is_open": [1] * 4},
        )
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_cal_df_2)
        # Only have 3 days local, missing 1
        self.mock_cache.get_cached_trade_dates = AsyncMock(
            return_value={"20230101", "20230102", "20230103"},
        )

        with patch.object(
            self.processor,
            "get_latest_trade_date",
            new_callable=AsyncMock,
            return_value="20230104",
        ), patch.object(
            self.processor,
            "get_trade_dates",
            new_callable=AsyncMock,
            return_value=mock_trade_dates_2,
        ):
            # Reset health cache to force fresh eval
            self.processor._health_cache = {"time": 0, "data": None}
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "yellow")

        # Scenario 3: Missing Critical Tables (Red)
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "tables": {
                    "daily_quotes": {"ratio": 0.0, "type": "stock"},
                    "daily_indicators": {"ratio": 0.0, "type": "stock"},
                    "financial_reports": {"ratio": 0.0, "type": "stock"},
                },
            },
        )
        dates_3 = [f"202301{i:02d}" for i in range(10, 20)]
        self.mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame(
                {"cal_date": dates_3, "is_open": [1] * len(dates_3)},
            ),
        )
        self.mock_cache.get_cached_trade_dates = AsyncMock(return_value={"20230110"})

        with patch.object(
            self.processor,
            "get_latest_trade_date",
            new_callable=AsyncMock,
            return_value="20230119",
        ), patch.object(
            self.processor,
            "get_trade_dates",
            new_callable=AsyncMock,
            return_value=dates_3,
        ):
            self.processor._health_cache = {"time": 0, "data": None}
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "red")

    def test_check_data_health(self):
        asyncio.run(self.async_test_check_data_health())

    # ==========================================================
    # Section 4: DataProcessor Core Methods
    # ==========================================================

    async def async_test_init_data(self):
        await self.processor.init_data()
        self.processor.cache.init_db.assert_called_once()

    def test_init_data(self):
        asyncio.run(self.async_test_init_data())

    # --- Sync Daily Market Snapshot Tests ---

    async def async_test_sync_daily_market_cache_hit(self):
        """Test that data is NOT fetched if cache exists"""
        trade_date = "20231025"

        # Mock Cache existence
        self.processor.cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame({"close": [10]}),
        )

        await self.processor.sync_daily_market_snapshot(trade_date)

        self.processor.cache.check_data_exists.assert_called_with(trade_date)
        self.processor.api.get_daily_quotes.assert_not_called()

    async def async_test_sync_daily_market_cache_miss(self):
        """Test cache miss fetches from API and saves"""
        target_date = "20231025"
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20200101")
        self.mock_cache.check_data_exists = AsyncMock(return_value=False)

        mock_quotes = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "trade_date": ["20231025"]},
        )
        mock_basic = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "trade_date": ["20231025"], "pe": [10]},
        )

        self.mock_api.get_daily_quotes.return_value = mock_quotes
        self.mock_api.get_daily_basic.return_value = mock_basic

        self.mock_cache.save_daily_quotes = AsyncMock(return_value=1)
        self.mock_cache.save_daily_indicators = AsyncMock(return_value=1)
        self.mock_cache.update_sync_status = AsyncMock()
        self.mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame({"ts_code": ["000001.SZ"], "pe": [10]}),
        )

        df = await self.processor.sync_daily_market_snapshot(target_date)

        self.assertIsNotNone(df)
        self.assertIn("pe", df.columns)
        self.mock_cache.save_daily_quotes.assert_called()
        self.mock_cache.save_daily_indicators.assert_called()

    def test_sync_daily_market_snapshot(self):
        asyncio.run(self.async_test_sync_daily_market_cache_hit())
        asyncio.run(self.async_test_sync_daily_market_cache_miss())

    # --- sync_stock_basic ---

    async def async_test_sync_stock_basic(self):
        """Test sync_stock_basic calls api.get_stock_list directly"""
        mock_df = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["PingAn"]})
        self.mock_api.get_stock_list = AsyncMock(return_value=mock_df)
        self.mock_cache.save_stock_basic = AsyncMock(return_value=1)

        # Reset the sync lock flag
        self.processor._is_syncing_basic = False

        count = await self.processor.sync_stock_basic()

        self.assertEqual(count, 1)
        self.mock_cache.save_stock_basic.assert_called()

    def test_sync_stock_basic(self):
        asyncio.run(self.async_test_sync_stock_basic())

    # --- Historical Sync & Circuit Breaker Tests ---

    async def async_test_sync_historical_breakpoint_resume(self):
        """Test that existing dates are skipped"""
        days = 5
        mock_dates = ["20230105", "20230104", "20230103", "20230102", "20230101"]
        mock_df = pd.DataFrame({"cal_date": mock_dates, "is_open": [1] * 5})
        self.mock_api.get_trade_cal.return_value = mock_df
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_df)

        self.mock_cache.get_cached_trade_dates = AsyncMock(
            return_value={"20230105", "20230104"},
        )
        self.mock_cache.get_cached_indicator_dates = AsyncMock(
            return_value={"20230105", "20230104"},
        )

        historical_strategy = self.processor.strategies["historical"]
        with patch.object(
            historical_strategy, "sync_daily_market_snapshot", new_callable=AsyncMock,
        ) as mock_sync:
            await self.processor.sync_historical_data(days=days)

            self.assertEqual(mock_sync.call_count, 3)
            call_args = [c.args[0] for c in mock_sync.call_args_list]
            self.assertIn("20230103", call_args)

    def test_sync_historical(self):
        asyncio.run(self.async_test_sync_historical_breakpoint_resume())

    # --- Financial Reports Tests ---

    async def async_test_sync_financial_reports(self):
        """Test financial report syncing logic"""
        periods = ["20230331"]

        mock_income = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20230331"],
                "ann_date": ["20230401"],
                "n_income": [1000],
                "total_revenue": [5000],
            },
        )
        mock_balance = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20230331"],
                "ann_date": ["20230401"],
                "total_assets": [10000],
                "total_liab": [5000],
            },
        )
        mock_indicator = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20230331"],
                "ann_date": ["20230401"],
                "roe": [10.5],
            },
        )

        self.mock_api.get_income.return_value = mock_income
        self.mock_api.get_balancesheet.return_value = mock_balance
        self.mock_api.get_fina_indicator.return_value = mock_indicator

        self.mock_cache.save_financial_reports = AsyncMock(return_value=1)
        self.mock_cache.update_sync_status = AsyncMock()

        count = await self.processor.sync_financial_reports(periods=periods)

        self.assertEqual(count, 1)
        self.assertTrue(self.mock_cache.save_financial_reports.call_count >= 3)

        saved_dfs = [
            c[0][0] for c in self.mock_cache.save_financial_reports.call_args_list
        ]

        has_assets = any(
            "total_assets" in df.columns and df.iloc[0]["total_assets"] == 10000
            for df in saved_dfs
        )
        has_roe = any(
            "roe" in df.columns and df.iloc[0]["roe"] == 10.5 for df in saved_dfs
        )

        self.assertTrue(has_assets, "total_assets not saved")
        self.assertTrue(has_roe, "roe not saved")

    def test_financial_reports(self):
        asyncio.run(self.async_test_sync_financial_reports())

    # --- prepare_screening_context ---

    async def async_test_prepare_screening_context(self):
        """Test prepare_screening_context"""
        self.mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame({"pe": [10]}),
        )
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20230101")

        self.mock_cache.get_northbound = AsyncMock(
            return_value=pd.DataFrame({"ratio": [5]}),
        )
        self.mock_cache.get_moneyflow = AsyncMock(
            return_value=pd.DataFrame({"net_mf_vol": [100]}),
        )
        self.mock_cache.get_top_list = AsyncMock(
            return_value=pd.DataFrame({"net_rate": [10.5]}),
        )
        self.mock_cache.get_block_trade = AsyncMock(
            return_value=pd.DataFrame({"amt": [5000]}),
        )

        context = await self.processor.prepare_screening_context()

        self.assertIn("screening_data", context)
        self.assertIn("northbound_data", context)
        self.assertIn("moneyflow_data", context)
        self.assertIn("top_list", context)
        self.assertIn("block_trade", context)

    def test_prepare_screening_context(self):
        asyncio.run(self.async_test_prepare_screening_context())

    # --- Cancel & Lifecycle ---

    async def async_test_cancel_propagation(self):
        """Test cancel propagation to strategies"""
        for strategy in self.processor.strategies.values():
            strategy.cancel = AsyncMock()

        await self.processor.request_cancel()

        self.assertTrue(self.processor.is_cancelled())
        for strategy in self.processor.strategies.values():
            strategy.cancel.assert_called_once()

    def test_cancel_propagation(self):
        asyncio.run(self.async_test_cancel_propagation())

    async def async_test_close_resources(self):
        """Test close gracefully stops and closes cache"""
        for strategy in self.processor.strategies.values():
            strategy.cancel = AsyncMock()
        self.mock_cache.close = AsyncMock()

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await self.processor.close()

        self.mock_cache.close.assert_called_once()

    def test_close_resources(self):
        asyncio.run(self.async_test_close_resources())

    # --- Market Overview with Cache ---

    async def async_test_get_market_overview_uses_memory_cache(self):
        """Verify get_market_overview uses memory cache to skip ensure_trade_cal"""

        self.mock_cache.get_trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20230101", "20230102"], "is_open": [1, 1]},
        )

        # Mock API methods to return valid DataFrames
        mock_result_df = pd.DataFrame(
            {"close": [3000], "pct_chg": [1.0], "north_money": [500]},
        )
        self.mock_api.get_index_daily = AsyncMock(return_value=mock_result_df)
        self.mock_api.get_moneyflow_hsgt = AsyncMock(return_value=mock_result_df)

        # Pre-seed the _trade_cal_cache so that ensure_trade_cal sees a cache hit
        today_str = get_now().strftime("%Y%m%d")
        self.processor._trade_cal_cache = {"date": today_str}

        with patch.object(
            self.processor,
            "_ensure_trade_cal_impl",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_impl:
            await self.processor.get_market_overview()
            # Because _trade_cal_cache is pre-seeded with today's date,
            # ensure_trade_cal should SKIP the _ensure_trade_cal_impl call
            mock_impl.assert_not_called()

    def test_get_market_overview_uses_memory_cache(self):
        asyncio.run(self.async_test_get_market_overview_uses_memory_cache())

    # --- Retry Mechanism ---

    async def async_test_retry_mechanism_logic(self):
        """Test the retry logical branch in sync_historical_data"""
        days = 1
        mock_dates = ["20230101", "20230102"]
        mock_df = pd.DataFrame({"cal_date": mock_dates, "is_open": [1] * 2})
        self.mock_api.get_trade_cal.return_value = mock_df
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_df)

        self.mock_cache.get_cached_trade_dates = AsyncMock(return_value=set())
        self.mock_cache.get_cached_indicator_dates = AsyncMock(return_value=set())

        call_count = 0

        async def side_effect(date):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Network Error")
            return pd.DataFrame({"a": [1]})

        historical_strategy = self.processor.strategies["historical"]
        with patch.object(
            historical_strategy, "sync_daily_market_snapshot", side_effect=side_effect,
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await self.processor.sync_historical_data(days=days)

            self.assertTrue(call_count > 2)

    def test_retry_mechanism(self):
        asyncio.run(self.async_test_retry_mechanism_logic())

    # ==========================================================
    # Section 5: run_quality_scan (HealthCheckMixin)
    # ==========================================================

    async def async_test_run_quality_scan_tier2(self):
        """Test run_quality_scan produces Tier 2 result with good data"""
        import datetime as dt

        # Fix time for deterministic date calculations
        fixed_now = dt.datetime(2025, 12, 1)

        # Mock stock basic
        stocks = [f"{i:06d}.SZ" for i in range(1, 6)]
        self.mock_cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": stocks, "list_status": ["L"] * 5}),
        )

        # Generate trade dates ending AT fixed_now (critical for recency check: lag < 5)
        end_date = fixed_now.strftime("%Y%m%d")
        start_date = (fixed_now - dt.timedelta(days=365)).strftime("%Y%m%d")
        cal_dates = pd.bdate_range(start_date, end_date).strftime("%Y%m%d").tolist()

        self.mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame(
                {"cal_date": cal_dates, "is_open": [1] * len(cal_dates)},
            ),
        )

        # Mock batch daily quotes: each stock has full coverage of ALL cal_dates
        rows = []
        for code in stocks:
            for d in cal_dates:
                rows.append(
                    {"ts_code": code, "trade_date": d, "close": 10.0, "vol": 1000},
                )
        batch_df = pd.DataFrame(rows)
        self.mock_cache.get_daily_quotes = AsyncMock(return_value=batch_df)

        progress_calls = []

        def progress_cb(current, total, msg):
            progress_calls.append((current, total, msg))

        with patch("data.mixins.health_mixin.get_now", return_value=fixed_now):
            with patch("random.sample", side_effect=lambda pop, k: pop[:k]):
                result = await self.processor.run_quality_scan(
                    sample_size=5, progress_callback=progress_cb,
                )

        self.assertIn("tier", result)
        self.assertIn("score", result)
        self.assertGreaterEqual(result["tier"], 2)
        self.assertGreater(result["score"], 90)
        self.assertTrue(len(progress_calls) > 0)
        # Verify progress reported completion
        self.assertEqual(progress_calls[-1][0], 100)

    def test_run_quality_scan_tier2(self):
        asyncio.run(self.async_test_run_quality_scan_tier2())

    async def async_test_run_quality_scan_empty_stocks(self):
        """Test run_quality_scan when no active stocks exist"""
        self.mock_cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())

        result = await self.processor.run_quality_scan()
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["tier"], 0)

    def test_run_quality_scan_empty_stocks(self):
        asyncio.run(self.async_test_run_quality_scan_empty_stocks())

    async def async_test_run_quality_scan_cancellation(self):
        """Test run_quality_scan respects cancellation"""
        stocks = [f"{i:06d}.SZ" for i in range(1, 20)]
        self.mock_cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {"ts_code": stocks, "list_status": ["L"] * len(stocks)},
            ),
        )
        self.mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20230101"], "is_open": [1]}),
        )
        self.mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20230101"],
                    "close": [10.0],
                    "vol": [100],
                },
            ),
        )

        # Set cancel after clear_cancel is called
        original_clear = self.processor.clear_cancel

        def cancel_after_clear():
            original_clear()
            self.processor._get_cancel_event().set()

        with patch.object(
            self.processor, "clear_cancel", side_effect=cancel_after_clear,
        ), patch("random.sample", side_effect=lambda pop, k: pop[:k]):
            result = await self.processor.run_quality_scan(sample_size=10)

        # Should return early with minimal data processed
        self.assertEqual(result["tier"], 1)

    def test_run_quality_scan_cancellation(self):
        asyncio.run(self.async_test_run_quality_scan_cancellation())

    # ==========================================================
    # Section 6: _ensure_trade_cal_impl (CalendarMixin)
    # ==========================================================

    async def async_test_ensure_trade_cal_impl_no_data(self):
        """Test _ensure_trade_cal_impl when DB has no calendar data → full fetch"""
        self.mock_cache.get_trade_cal_range = AsyncMock(return_value=(None, None))
        # api.get_trade_cal is called with await, so must be AsyncMock
        self.processor.api.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame({"cal_date": ["20230101"], "is_open": [1]}),
        )
        self.mock_cache.save_trade_cal = AsyncMock()

        result = await self.processor._ensure_trade_cal_impl("20230301")
        self.assertTrue(result)
        self.mock_cache.save_trade_cal.assert_called_once()

    def test_ensure_trade_cal_impl_no_data(self):
        asyncio.run(self.async_test_ensure_trade_cal_impl_no_data())

    async def async_test_ensure_trade_cal_impl_already_covered(self):
        """Test _ensure_trade_cal_impl when DB already covers the range → no API call"""
        self.mock_cache.get_trade_cal_range = AsyncMock(
            return_value=("20200101", "20261231"),
        )

        result = await self.processor._ensure_trade_cal_impl("20260305")
        self.assertTrue(result)
        # API should NOT have been called
        self.processor.api.get_trade_cal.assert_not_called()

    def test_ensure_trade_cal_impl_already_covered(self):
        asyncio.run(self.async_test_ensure_trade_cal_impl_already_covered())

    async def async_test_ensure_trade_cal_impl_gap_fill(self):
        """Test _ensure_trade_cal_impl fills gaps when DB range doesn't extend to end_date"""
        self.mock_cache.get_trade_cal_range = AsyncMock(
            return_value=("20200101", "20250101"),
        )
        # api.get_trade_cal is called with await, so must be AsyncMock
        self.processor.api.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame(
                {"cal_date": ["20250102", "20250103"], "is_open": [1, 1]},
            ),
        )
        self.mock_cache.save_trade_cal = AsyncMock()

        result = await self.processor._ensure_trade_cal_impl("20260301")
        self.assertTrue(result)
        self.mock_cache.save_trade_cal.assert_called()

    def test_ensure_trade_cal_impl_gap_fill(self):
        asyncio.run(self.async_test_ensure_trade_cal_impl_gap_fill())

    # ==========================================================
    # Section 7: _assign_basic_tier Silver path
    # ==========================================================

    async def async_test_assign_basic_tier_silver(self):
        """Test _assign_basic_tier assigns SILVER (2) when quotes fresh but no financial data"""
        import datetime as dt

        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["daily_quotes"],
                    "last_data_date": [get_now().strftime("%Y%m%d")],
                    "record_count": [1000],
                },
            ),
        )

        await self.processor._assign_basic_tier()
        self.assertEqual(self.processor._quality_tier, 2)

    def test_assign_basic_tier_silver(self):
        asyncio.run(self.async_test_assign_basic_tier_silver())

    # ==========================================================
    # Section 8: ensure_trade_cal with required_start_date bypasses cache
    # ==========================================================

    async def async_test_ensure_trade_cal_required_start_bypasses_cache(self):
        """Test that ensure_trade_cal with required_start_date always calls impl"""
        self.processor._trade_cal_cache = {"date": "20230101"}  # Pre-seed cache

        with patch.object(
            self.processor,
            "_ensure_trade_cal_impl",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_impl:
            # With required_start_date, cache should be bypassed even if date matches
            result = await self.processor.ensure_trade_cal(
                "20230101", required_start_date="20200101",
            )
            self.assertTrue(result)
            mock_impl.assert_called_once_with("20230101", "20200101")

    def test_ensure_trade_cal_required_start_bypasses_cache(self):
        asyncio.run(self.async_test_ensure_trade_cal_required_start_bypasses_cache())

    # ==========================================================
    # Section 9: sync_daily_market_snapshot auto-resolves trade_date
    # ==========================================================

    async def async_test_sync_daily_auto_resolves_date(self):
        """Test sync_daily_market_snapshot calls get_latest_trade_date when date is None"""
        with patch.object(
            self.processor,
            "get_latest_trade_date",
            new_callable=AsyncMock,
            return_value="20230103",
        ) as mock_latest:
            self.mock_cache.check_data_exists = AsyncMock(return_value=True)
            self.mock_cache.get_screening_data = AsyncMock(
                return_value=pd.DataFrame({"close": [10]}),
            )

            await self.processor.sync_daily_market_snapshot(trade_date=None)
            mock_latest.assert_called_once()

    def test_sync_daily_auto_resolves_date(self):
        asyncio.run(self.async_test_sync_daily_auto_resolves_date())


class TestScreenerDaoDynamicCols(unittest.TestCase):
    """Tests for P2-M2: ScreenerDao dynamic column reflection"""

    def test_sh_base_cols_excludes_thinking(self):
        """Verify SH_BASE_COLS dynamically reflects columns and excludes 'thinking'"""
        from data.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)  # Create without __init__
        cols_str = dao.SH_BASE_COLS

        col_list = [c.strip() for c in cols_str.split(",")]

        # 'thinking' must NOT be in the base columns
        self.assertNotIn("thinking", col_list)
        # Critical columns must be present
        self.assertIn("id", col_list)
        self.assertIn("trade_date", col_list)
        self.assertIn("ts_code", col_list)
        self.assertIn("ai_score", col_list)
        self.assertIn("prediction_result", col_list)

    def test_sh_full_cols_includes_thinking(self):
        """Verify SH_FULL_COLS includes 'thinking'"""
        from data.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        full_cols = dao.SH_FULL_COLS

        self.assertIn("thinking", full_cols)
        # Should end with ", thinking"
        self.assertTrue(full_cols.endswith(", thinking"))

    def test_sh_base_cols_matches_model(self):
        """Verify SH_BASE_COLS count matches ScreeningHistory columns minus 'thinking'"""
        from data.daos.screener_dao import ScreenerDao
        from data.models import ScreeningHistory

        dao = ScreenerDao.__new__(ScreenerDao)
        col_list = [c.strip() for c in dao.SH_BASE_COLS.split(",")]

        expected_count = len(ScreeningHistory.__table__.columns) - 1  # minus thinking
        self.assertEqual(len(col_list), expected_count)

    def test_sh_base_cols_cached(self):
        """Verify cached_property only computes once"""
        from data.daos.screener_dao import ScreenerDao

        dao = ScreenerDao.__new__(ScreenerDao)
        result1 = dao.SH_BASE_COLS
        result2 = dao.SH_BASE_COLS

        # cached_property means identity should match
        self.assertIs(result1, result2)


if __name__ == "__main__":
    unittest.main()
