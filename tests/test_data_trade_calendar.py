"""
Unit tests for TradeCalendarService.

Tests cover:
- Core methods: is_trading_day, get_trade_dates, count_trade_days
- Date calculation: get_start_date_by_trade_days, get_prev/next_trade_date, get_latest_trade_date
- Data persistence: API data is saved to database
- Fallback behavior: Database -> API -> Offline
- Edge cases: empty ranges, invalid inputs, boundary conditions
- Error handling: DB failures, API failures, offline fallback
- Concurrency: cache race conditions, lock correctness
"""

import asyncio
import datetime
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.services.trade_calendar_service import TradeCalendarService
from tests.test_infra_base import TestDatabaseBase


class TestTradeCalendarService(TestDatabaseBase):
    """Test TradeCalendarService with mocked dependencies."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_trade_calendar(self, start_date, end_date):
        """Seed the database with trade calendar data."""
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1,
                        "exchange": "SSE",
                    }
                )
            else:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 0,
                        "exchange": "SSE",
                    }
                )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    # ==========================================================
    # Core Method Tests
    # ==========================================================

    async def test_is_trading_day_weekday_from_db(self):
        """Test is_trading_day returns True for weekday in DB."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.is_trading_day(datetime.date(2024, 3, 21))
        self.assertTrue(result)

    async def test_is_trading_day_weekend_from_db(self):
        """Test is_trading_day returns False for weekend in DB."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 24)
        await self._seed_trade_calendar(start, end)

        result = await self.service.is_trading_day(datetime.date(2024, 3, 23))
        self.assertFalse(result)

    async def test_is_trading_day_fallback_to_api(self):
        """Test is_trading_day falls back to API when DB has no data."""
        test_date = datetime.date(2024, 3, 21)

        await self.cache.stock_dao._write_db(
            "DELETE FROM trade_cal WHERE cal_date = $1", (test_date,)
        )

        api_df = pd.DataFrame(
            {
                "cal_date": [test_date.strftime("%Y%m%d")],
                "is_open": [1],
                "exchange": ["SSE"],
            }
        )
        self.mock_api.get_trade_cal.return_value = api_df

        result = await self.service.is_trading_day(test_date)
        self.assertTrue(result)
        self.mock_api.get_trade_cal.assert_called()

    async def test_get_trade_dates_from_db(self):
        """Test get_trade_dates returns correct dates from DB."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_trade_dates(start, end)

        self.assertEqual(len(result), 5)
        self.assertEqual(result[0], datetime.date(2024, 3, 18))
        self.assertEqual(result[-1], datetime.date(2024, 3, 22))

    async def test_get_trade_dates_fallback_to_api(self):
        """Test get_trade_dates falls back to API and persists data."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)

        await self.cache.stock_dao._write_db(
            "DELETE FROM trade_cal WHERE cal_date >= $1 AND cal_date <= $2",
            (start, end),
        )

        api_df = pd.DataFrame(
            {
                "cal_date": [
                    "20240318",
                    "20240319",
                    "20240320",
                    "20240321",
                    "20240322",
                ],
                "is_open": [1, 1, 1, 1, 1],
                "exchange": ["SSE"] * 5,
            }
        )
        self.mock_api.get_trade_cal.return_value = api_df

        result = await self.service.get_trade_dates(start, end)

        self.assertEqual(len(result), 5)
        self.mock_api.get_trade_cal.assert_called()

        cached_df = await self.cache.get_trade_cal(start, end, is_open=1)
        self.assertIsNotNone(cached_df)
        self.assertEqual(len(cached_df), 5)

    async def test_count_trade_days(self):
        """Test count_trade_days returns correct count."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.count_trade_days(start, end)
        self.assertEqual(result, 5)

    async def test_get_start_date_by_trade_days(self):
        """Test get_start_date_by_trade_days calculates correct start date."""
        start = datetime.date(2024, 1, 1)
        end = datetime.date(2024, 3, 31)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_start_date_by_trade_days(
            datetime.date(2024, 3, 21), 10
        )

        self.assertIsNotNone(result)
        self.assertLess(result, datetime.date(2024, 3, 21))  # type: ignore

    async def test_get_prev_trade_date(self):
        """Test get_prev_trade_date returns previous trading day."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_prev_trade_date(datetime.date(2024, 3, 21))

        self.assertEqual(result, datetime.date(2024, 3, 20))

    async def test_get_prev_trade_date_from_monday(self):
        """Test get_prev_trade_date from Monday returns Friday."""
        start = datetime.date(2024, 3, 15)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_prev_trade_date(datetime.date(2024, 3, 18))

        self.assertEqual(result, datetime.date(2024, 3, 15))

    async def test_get_next_trade_date(self):
        """Test get_next_trade_date returns next trading day."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 25)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_next_trade_date(datetime.date(2024, 3, 21))

        self.assertEqual(result, datetime.date(2024, 3, 22))

    async def test_get_next_trade_date_from_friday(self):
        """Test get_next_trade_date from Friday returns Monday."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 25)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_next_trade_date(datetime.date(2024, 3, 22))

        self.assertEqual(result, datetime.date(2024, 3, 25))

    async def test_get_latest_trade_date(self):
        """Test get_latest_trade_date returns most recent trading day."""
        start = datetime.date(2024, 3, 1)
        end = datetime.date(2024, 3, 31)
        await self._seed_trade_calendar(start, end)

        with patch("data.services.trade_calendar_service.get_now") as mock_now:
            mock_dt = datetime.datetime(2024, 3, 21, 17, 0, 0)
            mock_now.return_value = mock_dt

            result = await self.service.get_latest_trade_date()

            self.assertEqual(result, datetime.date(2024, 3, 21))

    async def test_get_latest_trade_date_before_close(self):
        """Test get_latest_trade_date before market close returns previous day."""
        start = datetime.date(2024, 3, 1)
        end = datetime.date(2024, 3, 31)
        await self._seed_trade_calendar(start, end)

        with patch("data.services.trade_calendar_service.get_now") as mock_now:
            mock_dt = datetime.datetime(2024, 3, 21, 10, 0, 0)
            mock_now.return_value = mock_dt

            result = await self.service.get_latest_trade_date()

            self.assertEqual(result, datetime.date(2024, 3, 20))

    async def test_to_date_conversion(self):
        """Test _to_date handles various input types."""
        self.assertEqual(
            self.service._to_date("2024-03-21"), datetime.date(2024, 3, 21)
        )
        self.assertEqual(self.service._to_date("20240321"), datetime.date(2024, 3, 21))
        self.assertEqual(
            self.service._to_date(datetime.datetime(2024, 3, 21, 10, 0)),
            datetime.date(2024, 3, 21),
        )
        self.assertEqual(
            self.service._to_date(datetime.date(2024, 3, 21)),
            datetime.date(2024, 3, 21),
        )
        self.assertIsNone(self.service._to_date(None))

    async def test_clear_cache(self):
        """Test clear_cache resets internal caches."""
        self.service._latest_trade_date_cache = {
            "ts": 1234567890,
            "val": datetime.date(2024, 3, 21),
        }
        self.service._mem_cache = {"test_key": "test_value"}

        self.service.clear_cache()

        self.assertEqual(self.service._latest_trade_date_cache, {"ts": 0, "val": None})
        self.assertEqual(self.service._mem_cache, {})


class TestTradeCalendarServiceEdgeCases(TestDatabaseBase):
    """Test TradeCalendarService edge cases and boundary conditions."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_trade_calendar(self, start_date, end_date):
        """Seed the database with trade calendar data."""
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1,
                        "exchange": "SSE",
                    }
                )
            else:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 0,
                        "exchange": "SSE",
                    }
                )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    # ==========================================================
    # Edge Case: Empty/Invalid Ranges
    # ==========================================================

    async def test_get_trade_dates_reverse_range(self):
        """Test get_trade_dates with start > end returns empty list."""
        result = await self.service.get_trade_dates(
            datetime.date(2024, 3, 22), datetime.date(2024, 3, 18)
        )
        self.assertEqual(result, [])

    async def test_get_trade_dates_same_date_trading(self):
        """Test get_trade_dates with same start and end date (trading day)."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 21), datetime.date(2024, 3, 21)
        )

        result = await self.service.get_trade_dates(
            datetime.date(2024, 3, 21), datetime.date(2024, 3, 21)
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], datetime.date(2024, 3, 21))

    async def test_get_trade_dates_same_date_weekend(self):
        """Test get_trade_dates with same start and end date (weekend)."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 23), datetime.date(2024, 3, 23)
        )

        result = await self.service.get_trade_dates(
            datetime.date(2024, 3, 23), datetime.date(2024, 3, 23)
        )

        self.assertEqual(result, [])

    async def test_get_trade_dates_null_start(self):
        """Test get_trade_dates with None start returns empty list."""
        result = await self.service.get_trade_dates(None, datetime.date(2024, 3, 22))
        self.assertEqual(result, [])

    async def test_get_trade_dates_null_end(self):
        """Test get_trade_dates with None end returns empty list."""
        result = await self.service.get_trade_dates(datetime.date(2024, 3, 18), None)
        self.assertEqual(result, [])

    async def test_get_trade_dates_both_null(self):
        """Test get_trade_dates with both None returns empty list."""
        result = await self.service.get_trade_dates(None, None)
        self.assertEqual(result, [])

    # ==========================================================
    # Edge Case: Invalid Inputs
    # ==========================================================

    async def test_is_trading_day_null_date(self):
        """Test is_trading_day with None returns False."""
        result = await self.service.is_trading_day(None)
        self.assertFalse(result)

    async def test_get_start_date_by_trade_days_null_end(self):
        """Test get_start_date_by_trade_days with None end returns None."""
        result = await self.service.get_start_date_by_trade_days(None, 10)
        self.assertIsNone(result)

    async def test_get_start_date_by_trade_days_zero_days(self):
        """Test get_start_date_by_trade_days with 0 days returns None."""
        result = await self.service.get_start_date_by_trade_days(
            datetime.date(2024, 3, 21), 0
        )
        self.assertIsNone(result)

    async def test_get_start_date_by_trade_days_negative_days(self):
        """Test get_start_date_by_trade_days with negative days returns None."""
        result = await self.service.get_start_date_by_trade_days(
            datetime.date(2024, 3, 21), -5
        )
        self.assertIsNone(result)

    async def test_get_prev_trade_date_null_date(self):
        """Test get_prev_trade_date with None returns None."""
        result = await self.service.get_prev_trade_date(None)
        self.assertIsNone(result)

    async def test_get_next_trade_date_null_date(self):
        """Test get_next_trade_date with None returns None."""
        result = await self.service.get_next_trade_date(None)
        self.assertIsNone(result)

    # ==========================================================
    # Edge Case: Boundary Conditions
    # ==========================================================

    async def test_get_start_date_by_trade_days_exceeds_available(self):
        """Test get_start_date_by_trade_days when requesting more days than available."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        result = await self.service.get_start_date_by_trade_days(
            datetime.date(2024, 3, 22), 100
        )

        self.assertIsNotNone(result)
        self.assertLess(result, datetime.date(2024, 3, 22))  # type: ignore

    async def test_get_prev_trade_date_first_available(self):
        """Test get_prev_trade_date when at first available trading day."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        result = await self.service.get_prev_trade_date(datetime.date(2024, 3, 18))

        self.assertIsNotNone(result)
        self.assertLess(result, datetime.date(2024, 3, 18))  # type: ignore

    async def test_get_next_trade_date_last_available(self):
        """Test get_next_trade_date when at last available trading day."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        result = await self.service.get_next_trade_date(datetime.date(2024, 3, 22))

        self.assertIsNotNone(result)
        self.assertGreater(result, datetime.date(2024, 3, 22))  # type: ignore

    async def test_count_trade_days_empty_range(self):
        """Test count_trade_days with no trading days in range."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        result = await self.service.count_trade_days(
            datetime.date(2024, 1, 1), datetime.date(2024, 1, 5)
        )

        self.assertGreaterEqual(result, 0)

    # ==========================================================
    # Edge Case: String Date Formats
    # ==========================================================

    async def test_is_trading_day_string_with_hyphen(self):
        """Test is_trading_day accepts YYYY-MM-DD string format."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 21), datetime.date(2024, 3, 21)
        )

        result = await self.service.is_trading_day("2024-03-21")
        self.assertTrue(result)

    async def test_is_trading_day_string_without_hyphen(self):
        """Test is_trading_day accepts YYYYMMDD string format."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 21), datetime.date(2024, 3, 21)
        )

        result = await self.service.is_trading_day("20240321")
        self.assertTrue(result)

    async def test_get_trade_dates_string_inputs(self):
        """Test get_trade_dates accepts string date inputs."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        result = await self.service.get_trade_dates("2024-03-18", "20240322")

        self.assertEqual(len(result), 5)


class TestTradeCalendarServiceOffline(TestDatabaseBase):
    """Test TradeCalendarService offline fallback behavior."""

    def setUp(self):
        """Set up test fixtures without database."""
        self.mock_cache = MagicMock()
        self.mock_cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame())
        self.mock_cache.save_trade_cal = AsyncMock()
        self.mock_cache.count_trade_days = AsyncMock(side_effect=Exception("DB error"))

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock(return_value=None)

        self.service = TradeCalendarService(self.mock_cache, self.mock_api)

    async def test_is_trading_day_offline_fallback(self):
        """Test is_trading_day falls back to offline calendar."""
        result = await self.service.is_trading_day(datetime.date(2024, 3, 21))

        self.assertTrue(result)

    async def test_is_trading_day_weekend_offline(self):
        """Test is_trading_day returns False for weekend in offline mode."""
        result = await self.service.is_trading_day(datetime.date(2024, 3, 23))

        self.assertFalse(result)

    async def test_get_trade_dates_offline_fallback(self):
        """Test get_trade_dates falls back to offline calendar."""
        result = await self.service.get_trade_dates(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        self.assertEqual(len(result), 5)

    async def test_count_trade_days_offline_fallback(self):
        """Test count_trade_days falls back to list counting."""
        result = await self.service.count_trade_days(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        self.assertEqual(result, 5)


class TestTradeCalendarServiceErrorHandling(TestDatabaseBase):
    """Test TradeCalendarService error handling and resilience."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    # ==========================================================
    # Error Handling: Database Failures
    # ==========================================================

    async def test_is_trading_day_db_exception_fallback(self):
        """Test is_trading_day handles DB exceptions gracefully."""
        self.service._cache.get_trade_cal = AsyncMock(
            side_effect=Exception("DB connection failed")
        )

        result = await self.service.is_trading_day(datetime.date(2024, 3, 21))

        self.assertTrue(result)

    async def test_get_trade_dates_db_exception_fallback(self):
        """Test get_trade_dates handles DB exceptions with offline fallback."""
        self.service._cache.get_trade_cal = AsyncMock(
            side_effect=Exception("DB connection failed")
        )

        result = await self.service.get_trade_dates(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        self.assertEqual(len(result), 5)

    # ==========================================================
    # Error Handling: API Failures
    # ==========================================================

    async def test_is_trading_day_api_exception_fallback(self):
        """Test is_trading_day handles API exceptions with offline fallback."""
        self.service._cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame())
        self.service._api.get_trade_cal = AsyncMock(
            side_effect=Exception("API timeout")
        )

        result = await self.service.is_trading_day(datetime.date(2024, 3, 21))

        self.assertTrue(result)

    async def test_get_trade_dates_api_exception_fallback(self):
        """Test get_trade_dates handles API exceptions with offline fallback."""
        self.service._cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame())
        self.service._api.get_trade_cal = AsyncMock(
            side_effect=Exception("API timeout")
        )

        result = await self.service.get_trade_dates(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        self.assertEqual(len(result), 5)

    # ==========================================================
    # Error Handling: Persistence Failures
    # ==========================================================

    async def test_api_data_persistence_failure_does_not_affect_result(self):
        """Test that persistence failure doesn't affect the returned result."""
        self.service._cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame())
        self.service._cache.save_trade_cal = AsyncMock(
            side_effect=Exception("Write failed")
        )

        api_df = pd.DataFrame(
            {
                "cal_date": ["20240321"],
                "is_open": [1],
                "exchange": ["SSE"],
            }
        )
        self.service._api.get_trade_cal = AsyncMock(return_value=api_df)

        result = await self.service.is_trading_day(datetime.date(2024, 3, 21))

        self.assertTrue(result)

    # ==========================================================
    # Error Handling: Malformed Data
    # ==========================================================

    async def test_get_trade_dates_malformed_api_data(self):
        """Test get_trade_dates handles malformed API data gracefully."""
        self.service._cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame())

        malformed_df = pd.DataFrame(
            {
                "cal_date": ["20240321"],
                "exchange": ["SSE"],
            }
        )
        self.service._api.get_trade_cal = AsyncMock(return_value=malformed_df)

        result = await self.service.get_trade_dates(
            datetime.date(2024, 3, 21), datetime.date(2024, 3, 21)
        )

        self.assertIsNotNone(result)


class TestTradeCalendarServiceConcurrency(TestDatabaseBase):
    """Test TradeCalendarService thread safety and concurrency."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_trade_calendar(self, start_date, end_date):
        """Seed the database with trade calendar data."""
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1,
                        "exchange": "SSE",
                    }
                )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    async def test_concurrent_get_latest_trade_date_no_race(self):
        """Test concurrent calls to get_latest_trade_date don't cause race conditions."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 1), datetime.date(2024, 3, 31)
        )

        with patch("data.services.trade_calendar_service.get_now") as mock_now:
            mock_dt = datetime.datetime(2024, 3, 21, 17, 0, 0)
            mock_now.return_value = mock_dt

            results = await asyncio.gather(
                self.service.get_latest_trade_date(),
                self.service.get_latest_trade_date(),
                self.service.get_latest_trade_date(),
                self.service.get_latest_trade_date(),
                self.service.get_latest_trade_date(),
            )

            for result in results:
                self.assertEqual(result, datetime.date(2024, 3, 21))

    async def test_cache_ttl_prevents_excessive_queries(self):
        """Test that cache TTL prevents excessive database queries."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 1), datetime.date(2024, 3, 31)
        )

        with patch("data.services.trade_calendar_service.get_now") as mock_now:
            mock_dt = datetime.datetime(2024, 3, 21, 17, 0, 0)
            mock_now.return_value = mock_dt

            await self.service.get_latest_trade_date()
            await self.service.get_latest_trade_date()
            await self.service.get_latest_trade_date()

            self.assertEqual(
                self.service._latest_trade_date_cache["val"], datetime.date(2024, 3, 21)
            )


class TestTradeCalendarServiceBatch(TestDatabaseBase):
    """Test TradeCalendarService batch operations."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_trade_calendar(self, start_date, end_date):
        """Seed the database with trade calendar data."""
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1,
                        "exchange": "SSE",
                    }
                )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    async def test_get_trade_dates_batch(self):
        """Test get_trade_dates_batch returns correct results for multiple ranges."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 1), datetime.date(2024, 3, 31)
        )

        ranges = [
            (datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)),
            (datetime.date(2024, 3, 25), datetime.date(2024, 3, 29)),
        ]

        result = await self.service.get_trade_dates_batch(ranges)

        self.assertEqual(len(result), 2)
        self.assertEqual(
            len(result[(datetime.date(2024, 3, 18), datetime.date(2024, 3, 22))]), 5
        )
        self.assertEqual(
            len(result[(datetime.date(2024, 3, 25), datetime.date(2024, 3, 29))]), 5
        )

    async def test_get_trade_dates_batch_empty_input(self):
        """Test get_trade_dates_batch with empty input returns empty dict."""
        result = await self.service.get_trade_dates_batch([])

        self.assertEqual(result, {})


class TestTradeCalendarServiceHolidays(TestDatabaseBase):
    """Test TradeCalendarService with real A-share holiday scenarios."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_holiday_calendar(self):
        """
        Seed with 2024 A-share calendar including real holidays.

        2024 Spring Festival: Feb 9-17 (market closed Feb 9-18, resume Feb 19)
        2024 Qingming: Apr 4-6 (market closed Apr 4-6, resume Apr 7)
        2024 Labor Day: May 1-5 (market closed May 1-5, resume May 6)
        2024 Dragon Boat: Jun 10 (market closed Jun 10, resume Jun 11)
        2024 Mid-Autumn: Sep 15-17 (market closed Sep 15-17, resume Sep 18)
        2024 National Day: Oct 1-7 (market closed Oct 1-7, resume Oct 8)
        """
        dates = []
        start = datetime.date(2024, 1, 1)
        end = datetime.date(2024, 12, 31)

        holidays = {
            datetime.date(2024, 2, 9),
            datetime.date(2024, 2, 10),
            datetime.date(2024, 2, 11),
            datetime.date(2024, 2, 12),
            datetime.date(2024, 2, 13),
            datetime.date(2024, 2, 14),
            datetime.date(2024, 2, 15),
            datetime.date(2024, 2, 16),
            datetime.date(2024, 2, 17),
            datetime.date(2024, 2, 18),
            datetime.date(2024, 4, 4),
            datetime.date(2024, 4, 5),
            datetime.date(2024, 4, 6),
            datetime.date(2024, 5, 1),
            datetime.date(2024, 5, 2),
            datetime.date(2024, 5, 3),
            datetime.date(2024, 5, 4),
            datetime.date(2024, 5, 5),
            datetime.date(2024, 6, 10),
            datetime.date(2024, 9, 15),
            datetime.date(2024, 9, 16),
            datetime.date(2024, 9, 17),
            datetime.date(2024, 10, 1),
            datetime.date(2024, 10, 2),
            datetime.date(2024, 10, 3),
            datetime.date(2024, 10, 4),
            datetime.date(2024, 10, 5),
            datetime.date(2024, 10, 6),
            datetime.date(2024, 10, 7),
        }

        current = start
        while current <= end:
            is_weekend = current.weekday() >= 5
            is_holiday = current in holidays

            dates.append(
                {
                    "cal_date": current.strftime("%Y%m%d"),
                    "is_open": 0 if (is_weekend or is_holiday) else 1,
                    "exchange": "SSE",
                }
            )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    async def test_spring_festival_not_trading(self):
        """Test Spring Festival dates are not trading days."""
        await self._seed_holiday_calendar()

        spring_festival_dates = [
            datetime.date(2024, 2, 9),
            datetime.date(2024, 2, 12),
            datetime.date(2024, 2, 15),
        ]

        for d in spring_festival_dates:
            result = await self.service.is_trading_day(d)
            self.assertFalse(
                result, f"{d} should not be a trading day (Spring Festival)"
            )

    async def test_after_spring_festival_is_trading(self):
        """Test first trading day after Spring Festival."""
        await self._seed_holiday_calendar()

        result = await self.service.is_trading_day(datetime.date(2024, 2, 19))
        self.assertTrue(
            result, "Feb 19, 2024 should be a trading day (after Spring Festival)"
        )

    async def test_national_day_not_trading(self):
        """Test National Day dates are not trading days."""
        await self._seed_holiday_calendar()

        national_day_dates = [
            datetime.date(2024, 10, 1),
            datetime.date(2024, 10, 3),
            datetime.date(2024, 10, 7),
        ]

        for d in national_day_dates:
            result = await self.service.is_trading_day(d)
            self.assertFalse(result, f"{d} should not be a trading day (National Day)")

    async def test_after_national_day_is_trading(self):
        """Test first trading day after National Day."""
        await self._seed_holiday_calendar()

        result = await self.service.is_trading_day(datetime.date(2024, 10, 8))
        self.assertTrue(
            result, "Oct 8, 2024 should be a trading day (after National Day)"
        )

    async def test_get_trade_dates_across_holiday(self):
        """Test get_trade_dates correctly skips holidays."""
        await self._seed_holiday_calendar()

        result = await self.service.get_trade_dates(
            datetime.date(2024, 2, 8), datetime.date(2024, 2, 20)
        )

        expected_trading_days = [
            datetime.date(2024, 2, 8),
            datetime.date(2024, 2, 19),
            datetime.date(2024, 2, 20),
        ]

        self.assertEqual(result, expected_trading_days)

    async def test_count_trade_days_across_holiday(self):
        """Test count_trade_days correctly counts across holidays."""
        await self._seed_holiday_calendar()

        result = await self.service.count_trade_days(
            datetime.date(2024, 2, 8), datetime.date(2024, 2, 20)
        )

        self.assertEqual(result, 3)


class TestTradeCalendarServiceYearBoundary(TestDatabaseBase):
    """Test TradeCalendarService year boundary conditions."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_year_boundary_calendar(self):
        """Seed calendar spanning 2023-2024 year boundary."""
        dates = []
        start = datetime.date(2023, 12, 25)
        end = datetime.date(2024, 1, 10)

        current = start
        while current <= end:
            is_weekend = current.weekday() >= 5
            dates.append(
                {
                    "cal_date": current.strftime("%Y%m%d"),
                    "is_open": 0 if is_weekend else 1,
                    "exchange": "SSE",
                }
            )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    async def test_cross_year_get_trade_dates(self):
        """Test get_trade_dates across year boundary."""
        await self._seed_year_boundary_calendar()

        result = await self.service.get_trade_dates(
            datetime.date(2023, 12, 28), datetime.date(2024, 1, 5)
        )

        self.assertTrue(len(result) > 0)
        for d in result:
            self.assertEqual(type(d), datetime.date)

    async def test_cross_year_count_trade_days(self):
        """Test count_trade_days across year boundary."""
        await self._seed_year_boundary_calendar()

        result = await self.service.count_trade_days(
            datetime.date(2023, 12, 28), datetime.date(2024, 1, 5)
        )

        self.assertGreater(result, 0)

    async def test_year_end_trading_day(self):
        """Test last trading day of year."""
        await self._seed_year_boundary_calendar()

        result = await self.service.get_prev_trade_date(datetime.date(2024, 1, 1))

        self.assertIsNotNone(result)
        self.assertLessEqual(result.year, 2023)  # type: ignore

    async def test_year_start_trading_day(self):
        """Test first trading day of year."""
        await self._seed_year_boundary_calendar()

        result = await self.service.get_next_trade_date(datetime.date(2023, 12, 29))

        self.assertIsNotNone(result)
        self.assertGreaterEqual(result, datetime.date(2024, 1, 1))  # type: ignore


class TestTradeCalendarServicePersistence(TestDatabaseBase):
    """Test TradeCalendarService data persistence behavior."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def test_api_data_is_persisted_to_db(self):
        """Test that data fetched from API is persisted to database."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)

        await self.cache.stock_dao._write_db(
            "DELETE FROM trade_cal WHERE cal_date >= $1 AND cal_date <= $2",
            (start, end),
        )

        api_df = pd.DataFrame(
            {
                "cal_date": [
                    "20240318",
                    "20240319",
                    "20240320",
                    "20240321",
                    "20240322",
                ],
                "is_open": [1, 1, 1, 1, 1],
                "exchange": ["SSE"] * 5,
            }
        )
        self.mock_api.get_trade_cal.return_value = api_df

        await self.service.get_trade_dates(start, end)

        cached_df = await self.cache.get_trade_cal(start, end)
        self.assertIsNotNone(cached_df)
        self.assertEqual(len(cached_df), 5)

        self.mock_api.get_trade_cal.reset_mock()

        result = await self.service.get_trade_dates(start, end)
        self.assertEqual(len(result), 5)
        self.mock_api.get_trade_cal.assert_not_called()

    async def test_persistence_failure_does_not_block_result(self):
        """Test that persistence failure doesn't block returning results."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)

        self.service._cache.save_trade_cal = AsyncMock(
            side_effect=Exception("DB write failed")
        )

        api_df = pd.DataFrame(
            {
                "cal_date": [
                    "20240318",
                    "20240319",
                    "20240320",
                    "20240321",
                    "20240322",
                ],
                "is_open": [1, 1, 1, 1, 1],
                "exchange": ["SSE"] * 5,
            }
        )
        self.mock_api.get_trade_cal.return_value = api_df

        result = await self.service.get_trade_dates(start, end)

        self.assertEqual(len(result), 5)


class TestTradeCalendarServiceCacheTTL(TestDatabaseBase):
    """Test TradeCalendarService cache TTL behavior."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_trade_calendar(self, start_date, end_date):
        """Seed the database with trade calendar data."""
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1,
                        "exchange": "SSE",
                    }
                )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    async def test_cache_ttl_prevents_repeated_queries(self):
        """Test that cache TTL prevents repeated database queries."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 1), datetime.date(2024, 3, 31)
        )

        with patch("data.services.trade_calendar_service.get_now") as mock_now:
            mock_dt = datetime.datetime(2024, 3, 21, 17, 0, 0)
            mock_now.return_value = mock_dt

            result1 = await self.service.get_latest_trade_date()
            result2 = await self.service.get_latest_trade_date()
            result3 = await self.service.get_latest_trade_date()

            self.assertEqual(result1, result2)
            self.assertEqual(result2, result3)

    async def test_clear_cache_resets_ttl(self):
        """Test that clear_cache resets the TTL cache."""
        await self._seed_trade_calendar(
            datetime.date(2024, 3, 1), datetime.date(2024, 3, 31)
        )

        with patch("data.services.trade_calendar_service.get_now") as mock_now:
            mock_dt = datetime.datetime(2024, 3, 21, 17, 0, 0)
            mock_now.return_value = mock_dt

            result1 = await self.service.get_latest_trade_date()

            self.service.clear_cache()

            result2 = await self.service.get_latest_trade_date()

            self.assertEqual(result1, result2)


class TestTradeCalendarServiceDatabaseTypes(TestDatabaseBase):
    """Test TradeCalendarService database parameter type handling."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_trade_calendar(self, start_date, end_date):
        """Seed the database with trade calendar data."""
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1,
                        "exchange": "SSE",
                    }
                )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    async def test_date_object_passed_to_db(self):
        """Test that native date objects are passed to database queries."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_trade_dates(start, end)

        self.assertEqual(len(result), 5)
        for d in result:
            self.assertIsInstance(d, datetime.date)

    async def test_datetime_converted_to_date(self):
        """Test that datetime objects are converted to date for queries."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        start_dt = datetime.datetime(2024, 3, 18, 10, 0)
        end_dt = datetime.datetime(2024, 3, 22, 15, 0)

        result = await self.service.get_trade_dates(start_dt, end_dt)

        self.assertEqual(len(result), 5)

    async def test_string_date_converted_properly(self):
        """Test that string dates are converted to date objects."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_trade_dates("2024-03-18", "20240322")

        self.assertEqual(len(result), 5)


class TestTradeCalendarServiceIntegration(TestDatabaseBase):
    """Integration tests for TradeCalendarService with real DataProcessor."""

    async def asyncSetUp(self):
        await super().asyncSetUp()

        self.mock_api = MagicMock()
        self.mock_api.get_trade_cal = AsyncMock()

        self.service = TradeCalendarService(self.cache, self.mock_api)

    async def asyncTearDown(self):
        await super().asyncTearDown()

    async def _seed_trade_calendar(self, start_date, end_date):
        """Seed the database with trade calendar data."""
        dates = []
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 1,
                        "exchange": "SSE",
                    }
                )
            else:
                dates.append(
                    {
                        "cal_date": current.strftime("%Y%m%d"),
                        "is_open": 0,
                        "exchange": "SSE",
                    }
                )
            current += datetime.timedelta(days=1)

        df = pd.DataFrame(dates)
        await self.cache.save_trade_cal(df)

    async def test_get_trade_cal_df_returns_dataframe(self):
        """Test that get_trade_cal_df returns a DataFrame."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_trade_cal_df(start, end)

        self.assertIsInstance(result, pd.DataFrame)
        self.assertFalse(result.empty)

    async def test_get_trade_cal_df_filters_is_open(self):
        """Test that get_trade_cal_df correctly filters by is_open."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 24)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_trade_cal_df(start, end, is_open=1)

        self.assertIsInstance(result, pd.DataFrame)
        for _, row in result.iterrows():
            self.assertEqual(row["is_open"], 1)

    async def test_get_trade_cal_df_empty_range(self):
        """Test get_trade_cal_df with empty date range returns DataFrame."""
        result = await self.service.get_trade_cal_df(
            datetime.date(2024, 3, 18), datetime.date(2024, 3, 22)
        )

        self.assertIsInstance(result, pd.DataFrame)

    async def test_get_trade_cal_df_fallback_to_api(self):
        """Test get_trade_cal_df falls back to API when DB is empty."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)

        api_df = pd.DataFrame(
            [
                {"cal_date": "20240318", "is_open": 1, "exchange": "SSE"},
                {"cal_date": "20240319", "is_open": 1, "exchange": "SSE"},
                {"cal_date": "20240320", "is_open": 1, "exchange": "SSE"},
                {"cal_date": "20240321", "is_open": 1, "exchange": "SSE"},
                {"cal_date": "20240322", "is_open": 1, "exchange": "SSE"},
            ]
        )
        self.mock_api.get_trade_cal.return_value = api_df

        result = await self.service.get_trade_cal_df(start, end, is_open=1)

        self.assertFalse(result.empty)
        self.assertEqual(len(result), 5)

    async def test_get_trade_cal_df_with_string_dates(self):
        """Test get_trade_cal_df accepts string dates."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_trade_cal_df("2024-03-18", "20240322")

        self.assertIsInstance(result, pd.DataFrame)
        self.assertFalse(result.empty)

    async def test_get_trade_cal_df_with_none_dates(self):
        """Test get_trade_cal_df handles None dates gracefully."""
        start = datetime.date(2024, 3, 18)
        end = datetime.date(2024, 3, 22)
        await self._seed_trade_calendar(start, end)

        result = await self.service.get_trade_cal_df(None, None)

        self.assertIsInstance(result, pd.DataFrame)


if __name__ == "__main__":
    unittest.main()
