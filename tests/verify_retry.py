
import asyncio
import logging
import sys
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Add parent path to sys.path
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Mock ConfigHandler before importing DataProcessor
sys.modules['utils.config_handler'] = MagicMock()
from utils.config_handler import ConfigHandler
ConfigHandler.get_token.return_value = "mock_token"

from data.data_processor import DataProcessor

async def test_retry_success():
    print("\n=== Test 1: Retry Success Logic ===")
    
    # 1. Setup Mock DataProcessor
    dp = DataProcessor()
    dp.cache = AsyncMock()
    dp.cache.get_cached_trade_dates.return_value = set()
    dp.cache.get_cached_indicator_dates.return_value = set()
    
    # Mock Trade Dates
    dp.get_trade_dates = MagicMock(return_value=['20230101', '20230102', '20230103', '20230104', '20230105'])
    
    # Mock API call: 
    # Logic: 
    # - '20230101': Success
    # - '20230102': Fail once, Success on retry
    # - '20230103': Fail once, Fail twice, Success on 2nd retry
    # - others: Success
    
    fail_counts = {
        '20230102': 1,
        '20230103': 2
    }
    
    async def mock_sync(trade_date):
        if trade_date in fail_counts and fail_counts[trade_date] > 0:
            print(f"Mock Failure for {trade_date}")
            fail_counts[trade_date] -= 1
            raise Exception("Mock Network Error")
        print(f"Mock Success for {trade_date}")
        return 1
        
    dp.sync_daily_market_snapshot = AsyncMock(side_effect=mock_sync)
    
    # 2. Run Sync
    total = await dp.sync_historical_data(days=5)
    
    # 3. Assertions
    print(f"Total synced: {total}")
    # We should have synced 5 days (all eventually succeeded)
    assert total == 5
    print("Test 1 Passed: All items eventually succeeded.")

async def test_circuit_breaker():
    print("\n=== Test 2: Circuit Breaker Logic ===")
    
    dp = DataProcessor()
    dp.cache = AsyncMock()
    # Mock many dates
    dates = [f"202301{i:02d}" for i in range(1, 50)] # 49 days
    dp.get_trade_dates = MagicMock(return_value=dates)
    dp.cache.get_cached_trade_dates.return_value = set()
    dp.cache.get_cached_indicator_dates.return_value = set()
    
    # Mock Fail ALL
    dp.sync_daily_market_snapshot = AsyncMock(side_effect=Exception("Permanent Error"))
    
    # Run Sync
    total = await dp.sync_historical_data(days=50)
    
    # Expect:
    # CB_THRESHOLD is max(20, 49*0.1) = 20.
    # Should stop after ~20-25 failures (depends on concurrency race)
    
    print(f"Total synced (should be small due to abort): {total}")
    
    # Verify we didn't try ALL 49
    # The 'total' return value in abort case is (len(trade_dates) - len(failed_dates))
    # Since all tried failed, failed_dates should be around 20-25.
    # total return should be remaining.
    
    # But checking internal failed_dates via logging or side effect count
    call_count = dp.sync_daily_market_snapshot.call_count
    print(f"API Attempt Count: {call_count}")
    
    # Ensure we stopped early (much less than 49)
    assert call_count < 40 
    assert call_count >= 20
    print("Test 2 Passed: Circuit Breaker stopped early.")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(test_retry_success())
    loop.run_until_complete(test_circuit_breaker())
