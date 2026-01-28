import asyncio
import sys
import os
import pandas as pd
import logging
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.data_processor import DataProcessor
from data.cache_manager import CacheManager
from config import DB_QUEUE_SIZE

logging.basicConfig(level=logging.INFO, format='%(name)s - %(message)s')
logger = logging.getLogger("VerifyResume")

async def verify_resume_logic():
    test_db_path = "test_resume.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    logger.info(f"Setting up test DB: {test_db_path}")
    
    # 1. Setup minimal processor with mocked API
    processor = DataProcessor()
    # Replace cache with test DB cache
    processor.cache = CacheManager(db_path=test_db_path)
    await processor.cache.init_db()
    
    # Mock Tushare API to perform controlled "downloads"
    processor.api = MagicMock()
    
    # Mock get_trade_dates to return 5 days
    dates = ['20230101', '20230102', '20230103', '20230104', '20230105']
    processor.get_trade_dates = MagicMock(return_value=dates)
    
    # Mock data fetching
    async def mock_get_daily_quotes(trade_date):
        # Return dummy data
        df = pd.DataFrame([{
            'ts_code': '000001.SZ', 'trade_date': trade_date, 
            'open': 10, 'high': 10, 'low': 10, 'close': 10, 
            'pre_close': 10, 'change': 0, 'pct_chg': 0, 'vol': 100, 'amount': 1000
        }])
        return df

    processor.api.get_daily_quotes = mock_get_daily_quotes 
    # API methods are called in executor, so we need slightly more complex mocking if we want to spy on them effectively
    # But for this test, replacing the sync implementation in DataProcessor.sync_daily_market_snapshot 
    # might be easier to control behavior?
    # Actually, we can just let it run.
    
    # But wait, DataProcessor methods run `loop.run_in_executor(None, lambda: self.api.get_daily_quotes(...))`
    # So `self.api` needs to be a real object or a mock that is picklable/threadsafe? 
    # MagicMock works in threads usually.
    
    processor.api.get_daily_quotes = MagicMock(side_effect=lambda trade_date=None: pd.DataFrame([{
            'ts_code': '000001.SZ', 'trade_date': trade_date, 
            'open': 10, 'high': 10, 'low': 10, 'close': 10, 
            'pre_close': 10, 'change': 0, 'pct_chg': 0, 'vol': 100, 'amount': 1000
        }]))
    processor.api.get_daily_basic = MagicMock(side_effect=lambda trade_date=None: pd.DataFrame([{
            'ts_code': '000001.SZ', 'trade_date': trade_date, 
            'pe': 10, 'pb': 1
        }]))
    
    # 2. Simulate "Partial Sync" (Crash)
    # We manually write just days 1, 2, 4. Day 3 and 5 are "missing" (crashed/lost).
    logger.info("Simulating crash state: Days 20230101, 20230102, 20230104 exist.")
    
    # Manually save these
    for date in ['20230101', '20230102', '20230104']:
        df = processor.api.get_daily_quotes(date)
        await processor.cache.save_daily_quotes(df)
        
    await processor.cache.queue.join() # Wait for "crash" data to persist
    
    # 3. Run Historical Sync
    # This should detect that 3 and 5 are missing and fetch them.
    logger.info("Running sync_historical_data (should fetch 03 and 05)...")
    
    # We mock sync_daily_market_snapshot to track calls, 
    # but since it's an async method on the processor, we can spy on it.
    original_sync = processor.sync_daily_market_snapshot
    call_log = []
    
    async def spy_sync(trade_date=None):
        call_log.append(trade_date)
        return await original_sync(trade_date)
        
    processor.sync_daily_market_snapshot = spy_sync
    
    # Run sync (asking for last 5 days essentially)
    await processor.sync_historical_data(days=5)
    
    # 4. Verify what was fetched
    logger.info(f"Sync called for dates: {sorted(call_log)}")
    
    missing_fetched = '20230103' in call_log and '20230105' in call_log
    existing_skipped = '20230101' not in call_log and '20230102' not in call_log and '20230104' not in call_log
    
    if missing_fetched and existing_skipped:
        logger.info("Breakpoint Resume Validated: Only missing days were synced.")
    else:
        logger.error(f"Failed: Fetched {call_log}. Expected ['20230103', '20230105']")

    # Cleanup
    await processor.close()
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except:
            pass

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(verify_resume_logic())
