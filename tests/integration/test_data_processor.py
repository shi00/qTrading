import asyncio
import datetime
import threading
import unittest
from unittest.mock import AsyncMock, patch

import pandas as pd

from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.external.tushare_client import TushareClient
from utils.time_utils import get_now
import pytest


pytestmark = pytest.mark.integration


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
            "utils.thread_pool.ThreadPoolManager.run_async",
            new=self.fake_run_async,
        )
        self.patcher_tpm.start()

        # Mock TushareClient (Sync)
        self.mock_api = AsyncMock(spec=TushareClient)

        # Setup Patcher
        self.patcher_api = patch(
            "data.data_processor.TushareClient",
            return_value=self.mock_api,
        )
        self.patcher_api.start()

        # Patch ConfigHandler (both DataProcessor and HealthCheckMixin import paths)
        self.patcher_config = patch("data.data_processor.ConfigHandler")
        self.mock_config = self.patcher_config.start()
        self.mock_config.get_sync_max_concurrent_heavy.return_value = 5
        self.patcher_config_mixin = patch("utils.config_handler.ConfigHandler.get_init_history_years", return_value=3)
        self.patcher_config_mixin.start()

        # Reset Singleton State
        DataProcessor._instance = None
        DataProcessor._initialized = False  # Force re-init

        self.processor = DataProcessor()
        # Reset mocks
        self.mock_cache = AsyncMock(spec=CacheManager)

        # Inject mocks
        self.processor.api = self.mock_api
        self.processor.cache = self.mock_cache
        self.processor._cancel_event = threading.Event()  # Updated from _shutdown_event  # type: ignore[untyped]
        # CRITICAL: Inject mocks into TradeCalendarService
        if hasattr(self.processor, "trade_calendar"):
            self.processor.trade_calendar._cache = self.mock_cache
            self.processor.trade_calendar._api = self.mock_api

        # CRITICAL: Propagate mocks to SyncContext used by Strategies
        if hasattr(self.processor, "context"):
            self.processor.context.api = self.processor.api
            self.processor.context.cache = self.processor.cache
            self.processor.context.processor = self.processor  # type: ignore[untyped]
        from unittest.mock import MagicMock

        self.mock_cache.engine = MagicMock()
        self.mock_cache.engine.begin = MagicMock()
        self.mock_cache.engine.begin.return_value.__aenter__ = AsyncMock()
        self.mock_cache.engine.begin.return_value.__aexit__ = AsyncMock()

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
        # FIX: Mock get_field_completeness (needed by HealthCheckMixin)
        mock_quote_dao = MagicMock()
        mock_quote_dao.get_field_completeness = AsyncMock(return_value={})
        self.mock_cache.quote_dao = mock_quote_dao
        self.mock_cache.get_field_completeness = AsyncMock(return_value={})
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
        self.mock_config.get_sync_request_delay.return_value = 0
        self.mock_config.get_init_history_years.return_value = 3

        # Configure check_comprehensive_health default for mocks
        # CRITICAL: Must include ALL tables marked 'critical' in TABLE_DEFINITIONS:
        # daily_quotes, financial_reports, daily_indicators, moneyflow_daily
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "global_trade_days": 750,
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
        self.patcher_config_mixin.stop()
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
            DataProcessor.check_data_health,
            HealthCheckMixin.check_data_health,
        )
        # get_latest_trade_date should come from CalendarMixin
        self.assertIs(
            DataProcessor.get_latest_trade_date,
            CalendarMixin.get_latest_trade_date,
        )

    # ==========================================================
    # Section 2: CalendarMixin Tests (get_latest_trade_date, get_trade_dates, ensure_trade_cal)
    # ==========================================================

    async def async_test_get_latest_trade_date_weekday_pre_market(self):
        fixed_dt = datetime.datetime(2023, 10, 25, 10, 0, 0)  # Wed pre-market
        with patch("data.domain_services.trade_calendar_service.get_now", return_value=fixed_dt):
            self.processor.trade_calendar._latest_trade_date_cache = {
                "ts": 0,
                "val": None,
            }

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

            date_obj = await self.processor.get_latest_trade_date()
            # Pre-market Wednesday - should be Tuesday 20231024
            self.assertEqual(date_obj, datetime.date(2023, 10, 24))

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    # filterwarnings: 直接调用 CalendarMixin facade（DataProcessor.get_latest_trade_date）
    # 验证 TradeCalendarService 业务逻辑；facade 弃用警告为 incidental 噪声，
    # facade 弃用契约由 tests/unit/test_calendar_mixin.py 单测覆盖。
    def test_get_latest_trade_date_weekday_pre_market(self):
        asyncio.run(self.async_test_get_latest_trade_date_weekday_pre_market())

    async def async_test_get_latest_trade_date_weekday_post_market(self):
        fixed_dt = datetime.datetime(2023, 10, 25, 17, 0, 0)  # Wed post-market
        with patch("data.domain_services.trade_calendar_service.get_now", return_value=fixed_dt):
            self.processor.trade_calendar._latest_trade_date_cache = {
                "ts": 0,
                "val": None,
            }

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

            date_obj = await self.processor.get_latest_trade_date()
            self.assertEqual(date_obj, datetime.date(2023, 10, 25))

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    # filterwarnings: 直接调用 CalendarMixin facade，见 test_get_latest_trade_date_weekday_pre_market 注释。
    def test_get_latest_trade_date_weekday_post_market(self):
        asyncio.run(self.async_test_get_latest_trade_date_weekday_post_market())

    async def async_test_get_latest_trade_date_weekend(self):
        """Test weekend -> should skip to Friday"""
        fixed_dt = datetime.datetime(2023, 10, 28, 12, 0, 0)  # Sat
        with patch("data.domain_services.trade_calendar_service.get_now", return_value=fixed_dt):
            self.processor.trade_calendar._latest_trade_date_cache = {
                "ts": 0,
                "val": None,
            }

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

            date_obj = await self.processor.get_latest_trade_date()
            self.assertEqual(date_obj, datetime.date(2023, 10, 27))

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    # filterwarnings: 直接调用 CalendarMixin facade，见 test_get_latest_trade_date_weekday_pre_market 注释。
    def test_get_latest_trade_date_weekend(self):
        asyncio.run(self.async_test_get_latest_trade_date_weekend())

    async def async_test_get_latest_trade_date_ttl_cache(self):
        """Test that TTL cache returns cached value within 5 min"""
        import time

        self.processor.trade_calendar._latest_trade_date_cache = {
            "ts": time.time(),  # just now
            "val": datetime.date(2023, 1, 1),
        }
        result = await self.processor.get_latest_trade_date()
        self.assertEqual(result, datetime.date(2023, 1, 1))
        # No cache mock calls should have been made (cache hit)
        self.mock_cache.get_trade_cal.assert_not_called()

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    # filterwarnings: 直接调用 CalendarMixin facade，见 test_get_latest_trade_date_weekday_pre_market 注释。
    def test_get_latest_trade_date_ttl_cache(self):
        asyncio.run(self.async_test_get_latest_trade_date_ttl_cache())

    async def async_test_get_trade_dates(self):
        """Test get_trade_dates returns sorted list of date objects"""
        mock_df = pd.DataFrame(
            {"cal_date": ["20230103", "20230101", "20230102"], "is_open": [1, 1, 1]},
        )
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_df)

        dates = await self.processor.get_trade_dates("20230101", "20230103")
        self.assertEqual(
            dates,
            [
                datetime.date(2023, 1, 1),
                datetime.date(2023, 1, 2),
                datetime.date(2023, 1, 3),
            ],
        )

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    # filterwarnings: 直接调用 CalendarMixin facade（DataProcessor.get_trade_dates），
    # 验证 TradeCalendarService 业务逻辑；facade 弃用警告为 incidental 噪声。
    def test_get_trade_dates(self):
        asyncio.run(self.async_test_get_trade_dates())

    async def async_test_get_trade_dates_fallback(self):
        """Test get_trade_dates fallback when DB fails"""
        self.mock_cache.get_trade_cal_range = AsyncMock(
            side_effect=Exception("DB Error"),
        )
        self.mock_cache.get_trade_cal = AsyncMock(side_effect=Exception("DB Error"))

        dates = await self.processor.get_trade_dates("20230102", "20230106")
        # Fallback should return weekday-only dates via OfflineCalendar
        # Note: 2023-01-02 is Monday but 元旦假期调休, A股休市
        # 2023-01-03 (Tue) to 2023-01-06 (Fri) are trading days
        self.assertEqual(
            dates,
            [
                datetime.date(2023, 1, 3),
                datetime.date(2023, 1, 4),
                datetime.date(2023, 1, 5),
                datetime.date(2023, 1, 6),
            ],
        )

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    # filterwarnings: 直接调用 CalendarMixin facade，见 test_get_trade_dates 注释。
    def test_get_trade_dates_fallback(self):
        asyncio.run(self.async_test_get_trade_dates_fallback())

    async def async_test_get_stock_history_prefers_latest_closed_trade_date(self):
        """盘中读取历史行情时，应以最近闭市日作为结束日。"""
        fixed_now = datetime.datetime(2026, 3, 9, 10, 0, 0)
        self.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2026, 3, 6))
        self.processor.trade_calendar.get_trade_dates = AsyncMock(
            return_value=[
                datetime.date(2026, 3, 4),
                datetime.date(2026, 3, 5),
                datetime.date(2026, 3, 6),
            ]
        )
        self.mock_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())

        with patch("data.data_processor.get_now", return_value=fixed_now):
            await self.processor.get_stock_history("000001.SZ", days=2)

        self.processor.trade_calendar.get_trade_dates.assert_awaited_once_with(
            start_date=datetime.date(2026, 3, 2),
            end_date=datetime.date(2026, 3, 6),
        )
        self.mock_cache.get_daily_quotes.assert_awaited_once_with(
            ts_code="000001.SZ",
            start_date=datetime.date(2026, 3, 5),
            end_date=datetime.date(2026, 3, 6),
        )

    def test_get_stock_history_prefers_latest_closed_trade_date(self):
        asyncio.run(self.async_test_get_stock_history_prefers_latest_closed_trade_date())

    async def async_test_ensure_trade_cal_memory_cache(self):
        """Test ensure_trade_cal correctly delegates to TradeCalendarService"""
        result = await self.processor.ensure_trade_cal("20230101")
        self.assertTrue(result)

    def test_ensure_trade_cal_memory_cache(self):
        asyncio.run(self.async_test_ensure_trade_cal_memory_cache())

    # ==========================================================
    # Section 3: HealthCheckMixin Tests (_assign_basic_tier, check_data_health)
    # ==========================================================

    async def async_test_assign_basic_tier_gold(self):
        """Test _assign_basic_tier assigns SILVER (2) in fast-path when all critical tables are fresh.

        Note: GOLD (3) is unreachable in fast-path because field-level fundamental
        completeness (avg_fundamental) is unavailable. Use check_data_health for GOLD.
        """
        today = get_now().strftime("%Y%m%d")
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": [
                        "daily_quotes",
                        "financial_reports",
                        "daily_indicators",
                        "moneyflow_daily",
                    ],
                    "last_data_date": [today, today, today, today],
                    "record_count": [1000, 500, 800, 600],
                    "last_result_status": ["ok", "ok", "ok", "ok"],
                },
            ),
        )

        await self.processor._assign_basic_tier()
        self.assertEqual(self.processor._quality_tier, 2)

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

    async def async_test_check_data_health_green(self):
        """Test health check logic - Healthy (Green)"""
        mock_trade_dates = ["20230101", "20230102", "20230103"]
        mock_cal_df = pd.DataFrame({"cal_date": mock_trade_dates, "is_open": [1, 1, 1]})
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_cal_df)
        self.mock_cache.get_cached_trade_dates = AsyncMock(
            return_value={"20230101", "20230102", "20230103"},
        )

        with (
            patch.object(
                self.processor,
                "get_latest_trade_date",
                new_callable=AsyncMock,
                return_value="20230103",
            ),
            patch.object(
                self.processor,
                "get_trade_dates",
                new_callable=AsyncMock,
                return_value=mock_trade_dates,
            ),
        ):
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "green")

    def test_check_data_health_green(self):
        asyncio.run(self.async_test_check_data_health_green())

    async def async_test_check_data_health_yellow(self):
        """Test health check logic - Lagging (Yellow)"""
        mock_trade_dates_2 = ["20230101", "20230102", "20230103", "20230104"]
        mock_cal_df_2 = pd.DataFrame(
            {"cal_date": mock_trade_dates_2, "is_open": [1] * 4},
        )
        self.mock_cache.get_trade_cal = AsyncMock(return_value=mock_cal_df_2)
        # Only have 3 days local, missing 1
        self.mock_cache.get_cached_trade_dates = AsyncMock(
            return_value={"20230101", "20230102", "20230103"},
        )

        with (
            patch.object(
                self.processor,
                "get_latest_trade_date",
                new_callable=AsyncMock,
                return_value="20230104",
            ),
            patch.object(
                self.processor,
                "get_trade_dates",
                new_callable=AsyncMock,
                return_value=mock_trade_dates_2,
            ),
        ):
            # Reset health cache to force fresh eval
            self.processor._health_cache = {"time": 0, "data": None}
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "yellow")

    def test_check_data_health_yellow(self):
        asyncio.run(self.async_test_check_data_health_yellow())

    async def async_test_check_data_health_red(self):
        """Test health check logic - Missing Critical Tables (Red)"""
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "global_trade_days": 0,
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

        with (
            patch.object(
                self.processor,
                "get_latest_trade_date",
                new_callable=AsyncMock,
                return_value="20230119",
            ),
            patch.object(
                self.processor,
                "get_trade_dates",
                new_callable=AsyncMock,
                return_value=dates_3,
            ),
        ):
            self.processor._health_cache = {"time": 0, "data": None}
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "red")

    def test_check_data_health_red(self):
        asyncio.run(self.async_test_check_data_health_red())

    async def async_test_check_data_health_depth_insufficient(self):
        """Test depth check triggers yellow when actual trade days < 95% of required"""
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "global_trade_days": 500,
                "tables": {
                    "daily_quotes": {
                        "ratio": 1.0,
                        "type": "stock",
                        "depth_ratio": 0.67,
                    },
                    "daily_indicators": {
                        "ratio": 1.0,
                        "type": "stock",
                        "depth_ratio": 0.67,
                    },
                    "financial_reports": {"ratio": 0.95, "type": "stock"},
                    "moneyflow_daily": {"ratio": 1.0, "type": "stock"},
                    "stock_basic": {"ratio": 1.0, "type": "global"},
                },
            },
        )
        mock_trade_dates = ["20230101", "20230102", "20230103"]
        self.mock_cache.get_cached_trade_dates = AsyncMock(
            return_value={"20230101", "20230102", "20230103"},
        )

        with (
            patch.object(
                self.processor,
                "get_latest_trade_date",
                new_callable=AsyncMock,
                return_value="20230103",
            ),
            patch.object(
                self.processor,
                "get_trade_dates",
                new_callable=AsyncMock,
                return_value=mock_trade_dates,
            ),
        ):
            self.processor._health_cache = {"time": 0, "data": None}
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "yellow")
            self.assertEqual(res["tier"], 2)
            self.assertEqual(self.processor._quality_tier, 2)
            self.assertTrue(any("深度不足" in r or "depth" in r.lower() for r in res.get("reasons", [])))
            self.assertEqual(res["details"]["missing_depth"], 2)

    def test_check_data_health_depth_insufficient(self):
        asyncio.run(self.async_test_check_data_health_depth_insufficient())

    async def async_test_check_data_health_depth_sufficient(self):
        """Test depth check passes when actual trade days >= 95% of required"""
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "global_trade_days": 730,
                "tables": {
                    "daily_quotes": {
                        "ratio": 1.0,
                        "type": "stock",
                        "depth_ratio": 0.97,
                    },
                    "daily_indicators": {
                        "ratio": 1.0,
                        "type": "stock",
                        "depth_ratio": 0.97,
                    },
                    "financial_reports": {"ratio": 0.95, "type": "stock"},
                    "moneyflow_daily": {"ratio": 1.0, "type": "stock"},
                    "stock_basic": {"ratio": 1.0, "type": "global"},
                },
            },
        )
        mock_trade_dates = ["20230101", "20230102", "20230103"]
        self.mock_cache.get_cached_trade_dates = AsyncMock(
            return_value={"20230101", "20230102", "20230103"},
        )

        with (
            patch.object(
                self.processor,
                "get_latest_trade_date",
                new_callable=AsyncMock,
                return_value="20230103",
            ),
            patch.object(
                self.processor,
                "get_trade_dates",
                new_callable=AsyncMock,
                return_value=mock_trade_dates,
            ),
        ):
            self.processor._health_cache = {"time": 0, "data": None}
            res = await self.processor.check_data_health()
            self.assertEqual(res["status"], "green")
            self.assertEqual(res["details"]["missing_depth"], 0)

    def test_check_data_health_depth_sufficient(self):
        asyncio.run(self.async_test_check_data_health_depth_sufficient())

    # ==========================================================
    # Section 4: DataProcessor Core Methods
    # ==========================================================

    async def async_test_init_data(self):
        await self.processor.init_data()
        self.processor.cache.init_db.assert_called_once()  # type: ignore[untyped]

    def test_init_data(self):
        asyncio.run(self.async_test_init_data())

    # --- Sync Daily Market Snapshot Tests ---

    async def async_test_sync_daily_market_cache_hit(self):
        """Test that data is NOT fetched if cache exists"""
        trade_date = datetime.date(2023, 10, 25)

        # Mock Cache existence
        self.processor.cache.check_data_exists = AsyncMock(return_value=True)
        self.processor.cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame({"close": [10]}),
        )

        await self.processor.sync_daily_market_snapshot(trade_date)

        self.processor.cache.check_data_exists.assert_called_with(trade_date)  # type: ignore[untyped]
        self.processor.api.get_daily_quotes.assert_not_called()  # type: ignore[untyped]

    async def async_test_sync_daily_market_cache_miss(self):
        """Test cache miss fetches from API and saves"""
        target_date = datetime.date(2023, 10, 25)
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20200101")
        self.processor.cache.check_data_exists = AsyncMock(return_value=False)

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
        self.assertIn("ts_code", df.columns)
        self.assertIn("pe", df.columns)
        self.assertEqual(df.iloc[0]["ts_code"], "000001.SZ")
        self.mock_cache.get_screening_data.assert_called_with(target_date)

        # Verify save methods were called by Strategy
        self.assertEqual(self.mock_cache.save_daily_quotes.call_count, 1)
        self.assertEqual(self.mock_cache.save_daily_indicators.call_count, 1)

    def test_sync_daily_market_snapshot_hit(self):
        asyncio.run(self.async_test_sync_daily_market_cache_hit())

    def test_sync_daily_market_snapshot_miss(self):
        asyncio.run(self.async_test_sync_daily_market_cache_miss())

    # --- sync_stock_basic ---

    async def async_test_sync_stock_basic(self):
        """Test sync_stock_basic calls api.get_stock_basic_all directly"""
        mock_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "name": ["PingAn"],
                "list_status": ["L"],
            }
        )
        self.mock_api.get_stock_basic_all = AsyncMock(return_value=mock_df)
        self.mock_cache.save_stock_basic = AsyncMock(return_value=1)

        # Reset the sync lock flag
        self.processor._is_syncing_basic = False

        count = await self.processor.sync_stock_basic()

        self.assertEqual(count, 1)
        self.mock_cache.save_stock_basic.assert_called_once_with(mock_df)

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

        cached_dates = {
            datetime.date(2023, 1, 5),
            datetime.date(2023, 1, 4),
        }
        self.mock_cache.get_cached_dates_for_table = AsyncMock(return_value=cached_dates)

        historical_strategy = self.processor.strategies["historical"]
        with patch.object(
            historical_strategy,
            "sync_daily_market_snapshot",
            new_callable=AsyncMock,
        ) as mock_sync:
            await self.processor.sync_historical_data(days=days)

            self.assertEqual(mock_sync.call_count, 3)
            call_args = [c.args[0] for c in mock_sync.call_args_list]
            self.assertIn(datetime.date(2023, 1, 3), call_args)

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

        mock_cashflow = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": ["20230331"],
                "n_cashflow_act": [800],
            },
        )

        self.mock_api.get_income.return_value = mock_income
        self.mock_api.get_balancesheet.return_value = mock_balance
        self.mock_api.get_fina_indicator.return_value = mock_indicator
        self.mock_api.get_cashflow.return_value = mock_cashflow

        from unittest.mock import AsyncMock, MagicMock
        from contextlib import asynccontextmanager

        mock_conn = AsyncMock()

        @asynccontextmanager
        async def mock_guarded_begin(conn=None):
            yield mock_conn

        self.mock_cache.financial_dao = MagicMock()
        self.mock_cache.financial_dao._guarded_begin = mock_guarded_begin

        self.mock_cache.save_financial_reports = AsyncMock(return_value=1)
        self.mock_cache.update_sync_status = AsyncMock()

        count = await self.processor.sync_financial_reports(periods=periods)

        self.assertEqual(count, 1)
        # After fix: income/balance/cashflow/indicator are merged before saving (1 call, not 4)
        self.assertTrue(self.mock_cache.save_financial_reports.call_count >= 1)

        saved_df = self.mock_cache.save_financial_reports.call_args_list[0][0][0]

        # Merged DataFrame should contain fields from all 3 sources
        self.assertIn("total_assets", saved_df.columns)
        self.assertEqual(saved_df.iloc[0]["total_assets"], 10000)
        self.assertIn("roe", saved_df.columns)
        self.assertEqual(saved_df.iloc[0]["roe"], 10.5)
        self.assertIn("n_cashflow_act", saved_df.columns)
        self.assertEqual(saved_df.iloc[0]["n_cashflow_act"], 800)

    def test_financial_reports(self):
        asyncio.run(self.async_test_sync_financial_reports())

    # --- prepare_screening_context ---

    async def async_test_prepare_screening_context(self):
        """Test prepare_screening_context"""
        self.processor._quality_tier = 3
        self.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2023, 1, 1))
        self.mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame({"pe": [10], "trade_date": ["20230101"]}),
        )
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20230101")
        self.mock_cache.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame({"roe": [0.15], "trade_date": ["20230101"]}),
        )

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
        self.assertIn("trade_date", context)
        self.assertEqual(context["trade_date"], "20230101")
        self.assertIn("fundamental_screening_data", context)
        self.assertIn("northbound_data", context)
        self.assertIn("moneyflow_data", context)
        self.assertIn("top_list", context)
        self.assertIn("block_trade", context)

    def test_prepare_screening_context(self):
        asyncio.run(self.async_test_prepare_screening_context())

    async def async_test_prepare_screening_context_resolves_trade_date_from_data(self):
        """当缓存 trade_date 缺失时，应从 screening_data 的唯一 trade_date 推导"""
        self.processor._quality_tier = 3
        self.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=None)
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value=None)
        self.mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20230103"],
                    "close": [10.0],
                }
            ),
        )
        self.mock_cache.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20230103"],
                    "roe": [0.15],
                }
            ),
        )
        self.mock_cache.get_northbound = AsyncMock(return_value=None)
        self.mock_cache.get_moneyflow = AsyncMock(return_value=None)
        self.mock_cache.get_top_list = AsyncMock(return_value=None)
        self.mock_cache.get_block_trade = AsyncMock(return_value=None)

        context = await self.processor.prepare_screening_context()

        self.assertEqual(context["trade_date"], "20230103")
        self.mock_cache.get_northbound.assert_called_once_with(trade_date="20230103")
        self.mock_cache.get_moneyflow.assert_called_once_with(trade_date="20230103")
        self.mock_cache.get_top_list.assert_called_once_with(trade_date="20230103")
        self.mock_cache.get_block_trade.assert_called_once_with(trade_date="20230103")

    def test_prepare_screening_context_resolves_trade_date_from_data(self):
        asyncio.run(self.async_test_prepare_screening_context_resolves_trade_date_from_data())

    async def async_test_prepare_screening_context_prefers_latest_closed_trade_date(
        self,
    ):
        """无显式 trade_date 时，应优先使用交易日服务的最近闭市日而不是库里最大日期。"""
        self.processor._quality_tier = 3
        self.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2023, 1, 5))
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20230106")
        self.mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20230105"],
                    "close": [10.0],
                }
            ),
        )
        self.mock_cache.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20230105"],
                    "roe": [0.15],
                }
            ),
        )
        self.mock_cache.get_northbound = AsyncMock(return_value=None)
        self.mock_cache.get_moneyflow = AsyncMock(return_value=None)
        self.mock_cache.get_top_list = AsyncMock(return_value=None)
        self.mock_cache.get_block_trade = AsyncMock(return_value=None)

        context = await self.processor.prepare_screening_context()

        self.assertEqual(context["trade_date"], "20230105")
        self.mock_cache.get_screening_data.assert_awaited_once_with("20230105")
        self.mock_cache.get_latest_trade_date.assert_not_awaited()

    def test_prepare_screening_context_prefers_latest_closed_trade_date(self):
        asyncio.run(self.async_test_prepare_screening_context_prefers_latest_closed_trade_date())

    async def async_test_prepare_screening_context_raises_on_trade_date_mismatch(self):
        """缓存 trade_date 与 screening_data.trade_date 不一致时，应立即失败"""
        self.processor._quality_tier = 3
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20230101")
        self.mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20230102"],
                    "close": [10.0],
                }
            ),
        )

        with self.assertRaises(RuntimeError):
            await self.processor.prepare_screening_context()

    def test_prepare_screening_context_raises_on_trade_date_mismatch(self):
        asyncio.run(self.async_test_prepare_screening_context_raises_on_trade_date_mismatch())

    async def async_test_get_strategy_data_passes_trade_date_through(self):
        """get_strategy_data 应透传指定 trade_date 到 prepare_screening_context。"""
        self.processor.prepare_screening_context = AsyncMock(return_value={"trade_date": "20230105"})

        context = await self.processor.get_strategy_data(trade_date="20230105")

        self.assertEqual(context["trade_date"], "20230105")
        self.processor.prepare_screening_context.assert_awaited_once_with(trade_date="20230105")

    def test_get_strategy_data_passes_trade_date_through(self):
        asyncio.run(self.async_test_get_strategy_data_passes_trade_date_through())

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
        """Verify get_market_overview works with TradeCalendarService"""

        self.mock_cache.get_trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20230101", "20230102"], "is_open": [1, 1]},
        )

        # Mock API methods to return valid DataFrames
        mock_result_df = pd.DataFrame(
            {"close": [3000], "pct_chg": [1.0], "north_money": [500]},
        )
        self.mock_api.get_index_daily = AsyncMock(return_value=mock_result_df)
        self.mock_api.get_moneyflow_hsgt = AsyncMock(return_value=mock_result_df)

        # get_market_overview should work without errors
        await self.processor.get_market_overview()

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

        self.mock_cache.get_cached_dates_for_table = AsyncMock(return_value=set())
        self.mock_cache.get_bulk_sync_quality_scores = AsyncMock(return_value={})

        call_count = 0

        async def side_effect(date, force=False, sync_result=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Network Error")
            return pd.DataFrame({"a": [1]})

        historical_strategy = self.processor.strategies["historical"]
        with patch.object(
            historical_strategy,
            "sync_daily_market_snapshot",
            side_effect=side_effect,
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
        from zoneinfo import ZoneInfo

        cst = ZoneInfo("Asia/Shanghai")
        fixed_now = dt.datetime(2025, 12, 1, tzinfo=cst)

        self.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=dt.date(2025, 12, 1))

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

        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20251201")
        self.mock_cache.get_field_completeness = AsyncMock(
            return_value={
                "roe": 0.9,
                "or_yoy": 0.85,
                "netprofit_yoy": 0.8,
                "dv_ttm": 0.9,
                "pe_ttm": 0.95,
                "pb": 0.95,
                "debt_to_assets": 0.85,
            },
        )
        self.mock_cache.quote_dao.get_field_completeness = self.mock_cache.get_field_completeness
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["financial_reports"],
                    "last_data_date": ["20251120"],
                }
            ),
        )
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "global_trade_days": 750,
                "tables": {
                    "daily_quotes": {"ratio": 1.0, "type": "stock"},
                    "daily_indicators": {"ratio": 1.0, "type": "stock"},
                    "financial_reports": {"ratio": 0.95, "type": "stock"},
                    "moneyflow_daily": {"ratio": 1.0, "type": "stock"},
                    "stock_basic": {"ratio": 1.0, "type": "global"},
                },
            }
        )

        progress_calls = []

        def progress_cb(current, total, msg):
            progress_calls.append((current, total, msg))

        with patch("data.mixins.health_mixin.get_now", return_value=fixed_now):
            with patch("random.sample", side_effect=lambda pop, k: pop[:k]):
                result = await self.processor.run_quality_scan(
                    sample_size=5,
                    progress_callback=progress_cb,
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

    async def async_test_run_quality_scan_uses_latest_closed_trade_date_anchor(self):
        """盘中深度扫描应以最近闭市日计算行情和财务新鲜度。"""
        fixed_now = datetime.datetime(2025, 12, 2, 10, 0, 0)
        anchor_date = datetime.date(2025, 12, 1)
        stocks = [f"{i:06d}.SZ" for i in range(1, 4)]
        cal_dates = pd.bdate_range(anchor_date - datetime.timedelta(days=14), anchor_date).strftime("%Y%m%d").tolist()

        self.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=anchor_date)
        self.mock_cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": stocks, "list_status": ["L"] * len(stocks)}),
        )
        self.mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame({"cal_date": cal_dates, "is_open": [1] * len(cal_dates)}),
        )

        rows = []
        for code in stocks:
            for d in cal_dates:
                rows.append({"ts_code": code, "trade_date": d, "close": 10.0, "vol": 1000})
        self.mock_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame(rows))

        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20251201")
        self.mock_cache.get_field_completeness = AsyncMock(
            return_value={
                "roe": 0.95,
                "or_yoy": 0.95,
                "netprofit_yoy": 0.95,
                "dv_ttm": 0.95,
                "pe_ttm": 0.95,
                "pb": 0.95,
                "debt_to_assets": 0.95,
            },
        )
        self.mock_cache.quote_dao.get_field_completeness = self.mock_cache.get_field_completeness
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["financial_reports"],
                    "last_data_date": ["20250824"],
                }
            ),
        )
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "global_trade_days": 750,
                "tables": {
                    "daily_quotes": {"ratio": 1.0, "type": "stock"},
                    "daily_indicators": {"ratio": 1.0, "type": "stock"},
                    "financial_reports": {"ratio": 0.95, "type": "stock"},
                    "moneyflow_daily": {"ratio": 1.0, "type": "stock"},
                    "stock_basic": {"ratio": 1.0, "type": "global"},
                },
            }
        )

        with patch("data.mixins.health_mixin.get_now", return_value=fixed_now):
            with patch("random.sample", side_effect=lambda pop, k: pop[:k]):
                result = await self.processor.run_quality_scan(sample_size=3)

        self.assertEqual(result["avg_lag"], 0)
        self.assertTrue(result["fin_recency_ok"])
        self.assertEqual(result["tier"], 3)
        self.mock_cache.get_daily_quotes.assert_awaited_once_with(
            ts_code_list=stocks,
            start_date=datetime.date(2024, 12, 1),
            end_date=datetime.date(2025, 12, 1),
        )

    def test_run_quality_scan_uses_latest_closed_trade_date_anchor(self):
        asyncio.run(self.async_test_run_quality_scan_uses_latest_closed_trade_date_anchor())

    async def async_test_run_quality_scan_empty_stocks(self):
        """Test run_quality_scan when no active stocks exist"""
        self.mock_cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())

        result = await self.processor.run_quality_scan()
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["tier"], 0)

    def test_run_quality_scan_empty_stocks(self):
        asyncio.run(self.async_test_run_quality_scan_empty_stocks())

    async def async_test_run_quality_scan_low_fundamental(self):
        """Test run_quality_scan with low fundamental completeness caps tier to SILVER"""
        stocks = [f"{i:06d}.SZ" for i in range(1, 6)]
        fixed_now = datetime.datetime(2025, 12, 1, 16, 0, 0)
        cal_dates = [(fixed_now - datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(30)]

        self.mock_cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": stocks, "list_status": ["L"] * len(stocks)}),
        )
        self.mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame({"cal_date": cal_dates, "is_open": [1] * len(cal_dates)}),
        )
        rows = []
        for code in stocks:
            for d in cal_dates:
                rows.append({"ts_code": code, "trade_date": d, "close": 10.0, "vol": 1000})
        batch_df = pd.DataFrame(rows)
        self.mock_cache.get_daily_quotes = AsyncMock(return_value=batch_df)

        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20251201")
        self.mock_cache.get_field_completeness = AsyncMock(
            return_value={
                "roe": 0.1,
                "or_yoy": 0.05,
                "netprofit_yoy": 0.05,
                "dv_ttm": 0.1,
                "pe_ttm": 0.1,
                "pb": 0.1,
                "debt_to_assets": 0.05,
            },
        )
        self.mock_cache.quote_dao.get_field_completeness = self.mock_cache.get_field_completeness
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["financial_reports"],
                    "last_data_date": ["20251120"],
                }
            ),
        )

        with patch("data.mixins.health_mixin.get_now", return_value=fixed_now):
            with patch("random.sample", side_effect=lambda pop, k: pop[:k]):
                result = await self.processor.run_quality_scan(sample_size=5)

        self.assertEqual(result["tier"], 2)

    def test_run_quality_scan_low_fundamental(self):
        asyncio.run(self.async_test_run_quality_scan_low_fundamental())

    async def async_test_run_quality_scan_missing_critical_financials(self):
        """Test run_quality_scan uses comprehensive coverage and downgrades missing critical tables to CRITICAL"""
        stocks = [f"{i:06d}.SZ" for i in range(1, 4)]
        fixed_now = datetime.datetime(2025, 12, 1, 16, 0, 0)
        cal_dates = [(fixed_now - datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(10)]

        self.mock_cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame({"ts_code": stocks, "list_status": ["L"] * len(stocks)}),
        )
        self.mock_cache.get_trade_cal = AsyncMock(
            return_value=pd.DataFrame({"cal_date": cal_dates, "is_open": [1] * len(cal_dates)}),
        )
        rows = []
        for code in stocks:
            for d in cal_dates:
                rows.append({"ts_code": code, "trade_date": d, "close": 10.0, "vol": 1000})
        self.mock_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame(rows))
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20251201")
        self.mock_cache.get_field_completeness = AsyncMock(
            return_value={"roe": 0.9, "or_yoy": 0.9, "netprofit_yoy": 0.9},
        )
        self.mock_cache.quote_dao.get_field_completeness = self.mock_cache.get_field_completeness
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["financial_reports"],
                    "last_data_date": ["20251120"],
                }
            )
        )
        self.mock_cache.check_comprehensive_health = AsyncMock(
            return_value={
                "global_trade_days": 750,
                "tables": {
                    "daily_quotes": {"ratio": 1.0, "type": "stock"},
                    "daily_indicators": {"ratio": 1.0, "type": "stock"},
                    "financial_reports": {"ratio": 0.05, "type": "stock"},
                    "moneyflow_daily": {"ratio": 1.0, "type": "stock"},
                    "stock_basic": {"ratio": 1.0, "type": "global"},
                },
            }
        )

        with patch("data.mixins.health_mixin.get_now", return_value=fixed_now):
            with patch("random.sample", side_effect=lambda pop, k: pop[:k]):
                result = await self.processor.run_quality_scan(sample_size=3)

        self.assertEqual(result["tier"], 0)
        self.assertEqual(self.processor._quality_tier, 0)

    def test_run_quality_scan_missing_critical_financials(self):
        asyncio.run(self.async_test_run_quality_scan_missing_critical_financials())

    async def async_test_run_quality_scan_cancellation(self):
        """Test run_quality_scan respects cancellation"""
        stocks = [f"{i:06d}.SZ" for i in range(1, 20)]
        self.processor.trade_calendar.get_latest_trade_date = AsyncMock(return_value=datetime.date(2023, 1, 1))
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

        with (
            patch.object(
                self.processor,
                "clear_cancel",
                side_effect=cancel_after_clear,
            ),
            patch("random.sample", side_effect=lambda pop, k: pop[:k]),
        ):
            result = await self.processor.run_quality_scan(sample_size=10)

        # Should return early with minimal data processed
        self.assertEqual(result["tier"], 1)

    def test_run_quality_scan_cancellation(self):
        asyncio.run(self.async_test_run_quality_scan_cancellation())

    # ==========================================================
    # Section 6: CalendarMixin tests removed - now covered by TradeCalendarService tests
    # ==========================================================

    # ==========================================================
    # Section 7: _assign_basic_tier Silver path
    # ==========================================================

    async def async_test_assign_basic_tier_silver(self):
        """Test _assign_basic_tier assigns SILVER (2) when all critical tables fresh but fin_fresh_ratio=0.5"""
        today = get_now().strftime("%Y%m%d")
        self.mock_cache.get_sync_status = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": [
                        "daily_quotes",
                        "daily_indicators",
                        "moneyflow_daily",
                        "financial_reports",
                    ],
                    "last_data_date": [today, today, today, today],
                    "record_count": [1000, 800, 600, 500],
                    "last_result_status": ["ok", "ok", "ok", "ok"],
                },
            ),
        )

        await self.processor._assign_basic_tier()
        self.assertEqual(self.processor._quality_tier, 2)

    def test_assign_basic_tier_silver(self):
        asyncio.run(self.async_test_assign_basic_tier_silver())

    # ==========================================================
    # Section 8: TradeCalendar Service Facade Tests
    # ==========================================================

    # ==========================================================
    # Section 9: sync_daily_market_snapshot auto-resolves trade_date
    # ==========================================================

    async def async_test_sync_daily_auto_resolves_date(self):
        """Test sync_daily_market_snapshot calls get_latest_trade_date when date is None"""
        with patch.object(
            self.processor,
            "get_latest_trade_date",
            new_callable=AsyncMock,
            return_value=datetime.date(2023, 1, 3),
        ) as mock_latest:
            self.mock_cache.check_data_exists = AsyncMock(return_value=True)
            self.mock_cache.get_screening_data = AsyncMock(
                return_value=pd.DataFrame({"close": [10]}),
            )

            await self.processor.sync_daily_market_snapshot(trade_date=None)
            mock_latest.assert_called_once()

    def test_sync_daily_auto_resolves_date(self):
        asyncio.run(self.async_test_sync_daily_auto_resolves_date())


if __name__ == "__main__":
    unittest.main()
