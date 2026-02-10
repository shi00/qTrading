import unittest
import pytest
import asyncio
import pandas as pd
from unittest.mock import MagicMock, patch, AsyncMock
from data.cache_manager import CacheManager
from data.data_processor import DataProcessor
import pytest_asyncio
from data.data_dictionary import TABLE_DEFINITIONS

# Redefining event_loop is often not needed with newer pytest-asyncio,
# but if we do, we must match scope.
# However, let's just use the default function scope for robustness.

@pytest_asyncio.fixture(scope="function")
async def db():
    # Setup
    cm = CacheManager()
    await cm.init_db()
    yield cm
    # Teardown
    await cm.close()

@pytest.mark.asyncio
class TestDataIntegrity:

    async def test_repair_financial_data(self):
        """Test repair logic calls API sequentially"""
        dp = DataProcessor()
        dp.api = MagicMock()
        dp.cache = AsyncMock()
        
        # Critical: Update context because strategies hold a reference to it
        if hasattr(dp, 'context'):
             dp.context.cache = dp.cache
             dp.context.api = dp.api
        
        
        # Mock ThreadPoolManager to execute immediately
        async def mock_run_async(task_type, func, *args, **kwargs):
            if asyncio.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)
            
        with patch('utils.thread_pool.ThreadPoolManager.run_async', side_effect=mock_run_async):
            # Mock API return
            mock_df = pd.DataFrame({'ts_code': ['000002.SZ'], 'end_date': ['20231231']})
            # repair_financial_data calls _fetch_comprehensive_financial_data
            # which calls get_income, get_balancesheet, get_fina_indicator, etc.
            # We need to mock all of them or at least get_fina_indicator which is asserted.
            dp.api.get_fina_indicator.return_value = mock_df
            dp.api.get_income.return_value = mock_df
            dp.api.get_balancesheet.return_value = mock_df
            # Mock aux tables
            dp.api.get_fina_mainbz.return_value = pd.DataFrame()
            dp.api.get_fina_audit.return_value = pd.DataFrame() 
            dp.api.get_pledge_stat.return_value = pd.DataFrame()
            
            # Mock cache save return
            dp.cache.save_financial_reports.return_value = 1
            
            # Run repair for 1 stock
            count = await dp.repair_financial_data(['000002.SZ'])
        
        # Determine strict call count: 12 periods * 1 stock = 12 API calls
        assert dp.api.get_fina_indicator.call_count == 12
        assert count == 12 # 12 periods successful

if __name__ == '__main__':
    unittest.main()
