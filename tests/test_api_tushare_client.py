import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Make sure to import the class to be tested
# Assuming path is setup or we run as module
from data.tushare_client import TushareClient
from utils.config_handler import ConfigHandler


class TestTushareClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Reset singleton
        TushareClient._instance = None

    def tearDown(self):
        TushareClient._instance = None

    # Legacy RateLimiter tests removed as RateLimiter class was replaced by TokenBucket

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_client_respects_rate_limit(
        self,
        mock_set_token,
        mock_pro_api,
        mock_sleep,
    ):
        """Test that client integration works"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        # Mock DataFrame
        mock_df = MagicMock()
        mock_df.empty = False
        mock_api_instance.daily.return_value = mock_df
        mock_api_instance.adj_factor.return_value = None

        # Mock Config to return a known limit
        with patch.object(
            ConfigHandler,
            "get_tushare_api_limit",
            return_value=60,
        ):  # 1s interval
            client = TushareClient(token="dummy")

            # Mock internal limiter to verify acquire is called
            client._rate_limiter = AsyncMock()

            await client.get_daily_quotes(ts_code="000001.SZ")

            await client.get_daily_quotes(ts_code="000001.SZ")

            # get_daily_quotes calls daily and adj_factor, so consume_async might be called multiple times
            self.assertGreaterEqual(client._rate_limiter.consume_async.call_count, 1)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_retry_on_rate_limit(self, mock_set_token, mock_pro_api, mock_sleep):
        """Test retry logic when rate limit is hit"""
        # Setup mock API
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_df = MagicMock()
        mock_df.empty = False

        # Define side effects: 2 failures then success
        mock_api_instance.daily.side_effect = [
            Exception("抱歉，您每分钟最多访问"),
            Exception("抱歉，您每分钟最多访问"),
            mock_df,
        ]
        mock_api_instance.adj_factor.return_value = None

        client = TushareClient(token="dummy")
        # Disable rate limiter for this test to focus on retry logic
        client._rate_limiter = None

        result = await client.get_daily_quotes(ts_code="000001.SZ")

        self.assertIsNotNone(result)
        self.assertEqual(mock_api_instance.daily.call_count, 3)
        self.assertGreaterEqual(mock_sleep.call_count, 2)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_retry_on_network_error(
        self,
        mock_set_token,
        mock_pro_api,
        mock_sleep,
    ):
        """Test retry logic on network error"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_df = MagicMock()
        mock_df.empty = False

        mock_api_instance.daily.side_effect = [Exception("Connection timeout"), mock_df]
        mock_api_instance.adj_factor.return_value = None

        client = TushareClient(token="dummy")
        client._rate_limiter = None

        result = await client.get_daily_quotes(ts_code="000001.SZ")

        self.assertIsNotNone(result)
        self.assertEqual(mock_api_instance.daily.call_count, 2)
        self.assertGreaterEqual(mock_sleep.call_count, 1)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_failure_after_max_retries(
        self,
        mock_set_token,
        mock_pro_api,
        mock_sleep,
    ):
        """Test that it raises exception after max retries"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_api_instance.daily.side_effect = Exception("General Error")

        client = TushareClient(token="dummy")
        client._rate_limiter = None

        with self.assertRaises(Exception):
            await client.get_daily_quotes(ts_code="000001.SZ")

        self.assertEqual(
            mock_api_instance.daily.call_count,
            3,
        )  # Max retries default is 3

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_is_trading_day_caching(self, mock_set_token, mock_pro_api):
        """Test that is_trading_day uses year-based caching"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        # Mock API Response for 2025: Only 20250101 is trading day
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.__getitem__.return_value.tolist.return_value = [
            "20250101",
        ]  # cal_date column

        mock_api_instance.trade_cal.return_value = mock_df

        client = TushareClient(token="dummy")
        # clear cache for test
        TushareClient._trade_cal_cache = set()
        TushareClient._loaded_years = set()

        # 1. First Call: Should hit API (load 2025)
        is_open = client.is_trading_day("20250101")
        self.assertTrue(is_open)
        self.assertEqual(mock_api_instance.trade_cal.call_count, 1)

        # Verify API called with full year range
        args, kwargs = mock_api_instance.trade_cal.call_args
        self.assertEqual(kwargs["start_date"], "20250101")
        self.assertEqual(kwargs["end_date"], "20251231")

        # 2. Second Call (Same Year, Different Date): Should HIT CACHE (No API call)
        is_open_2 = client.is_trading_day("20250102")  # Not in the mocked list
        self.assertFalse(is_open_2)
        self.assertEqual(mock_api_instance.trade_cal.call_count, 1)  # Count stays 1

        # 3. Third Call (Different Year): Should hit API again
        client.is_trading_day("20260101")
        self.assertEqual(mock_api_instance.trade_cal.call_count, 2)  # Count increases


if __name__ == "__main__":
    unittest.main()
