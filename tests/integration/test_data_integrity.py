import inspect
from unittest.mock import AsyncMock, patch

import pandas as pd

from data.data_processor import DataProcessor
from tests.integration.test_infra_base import TestDatabaseBase
import pytest
from sqlalchemy import text


pytestmark = pytest.mark.integration


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

        try:
            self.assertEqual(dp.api.get_fina_indicator.call_count, 12)
            self.assertGreaterEqual(count, 0)
        finally:
            # 清理 repair 写入的 financial_reports（000002.SZ）：mock 数据缺 ann_date 列，
            # repair 会写入 ann_date NULL 行，而 TestDatabaseBase.asyncTearDown 不清理数据，
            # 残留会污染同 worker 后续测试（如 DAT-06 test_has_ann_date_nulls_false_on_clean_mvd
            # 断言 has_ann_date_nulls() is False）。无论断言成败都恢复共享测试库干净状态。
            async with self.engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM financial_reports WHERE ts_code = :ts_code"),
                    {"ts_code": "000002.SZ"},
                )
