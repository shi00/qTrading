import inspect
from unittest.mock import AsyncMock, patch

import pandas as pd

from data.data_processor import DataProcessor
from tests.integration.test_infra_base import TestDatabaseBase


class TestDataIntegrity(TestDatabaseBase):
    """Test data integrity using test_astock database."""

    async def test_repair_financial_data(self):
        """Test repair logic calls API sequentially"""
        dp = DataProcessor()
        dp.api = AsyncMock()
        dp.cache = self.cache

        if hasattr(dp, "context"):
            dp.context.cache = self.cache
            dp.context.api = dp.api

        async def mock_run_async(task_type, func, *args, **kwargs):
            if inspect.iscoroutinefunction(func):
                return await func(*args, **kwargs)
            return func(*args, **kwargs)

        with patch(
            "utils.thread_pool.ThreadPoolManager.run_async",
            side_effect=mock_run_async,
        ):
            mock_df = pd.DataFrame({"ts_code": ["000002.SZ"], "end_date": ["20231231"]})
            dp.api.get_fina_indicator.return_value = mock_df
            dp.api.get_income.return_value = mock_df
            dp.api.get_balancesheet.return_value = mock_df
            dp.api.get_cashflow.return_value = mock_df
            dp.api.get_fina_mainbz.return_value = pd.DataFrame()
            dp.api.get_fina_audit.return_value = pd.DataFrame()
            dp.api.get_pledge_stat.return_value = pd.DataFrame()

            count = await dp.repair_financial_data(["000002.SZ"])

        self.assertEqual(dp.api.get_fina_indicator.call_count, 12)
        self.assertGreaterEqual(count, 0)
