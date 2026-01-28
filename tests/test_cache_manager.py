import unittest
import asyncio
import pandas as pd
import aiosqlite
from unittest.mock import patch, MagicMock
from data.cache_manager import CacheManager

class TestCacheManager(unittest.TestCase):
    
    def setUp(self):
        # Use shared in-memory database so multiple connections see the same DB
        self.db_path = "file:testdb?mode=memory&cache=shared"
        self.cache = CacheManager(db_path=self.db_path)
        
        # Initialize DB schema before each test
        asyncio.run(self.cache.init_db())

    def tearDown(self):
        # Clear database to ensure test isolation
        async def _clear():
            await self.cache.clear_all_cache()
            # Also clear stock_basic manually since clear_all_cache skips it
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("DELETE FROM stock_basic")
                await db.commit()
            await self.cache.close() # Ensure writer task is stopped
        asyncio.run(_clear())

    async def async_test_stock_basic(self):
        """Test saving and retrieving stock basic info"""
        df = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'symbol': ['000001'],
            'name': ['PingAn'],
            'area': ['Shenzhen'],
            'industry': ['Bank'],
            'market': ['Main'],
            'list_date': ['19910403']
        })
        
        saved_count = await self.cache.save_stock_basic(df)
        await asyncio.sleep(0.5) # Wait for write
        self.assertEqual(saved_count, 1)
        
        result_df = await self.cache.get_stock_basic()
        self.assertEqual(len(result_df), 1)
        self.assertEqual(result_df.iloc[0]['name'], 'PingAn')

    def test_stock_basic(self):
        asyncio.run(self.async_test_stock_basic())

    async def async_test_daily_quotes(self):
        """Test daily quotes operations"""
        df = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20230101'],
            'open': [10.0], 'high': [11.0], 'low': [9.0], 'close': [10.5],
            'pre_close': [10.0], 'change': [0.5], 'pct_chg': [5.0],
            'vol': [1000], 'amount': [10000]
        })
        
        await self.cache.save_daily_quotes(df)
        await asyncio.sleep(0.5) # Wait for write # Wait for write
        
        # Test get_daily_quotes
        res = await self.cache.get_daily_quotes(ts_code='000001.SZ')
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]['close'], 10.5)
        
        # Test get_latest_trade_date
        date = await self.cache.get_latest_trade_date()
        self.assertEqual(date, '20230101')
        
        # Test get_cached_trade_dates
        dates = await self.cache.get_cached_trade_dates()
        self.assertIn('20230101', dates)

    def test_daily_quotes(self):
        asyncio.run(self.async_test_daily_quotes())

    async def async_test_daily_indicators(self):
        """Test daily indicators operations"""
        df = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20230101'],
            'pe': [10.0], 'pe_ttm': [9.5], 'pb': [1.2],
            'total_mv': [100000], 'circ_mv': [50000]
        })
        
        await self.cache.save_daily_indicators(df)
        await asyncio.sleep(0.5) # Wait for write
        
        # Test get_cached_indicator_dates
        dates = await self.cache.get_cached_indicator_dates()
        self.assertIn('20230101', dates)
        
        # Test get_latest_indicators
        res = await self.cache.get_latest_indicators('20230101')
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]['pe'], 10.0)

    def test_daily_indicators(self):
        asyncio.run(self.async_test_daily_indicators())

    async def async_test_financial_reports(self):
        """Test financial reports operations"""
        df = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'end_date': ['20230331'],
            'report_type': ['1'],
            'roe': [15.5],
            'total_revenue': [50000]
        })
        
        await self.cache.save_financial_reports(df)
        await asyncio.sleep(0.5) # Wait for write
        
        res = await self.cache.get_latest_financials()
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]['roe'], 15.5)

    def test_financial_reports(self):
        asyncio.run(self.async_test_financial_reports())

    async def async_test_moneyflow_and_northbound(self):
        """Test moneyflow and northbound data"""
        mf_df = pd.DataFrame({'ts_code': ['000001.SZ'], 'trade_date': ['20230101'], 'buy_md_amount': [100]})
        nb_df = pd.DataFrame({'ts_code': ['000001.SZ'], 'trade_date': ['20230101'], 'ratio': [5.5]})
        
        await self.cache.save_moneyflow(mf_df)
        await self.cache.save_northbound(nb_df)
        await asyncio.sleep(0.5) # Wait for write
        
        res_mf = await self.cache.get_moneyflow('20230101')
        self.assertEqual(res_mf.iloc[0]['buy_md_amount'], 100)
        
        res_nb = await self.cache.get_northbound('20230101')
        self.assertEqual(res_nb.iloc[0]['ratio'], 5.5)
        
        res_latest_nb = await self.cache.get_latest_northbound()
        self.assertEqual(len(res_latest_nb), 1)

    def test_moneyflow_northbound(self):
        asyncio.run(self.async_test_moneyflow_and_northbound())

    async def async_test_sync_status(self):
        """Test sync status operations"""
        await self.cache.update_sync_status('test_table', '20230101', 100)
        await asyncio.sleep(0.5) # Wait for write
        
        status = await self.cache.get_sync_status('test_table')
        self.assertEqual(status['record_count'], 100)
        self.assertEqual(status['status'], 'success')
        
        all_status = await self.cache.get_sync_status()
        self.assertFalse(all_status.empty)

    def test_sync_status(self):
        asyncio.run(self.async_test_sync_status())

    async def async_test_get_screening_data(self):
        """Test complex join for screening data"""
        # Prepare related data
        stock_basic = pd.DataFrame({'ts_code': ['000001.SZ'], 'name': ['PA']})
        daily_quotes = pd.DataFrame({
            'ts_code': ['000001.SZ'], 'trade_date': ['20230101'], 
            'close': [10.0], 'pct_chg': [1.0]
        })
        daily_ind = pd.DataFrame({
            'ts_code': ['000001.SZ'], 'trade_date': ['20230101'], 
            'pe_ttm': [8.0]
        })
        fina = pd.DataFrame({
            'ts_code': ['000001.SZ'], 'end_date': ['20221231'], 
            'roe': [12.0]
        })
        
        await self.cache.save_stock_basic(stock_basic)
        await self.cache.save_daily_quotes(daily_quotes)
        await self.cache.save_daily_indicators(daily_ind)
        await self.cache.save_financial_reports(fina)
        await asyncio.sleep(0.5) # Wait for write
        
        # Execute Query
        df = await self.cache.get_screening_data(trade_date='20230101')
        
        self.assertFalse(df.empty)
        row = df.iloc[0]
        self.assertEqual(row['ts_code'], '000001.SZ')
        self.assertEqual(row['close'], 10.0)
        self.assertEqual(row['pe_ttm'], 8.0)
        self.assertEqual(row['roe'], 12.0)

    def test_get_screening_data(self):
        asyncio.run(self.async_test_get_screening_data())

    async def async_test_screening_history(self):
        """Test screening history saving and updating"""
        # Save
        df = pd.DataFrame({
            'ts_code': ['000001.SZ'], 'name': ['PA'], 
            'close': [10.0], 'pct_chg': [1.0]
        })
        await self.cache.save_screening_result(df, 'value', '20230101')
        await asyncio.sleep(0.5) # Wait for write
        
        history = await self.cache.get_screening_history('value')
        self.assertEqual(len(history), 1)
        self.assertEqual(history.iloc[0]['ts_code'], '000001.SZ')
        
        # Update performance
        # Get ID first? Or just update assuming ID=1 (since we just inserted)
        # In real test we can't assume ID. But update_screening_performance takes list of updates with ID.
        # Let's get pending reviews first
        pending = await self.cache.get_pending_reviews()
        self.assertEqual(len(pending), 1)
        record_id = pending[0]['id']
        
        updates = [(11.0, 10.0, 12.0, 20.0, record_id)]
        await self.cache.update_screening_performance(updates)
        await asyncio.sleep(0.5) # Wait for write
        
        # Verify update
        history_updated = await self.cache.get_screening_history('value')
        self.assertEqual(history_updated.iloc[0]['t1_price'], 11.0)

    def test_screening_history(self):
        asyncio.run(self.async_test_screening_history())

    async def async_test_clear_cache(self):
        """Test clearing cache"""
        await self.cache.update_sync_status('test', '20230101', 1)
        await asyncio.sleep(0.5) # Wait for write
        await self.cache.clear_all_cache()
        # clear_all_cache might also be async queue based? No, it executes delete directly?
        # Let's check clear_all_cache implementation if needed. Assuming it waits.
        
        status = await self.cache.get_sync_status('test')
        self.assertIsNone(status)

    def test_clear_cache(self):
        asyncio.run(self.async_test_clear_cache())

    async def async_test_sync_stats_view(self):
        """Test get_sync_stats"""
        # Insert some data
        await self.cache.save_stock_basic(pd.DataFrame({'ts_code': ['000001.SZ'], 'name': ['PA']}))
        await self.cache.save_daily_quotes(pd.DataFrame({'ts_code': ['000001.SZ'], 'trade_date': ['20230101'], 'close': [10]}))
        await asyncio.sleep(0.5) # Wait for write
        
        stats = await self.cache.get_sync_stats()
        self.assertEqual(stats['stock_count'], 1)
        self.assertEqual(stats['quotes_count'], 1)

    def test_sync_stats_view(self):
        asyncio.run(self.async_test_sync_stats_view())

    async def async_test_top_list(self):
        """Test Top List (LHB)"""
        df = pd.DataFrame({'trade_date': ['20230101'], 'ts_code': ['000001.SZ'], 'net_amount': [1000]})
        await self.cache.save_top_list(df)
        await asyncio.sleep(0.5) # Wait for write
        
        res = await self.cache.get_top_list('20230101')
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]['net_amount'], 1000)

    def test_top_list(self):
        asyncio.run(self.async_test_top_list())

    async def async_test_block_trade(self):
        """Test Block Trade"""
        df = pd.DataFrame({'trade_date': ['20230101'], 'ts_code': ['000001.SZ'], 'amount': [500]})
        await self.cache.save_block_trade(df)
        await asyncio.sleep(0.5) # Wait for write
        
        res = await self.cache.get_block_trade('20230101')
        self.assertEqual(len(res), 1)
        self.assertEqual(res.iloc[0]['amount'], 500)

    def test_block_trade(self):
        asyncio.run(self.async_test_block_trade())

    async def async_test_empty_db_returns(self):
        """Test methods returning empty when no data exists"""
        # Ensure DB is clear (tearDown handles it, but just to be sure)
        await self.cache.clear_all_cache() 
        # Also clear stock basic
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM stock_basic")
            await db.commit()
            
        # 1. get_screening_data
        df = await self.cache.get_screening_data()
        self.assertTrue(df.empty)
        
        # 2. get_latest_northbound
        df = await self.cache.get_latest_northbound()
        self.assertTrue(df.empty)
        
        # 3. get_latest_indicators
        df = await self.cache.get_latest_indicators()
        self.assertTrue(df.empty)
        
        # 4. get_latest_trade_date
        date = await self.cache.get_latest_trade_date()
        self.assertIsNone(date)

    def test_empty_db(self):
        asyncio.run(self.async_test_empty_db_returns())

if __name__ == '__main__':
    unittest.main()
