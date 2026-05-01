import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd

# Make sure to import the class to be tested
# Assuming path is setup or we run as module
from data.constants import (
    DATAFRAME_ATTR_COLUMN_UNITS,
    DATAFRAME_ATTR_COLUMN_UNIT_SOURCES,
    TOP_LIST_NET_AMOUNT_UNIT,
    TOP_LIST_NET_AMOUNT_UNIT_SOURCE,
)
from data.external.tushare_client import TushareClient
from utils.config_handler import ConfigHandler


class TestTushareClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        TushareClient._reset_singleton()

    def tearDown(self):
        TushareClient._reset_singleton()

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_get_top_list_attaches_net_amount_unit_metadata(self, mock_set_token, mock_pro_api):
        mock_pro_api.return_value = MagicMock()
        client = TushareClient(token="dummy")
        client._handle_api_call = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "trade_date": ["20230101"],
                    "ts_code": ["000001.SZ"],
                    "net_amount": [1000.0],
                }
            )
        )

        df = await client.get_top_list("20230101")

        self.assertEqual(
            df.attrs[DATAFRAME_ATTR_COLUMN_UNITS]["net_amount"],
            TOP_LIST_NET_AMOUNT_UNIT,
        )
        self.assertEqual(
            df.attrs[DATAFRAME_ATTR_COLUMN_UNIT_SOURCES]["net_amount"],
            TOP_LIST_NET_AMOUNT_UNIT_SOURCE,
        )

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
            client._rate_limiter = MagicMock()
            client._rate_limiter.consume_async = AsyncMock()

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

        with self.assertRaises(Exception) as context:
            await client.get_daily_quotes(ts_code="000001.SZ")
        self.assertEqual(str(context.exception), "General Error")

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


class TestSlowApiLimiters(unittest.IsolatedAsyncioTestCase):
    """测试慢速 API 专用限流器（P0 级）"""

    def setUp(self):
        TushareClient._reset_singleton()

    def tearDown(self):
        TushareClient._reset_singleton()

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_slow_api_limiters_initialized(self, mock_set_token, mock_pro_api):
        """慢速 API 限流器按 _SLOW_API_OVERRIDES 正确创建"""
        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="dummy")

            self.assertIn("top10_holders", client._slow_api_limiters)
            self.assertIn("concept_detail", client._slow_api_limiters)
            self.assertEqual(len(client._slow_api_limiters), 2)

            top10_limiter = client._slow_api_limiters["top10_holders"]
            expected_rate = (200 / 60.0) * 0.5
            self.assertAlmostEqual(top10_limiter.rate, expected_rate, places=2)

            concept_limiter = client._slow_api_limiters["concept_detail"]
            expected_rate_concept = (200 / 60.0) * 0.3
            self.assertAlmostEqual(concept_limiter.rate, expected_rate_concept, places=2)

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_no_slow_limiters_when_rate_limit_disabled(self, mock_set_token, mock_pro_api):
        """无限流配置时不创建慢速限流器"""
        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=0):
            client = TushareClient(token="dummy")

            self.assertIsNone(client._rate_limiter)
            self.assertEqual(client._slow_api_limiters, {})

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_slow_api_uses_dedicated_limiter(self, mock_set_token, mock_pro_api, mock_sleep):
        """top10_holders API 调用走专用慢速限流器"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_df = MagicMock()
        mock_df.empty = False
        mock_api_instance.top10_holders.__name__ = "top10_holders"
        mock_api_instance.top10_holders.return_value = mock_df

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="dummy")

            slow_limiter = client._slow_api_limiters["top10_holders"]
            slow_limiter.consume_async = AsyncMock()
            slow_limiter.on_success = MagicMock()

            general_limiter = client._rate_limiter
            general_limiter.consume_async = AsyncMock()
            general_limiter.on_success = MagicMock()

            await client.get_top10_holders(ts_code="000001.SZ", period="20231231")

            slow_limiter.consume_async.assert_called()
            general_limiter.consume_async.assert_not_called()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_non_slow_api_uses_general_limiter(self, mock_set_token, mock_pro_api, mock_sleep):
        """非慢速 API 调用走通用限流器"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_df = MagicMock()
        mock_df.empty = False
        mock_api_instance.daily.return_value = mock_df
        mock_api_instance.adj_factor.return_value = None

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="dummy")

            general_limiter = client._rate_limiter
            general_limiter.consume_async = AsyncMock()
            general_limiter.on_success = MagicMock()

            for limiter in client._slow_api_limiters.values():
                limiter.consume_async = AsyncMock()
                limiter.on_success = MagicMock()

            await client.get_daily_quotes(ts_code="000001.SZ")

            general_limiter.consume_async.assert_called()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_rate_limit_error_calls_reduce_rate(self, mock_set_token, mock_pro_api, mock_sleep):
        """限流错误时 reduce_rate 被调用"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_api_instance.daily.side_effect = [
            Exception("抱歉，您每分钟最多访问"),
            MagicMock(empty=False),
        ]
        mock_api_instance.adj_factor.return_value = None

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="dummy")

            general_limiter = client._rate_limiter
            general_limiter.consume_async = AsyncMock()
            general_limiter.reduce_rate = MagicMock()

            await client.get_daily_quotes(ts_code="000001.SZ")

            general_limiter.reduce_rate.assert_called_once_with(factor=0.5)

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_rate_limit_on_slow_api_calls_slow_reduce_rate(self, mock_set_token, mock_pro_api, mock_sleep):
        """慢速 API 限流错误时调用慢速限流器的 reduce_rate"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_api_instance.top10_holders.__name__ = "top10_holders"
        mock_api_instance.top10_holders.side_effect = [
            Exception("频次超限"),
            MagicMock(empty=False),
        ]

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="dummy")

            slow_limiter = client._slow_api_limiters["top10_holders"]
            slow_limiter.consume_async = AsyncMock()
            slow_limiter.reduce_rate = MagicMock()

            general_limiter = client._rate_limiter
            general_limiter.consume_async = AsyncMock()
            general_limiter.reduce_rate = MagicMock()

            await client.get_top10_holders(ts_code="000001.SZ", period="20231231")

            slow_limiter.reduce_rate.assert_called_once_with(factor=0.5)
            general_limiter.reduce_rate.assert_not_called()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_success_calls_on_success(self, mock_set_token, mock_pro_api, mock_sleep):
        """API 成功返回时 on_success 被调用"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_df = MagicMock()
        mock_df.empty = False
        mock_api_instance.daily.return_value = mock_df
        mock_api_instance.adj_factor.return_value = None

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="dummy")

            general_limiter = client._rate_limiter
            general_limiter.consume_async = AsyncMock()
            general_limiter.on_success = MagicMock()

            await client.get_daily_quotes(ts_code="000001.SZ")

            general_limiter.on_success.assert_called()

    @patch("asyncio.sleep", new_callable=AsyncMock)
    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    async def test_permission_error_not_retried(self, mock_set_token, mock_pro_api, mock_sleep):
        """权限错误不重试，直接抛出"""
        mock_api_instance = MagicMock()
        mock_pro_api.return_value = mock_api_instance

        mock_api_instance.daily.side_effect = Exception("积分不足，无权访问此接口")

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="dummy")
            client._rate_limiter = None

            with self.assertRaises(Exception) as context:
                await client.get_daily_quotes(ts_code="000001.SZ")
            self.assertIn("积分", str(context.exception))

            self.assertEqual(mock_api_instance.daily.call_count, 1)


class TestSetTokenRebuildsLimiters(unittest.IsolatedAsyncioTestCase):
    """测试 set_token 正确重建限流器（P0 级）"""

    def setUp(self):
        TushareClient._reset_singleton()

    def tearDown(self):
        TushareClient._reset_singleton()

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_set_token_rebuilds_rate_limiter(self, mock_set_token, mock_pro_api):
        """set_token 更新 token 后重建通用限流器"""
        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=120):
            client = TushareClient(token="old_token")

            old_limiter = client._rate_limiter
            self.assertIsNotNone(old_limiter)

            with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
                client.set_token("new_token")

                self.assertIsNotNone(client._rate_limiter)
                self.assertAlmostEqual(client._rate_limiter.rate, 200 / 60.0, places=2)

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_set_token_rebuilds_slow_limiters(self, mock_set_token, mock_pro_api):
        """set_token 后慢速限流器被重建"""
        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=120):
            client = TushareClient(token="old_token")

            with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
                client.set_token("new_token")

                self.assertIn("top10_holders", client._slow_api_limiters)
                self.assertIn("concept_detail", client._slow_api_limiters)

                new_top10 = client._slow_api_limiters["top10_holders"]
                expected_rate = (200 / 60.0) * 0.5
                self.assertAlmostEqual(new_top10.rate, expected_rate, places=2)

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_set_token_clears_limiters_when_disabled(self, mock_set_token, mock_pro_api):
        """set_token 时无限流配置应清空限流器"""
        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client = TushareClient(token="old_token")

            self.assertIsNotNone(client._rate_limiter)
            self.assertGreater(len(client._slow_api_limiters), 0)

            with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=0):
                client.set_token("new_token")

                self.assertIsNone(client._rate_limiter)
                self.assertEqual(client._slow_api_limiters, {})

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_set_token_creates_limiter_when_previously_none(self, mock_set_token, mock_pro_api):
        """set_token 时从无限流变为有限流应创建限流器"""
        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=0):
            client = TushareClient(token="old_token")

            self.assertIsNone(client._rate_limiter)

            with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
                client.set_token("new_token")

                self.assertIsNotNone(client._rate_limiter)
                self.assertAlmostEqual(client._rate_limiter.rate, 200 / 60.0, places=2)
                self.assertIn("top10_holders", client._slow_api_limiters)


class TestClassVariableDirtyDataFix(unittest.IsolatedAsyncioTestCase):
    """P0-2 修复验证：类变量残留脏数据问题"""

    def setUp(self):
        TushareClient._reset_singleton()

    def tearDown(self):
        TushareClient._reset_singleton()

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_reset_singleton_clears_trade_cal_cache(self, mock_set_token, mock_pro_api):
        """_reset_singleton 后新实例的交易日历缓存应为空"""
        mock_pro_api.return_value = MagicMock()

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=0):
            client1 = TushareClient(token="token1")
            client1._trade_cal_cache.add("20250101")
            client1._loaded_years.add("2025")
            self.assertEqual(len(client1._trade_cal_cache), 1)
            self.assertEqual(len(client1._loaded_years), 1)

        TushareClient._reset_singleton()

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=0):
            client2 = TushareClient(token="token2")
            self.assertEqual(len(client2._trade_cal_cache), 0)
            self.assertEqual(len(client2._loaded_years), 0)

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_reset_singleton_clears_rate_limiter(self, mock_set_token, mock_pro_api):
        """_reset_singleton 后新实例的限流器应为全新对象"""
        mock_pro_api.return_value = MagicMock()

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client1 = TushareClient(token="token1")
            old_limiter = client1._rate_limiter
            old_slow_limiter = client1._slow_api_limiters.get("top10_holders")

        TushareClient._reset_singleton()

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=200):
            client2 = TushareClient(token="token2")
            self.assertIsNot(client2._rate_limiter, old_limiter)
            self.assertIsNot(client2._slow_api_limiters.get("top10_holders"), old_slow_limiter)

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_trade_cal_cache_is_instance_variable(self, mock_set_token, mock_pro_api):
        """交易日历缓存应为实例变量而非类变量"""
        mock_pro_api.return_value = MagicMock()

        with patch.object(ConfigHandler, "get_tushare_api_limit", return_value=0):
            client = TushareClient(token="dummy")
            self.assertIn("_trade_cal_cache", client.__dict__)
            self.assertIn("_loaded_years", client.__dict__)
            self.assertIn("_calendar_lock", client.__dict__)

    @patch("tushare.pro_api")
    @patch("tushare.set_token")
    def test_no_class_level_mutable_state(self, mock_set_token, mock_pro_api):
        """类级别不应有可变状态（_trade_cal_cache, _loaded_years）"""
        self.assertFalse(hasattr(TushareClient, "_trade_cal_cache"))
        self.assertFalse(hasattr(TushareClient, "_loaded_years"))


if __name__ == "__main__":
    unittest.main()
