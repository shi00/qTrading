import unittest
import pytest
import asyncio
import pandas as pd
from unittest.mock import MagicMock, patch, AsyncMock
from data.cache_manager import CacheManager
from data.data_processor import DataProcessor

@pytest.mark.asyncio
class TestDataIntegrity:

    async def test_check_financial_coverage(self):
        """Test coverage calculation logic"""
        cm = CacheManager()
        
        # Mock database interactions
        # 1. Cursor
        mock_cursor = AsyncMock()
        mock_cursor.fetchall.side_effect = [
            [('000001.SZ', 'PA Bank'), ('000002.SZ', 'Vanke')], # All stocks
            [('000001.SZ',)] # Covered stocks
        ]
        
        # 2. DB Connection (execute return value must be AsyncCM)
        mock_db = MagicMock()
        mock_execute_cm = AsyncMock()
        mock_execute_cm.__aenter__.return_value = mock_cursor
        mock_db.execute.return_value = mock_execute_cm
        
        # 3. Connect (returns AsyncCM yielding db)
        mock_connect_cm = AsyncMock()
        mock_connect_cm.__aenter__.return_value = mock_db
        
        with patch('aiosqlite.connect', return_value=mock_connect_cm):
            stats, missing = await cm.check_financial_coverage()
            
            assert stats['total'] == 2
            assert stats['covered'] == 1
            assert stats['ratio'] == 0.5
            assert missing == ['000002.SZ']

    async def test_repair_financial_data(self):
        """Test repair logic calls API sequentially"""
        dp = DataProcessor()
        dp.api = MagicMock()
        dp.cache = AsyncMock()
        
        # Mock API return
        mock_df = pd.DataFrame({'ts_code': ['000002.SZ'], 'end_date': ['20231231']})
        dp.api.get_fina_indicator.return_value = mock_df
        
        # Mock cache save return
        dp.cache.save_financial_reports.return_value = 1
        
        # Run repair for 1 stock
        count = await dp.repair_financial_data(['000002.SZ'])
        
        # Determine strict call count: 4 periods * 1 stock = 4 API calls
        # (Assuming current date logic selects 4 periods)
        assert dp.api.get_fina_indicator.call_count == 4
        assert count == 4 # 4 periods successful

if __name__ == '__main__':
    unittest.main()
