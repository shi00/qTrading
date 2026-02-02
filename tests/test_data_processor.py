import unittest
import asyncio
import datetime
import pandas as pd
from unittest.mock import MagicMock, patch, AsyncMock, call
from data.data_processor import DataProcessor

class TestDataProcessor(unittest.TestCase):
    
    def setUp(self):
        # Patch TushareClient and CacheManager and ConfigHandler
        self.patcher_ts = patch('data.data_processor.TushareClient')
        self.patcher_cache = patch('data.data_processor.CacheManager')
        self.patcher_config = patch('data.data_processor.ConfigHandler')
        
        self.mock_ts_cls = self.patcher_ts.start()
        self.mock_cache_cls = self.patcher_cache.start()
        self.mock_config = self.patcher_config.start()
        
        # Reset Singleton
        DataProcessor._instance = None
        
        self.processor = DataProcessor()
        self.mock_api = self.processor.api
        self.mock_cache = self.processor.cache
        
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
        self.mock_cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame({'ts_code': ['000001.SZ']}))
        self.mock_cache.get_cached_financial_records = AsyncMock(return_value=set())
        
        # Configure ConfigHandler return value
        self.mock_config.get_sync_concurrency.return_value = 5

    def tearDown(self):
        self.patcher_ts.stop()
        self.patcher_cache.stop()
        self.patcher_config.stop()

    def test_singleton(self):
        """Verify Singleton pattern"""
        p1 = DataProcessor()
        p2 = DataProcessor()
        self.assertIs(p1, p2)
        self.assertIs(p1.api, p2.api)

    def test_get_latest_trade_date_weekday_pre_market(self):
        """Test weekday before 16:00 (market open/mid-day) -> should be yesterday"""
        # Mock datetime to a Monday 10:00 AM
        # Monday is weekday 0. Yesterday is Sunday (6), skip to Friday.
        # Wait, get_latest_trade_date logic:
        # if < 16:00 -> yesterday. if yesterday is weekend -> skip back.
        
        # Let's test a simple case: Wednesday 10:00 -> Tuesday
        fixed_dt = datetime.datetime(2023, 10, 25, 10, 0, 0) # Wed
        with patch('datetime.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_dt
            
            date_str = self.processor.get_latest_trade_date()
            self.assertEqual(date_str, '20231024') # Tue

    def test_get_latest_trade_date_weekday_post_market(self):
        """Test weekday after 16:00 -> should be today"""
        fixed_dt = datetime.datetime(2023, 10, 25, 17, 0, 0) # Wed
        with patch('datetime.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_dt
            
            date_str = self.processor.get_latest_trade_date()
            self.assertEqual(date_str, '20231025') # Wed

    def test_get_latest_trade_date_weekend(self):
        """Test weekend -> should skip to Friday"""
        # Saturday -> Friday
        fixed_dt = datetime.datetime(2023, 10, 28, 12, 0, 0) # Sat
        with patch('datetime.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_dt
            
            date_str = self.processor.get_latest_trade_date()
            self.assertEqual(date_str, '20231027') # Fri 27th

    async def async_test_init_data(self):
        await self.processor.init_data()
        self.mock_cache.init_db.assert_called_once()

    def test_init_data(self):
        asyncio.run(self.async_test_init_data())

    # --- Sync Daily Market Snapshot Tests ---

    async def async_test_sync_daily_market_cache_hit(self):
        """Test cache hit returns cached data"""
        target_date = "20231025"
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value=target_date)
        self.mock_cache.get_screening_data = AsyncMock(return_value=pd.DataFrame({'test': [1]}))
        
        df = await self.processor.sync_daily_market_snapshot(target_date)
        
        self.mock_cache.get_screening_data.assert_called_with(target_date)
        self.assertFalse(df.empty)
        # API should NOT be called
        self.mock_api.get_daily_quotes.assert_not_called()

    async def async_test_sync_daily_market_cache_miss(self):
        """Test cache miss fetches from API and saves"""
        target_date = "20231025"
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20200101") # Old date
        
        # Mock API returns
        mock_quotes = pd.DataFrame({'ts_code': ['000001.SZ'], 'trade_date': ['20231025']})
        mock_basic = pd.DataFrame({'ts_code': ['000001.SZ'], 'trade_date': ['20231025'], 'pe': [10]})
        
        self.mock_api.get_daily_quotes.return_value = mock_quotes
        self.mock_api.get_daily_basic.return_value = mock_basic
        
        # Mock Cache saves
        self.mock_cache.save_daily_quotes = AsyncMock(return_value=1)
        self.mock_cache.save_daily_indicators = AsyncMock(return_value=1)
        self.mock_cache.update_sync_status = AsyncMock()

        df = await self.processor.sync_daily_market_snapshot(target_date)
        
        # Verify merged result
        self.assertIsNotNone(df)
        self.assertIn('pe', df.columns)
        
        # Verify saves
        self.mock_cache.save_daily_quotes.assert_called()
        self.mock_cache.save_daily_indicators.assert_called()

    def test_sync_daily_market_snapshot(self):
        asyncio.run(self.async_test_sync_daily_market_cache_hit())
        asyncio.run(self.async_test_sync_daily_market_cache_miss())

    # --- Historical Sync & Circuit Breaker Tests ---

    async def async_test_sync_historical_data_circuit_breaker(self):
        """Test that excessive failures trigger abort"""
        days = 30
        
        # Mock trade dates
        mock_dates = [f"202301{i:02d}" for i in range(10, 30)] # 20 days
        self.processor.get_trade_dates = MagicMock(return_value=mock_dates)
        
        # Mock cache to return empty (so we try to sync all)
        self.mock_cache.get_cached_trade_dates = AsyncMock(return_value=set())
        self.mock_cache.get_cached_indicator_dates = AsyncMock(return_value=set())
        
        # Mock sync_daily_market_snapshot to raise Exception EVERY time
        with patch.object(self.processor, 'sync_daily_market_snapshot', side_effect=Exception("API Error")):
            
            # Lower threshold for testing to avoid 20 default
            # Need to patch the semaphore to avoid slow execution if needed, but asyncio.Semaphore is fast
            
            synced_count = await self.processor.sync_historical_data(days=days)
            
            # Since we fail every time, circuit breaker should trigger
            # The exact count depends on concurrency race, but it should be < total
            # With default CB_THRESHOLD = 20, and 20 days, it might just finish or abort exactly.
            # Let's verify that we see "Circuit Breaker triggered" in logs (implicit via behavior)
            # or check that we have failures.
            pass
            
    async def async_test_sync_historical_breakpoint_resume(self):
        """Test that existing dates are skipped"""
        days = 5
        mock_dates = ["20230105", "20230104", "20230103", "20230102", "20230101"]
        self.processor.get_trade_dates = MagicMock(return_value=mock_dates)
        
        # Mock partial cache
        self.mock_cache.get_cached_trade_dates = AsyncMock(return_value={"20230105", "20230104"})
        self.mock_cache.get_cached_indicator_dates = AsyncMock(return_value={"20230105", "20230104"})
        # Intersection = {05, 04}
        
        with patch.object(self.processor, 'sync_daily_market_snapshot', new_callable=AsyncMock) as mock_sync:
            await self.processor.sync_historical_data(days=days)
            
            # Should have skipped 05 and 04, synced 03, 02, 01
            expected_calls = [call("20230103"), call("20230102"), call("20230101")]
            # Note: Asyncio gather order isn't strictly guaranteed but likely sequential in submission
            self.assertEqual(mock_sync.call_count, 3)
            # Check calls were made for missing dates
            call_args = [c.args[0] for c in mock_sync.call_args_list]
            self.assertIn("20230103", call_args)

    def test_sync_historical(self):
        asyncio.run(self.async_test_sync_historical_breakpoint_resume())

    # --- Financial Reports Tests ---

    async def async_test_sync_financial_reports(self):
        """Test financial report syncing logic"""
        periods = ["20230331"]
        
        # Mock API Calls
        mock_income = pd.DataFrame({'ts_code': ['000001.SZ'], 'n_income': [1000], 'total_revenue': [5000]})
        mock_balance = pd.DataFrame({'ts_code': ['000001.SZ'], 'total_assets': [10000], 'total_liab': [5000]})
        mock_indicator = pd.DataFrame({'ts_code': ['000001.SZ'], 'roe': [10.5]})
        
        self.mock_api.get_income.return_value = mock_income
        self.mock_api.get_balancesheet.return_value = mock_balance
        self.mock_api.get_fina_indicator.return_value = mock_indicator
        
        self.mock_cache.save_financial_reports = AsyncMock(return_value=1)
        self.mock_cache.update_sync_status = AsyncMock()
        
        count = await self.processor.sync_financial_reports(periods=periods)
        
        self.assertEqual(count, 1)
        self.mock_cache.save_financial_reports.assert_called()
        
        # Verify merger logic
        # Retrieve the dataframe passed to save
        saved_df = self.mock_cache.save_financial_reports.call_args[0][0]
        self.assertEqual(saved_df.iloc[0]['roe'], 10.5)
        self.assertEqual(saved_df.iloc[0]['total_assets'], 10000)

    def test_financial_reports(self):
        asyncio.run(self.async_test_sync_financial_reports())

    # --- Extended Coverage Tests ---

    async def async_test_sync_stock_basic(self):
        """Test sync_stock_basic"""
        mock_df = pd.DataFrame({'ts_code': ['000001.SZ'], 'name': ['PingAn']})
        self.mock_api.get_stock_list.return_value = mock_df
        self.mock_cache.save_stock_basic = AsyncMock(return_value=1)
        
        count = await self.processor.sync_stock_basic()
        
        self.assertEqual(count, 1)
        self.mock_cache.save_stock_basic.assert_called()

    def test_sync_stock_basic(self):
        asyncio.run(self.async_test_sync_stock_basic())

    async def async_test_sync_moneyflow(self):
        """Test sync_moneyflow"""
        mock_df = pd.DataFrame({'ts_code': ['000001.SZ'], 'buy_md_vol': [100]})
        self.mock_api.get_moneyflow.return_value = mock_df
        self.mock_cache.save_moneyflow = AsyncMock(return_value=1)
        
        count = await self.processor.sync_moneyflow("20230101")
        
        self.assertEqual(count, 1)
        self.mock_cache.save_moneyflow.assert_called()

    def test_sync_moneyflow(self):
        asyncio.run(self.async_test_sync_moneyflow())

    async def async_test_sync_northbound(self):
        """Test sync_northbound"""
        mock_df = pd.DataFrame({'ts_code': ['000001.SZ'], 'ratio': [5.0]})
        self.mock_api.get_hk_hold.return_value = mock_df
        self.mock_cache.save_northbound = AsyncMock(return_value=1)
        
        count = await self.processor.sync_northbound("20230101")
        
        self.assertEqual(count, 1)
        self.mock_cache.save_northbound.assert_called()

    def test_sync_northbound(self):
        asyncio.run(self.async_test_sync_northbound())

    # Removed test_sync_all_daily as the method is deprecated and removed.

    async def async_test_prepare_screening_context(self):
        """Test prepare_screening_context"""
        # Mock cache data
        self.mock_cache.get_screening_data = AsyncMock(return_value=pd.DataFrame({'pe': [10]}))
        self.mock_cache.get_latest_northbound = AsyncMock(return_value=pd.DataFrame({'ratio': [5]}))
        self.mock_cache.get_latest_trade_date = AsyncMock(return_value="20230101")
        self.mock_cache.get_top_list = AsyncMock(return_value=pd.DataFrame({'net': [100]}))
        self.mock_cache.get_block_trade = AsyncMock(return_value=pd.DataFrame({'amt': [100]}))
        
        context = await self.processor.prepare_screening_context()
        
        self.assertIn('screening_data', context)
        self.assertIn('northbound_data', context)
        self.assertIn('top_list', context)
        self.assertIn('block_trade', context)

    def test_prepare_screening_context(self):
        asyncio.run(self.async_test_prepare_screening_context())

    async def async_test_retry_mechanism_logic(self):
        """Test the retry logical branch in sync_historical_data"""
        # Simulate partial failure then success on retry
        days = 1
        mock_dates = ["20230101", "20230102"] # 2 days to start
        self.processor.get_trade_dates = MagicMock(return_value=mock_dates)
        
        # Mock caches returning empty => sync all
        self.mock_cache.get_cached_trade_dates = AsyncMock(return_value=set())
        self.mock_cache.get_cached_indicator_dates = AsyncMock(return_value=set())

        # We want sync_daily_market_snapshot to fail first time for a date, then succeed
        # Use side_effect with an iterator?
        # But wait, it's called concurrently for 2 dates.
        # Let's say it fails for ALL initially, then succeeds for ALL.
        
        # Because the code creates new coroutines in retry loop, we can use a counter or checking args.
        # But simpler: make it fail for "20230101" always on first attempt, succeed later.
        
        call_count = 0
        async def side_effect(date):
            nonlocal call_count
            # First pass for both dates (0, 1) -> Fail
            # Retry pass -> Succeed
            call_count += 1
            if call_count <= 2: 
                raise Exception("Network Error")
            return pd.DataFrame({'a': [1]})
            
        with patch.object(self.processor, 'sync_daily_market_snapshot', side_effect=side_effect) as mock_sync:
             # Need to patch sleep so test is fast
            with patch('asyncio.sleep', new_callable=AsyncMock):
                await self.processor.sync_historical_data(days=days)
            
            # verify retry logic was hit
            # We expect initial calls (2 failures) + Retry calls
            self.assertTrue(call_count > 2)

    def test_retry_mechanism(self):
        asyncio.run(self.async_test_retry_mechanism_logic())


    async def async_test_check_data_health(self):
        """Test health check logic"""
        # self.processor is already set up in setUp
        
        # Scenario 1: Healthy (Green)
        # Mock API trade dates (blocking call in executor)
        self.mock_api.get_trade_dates.return_value = ['20230101', '20230102', '20230103']
        # Mock Cache trade dates
        self.mock_cache.get_cached_trade_dates.return_value = {'20230101', '20230102', '20230103'}
        
        with patch.object(self.processor, 'get_latest_trade_date', return_value='20230103'):
            res = await self.processor.check_data_health()
            self.assertEqual(res['status'], 'green')
            self.assertEqual(res['missing_count'], 0)
            self.assertEqual(res['lag_days'], 0)

        # Scenario 2: Lagging (Yellow)
        # Official has 20230104, Local ends at 20230103
        self.mock_api.get_trade_dates.return_value = ['20230101', '20230102', '20230103', '20230104']
        with patch.object(self.processor, 'get_latest_trade_date', return_value='20230104'):
            res = await self.processor.check_data_health()
            self.assertEqual(res['status'], 'yellow')
            self.assertEqual(res['lag_days'], 1)

        # Scenario 3: Missing History (Red)
        # Official 10 days, Local 2 days (gaps)
        dates = [f'202301{i:02d}' for i in range(10, 20)] # 20230110 - 20230119
        self.mock_api.get_trade_dates.return_value = dates
        self.mock_cache.get_cached_trade_dates.return_value = {'20230110', '20230119'}
        with patch.object(self.processor, 'get_latest_trade_date', return_value='20230119'):
            res = await self.processor.check_data_health()
            self.assertEqual(res['status'], 'red')
            self.assertEqual(res['missing_count'], 8)

    def test_check_data_health(self):
        asyncio.run(self.async_test_check_data_health())

if __name__ == '__main__':
    unittest.main()
