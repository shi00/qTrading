import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
import datetime

from data.persistence.daos.sync_dao import SyncDao


class TestSyncDaoGetSyncStatus:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["financial_reports"],
                    "last_sync_date": ["20240615"],
                    "row_count": [100],
                }
            )
        )
        result = await dao.get_sync_status("financial_reports")
        assert result is not None

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_sync_status("financial_reports")
        assert result is None


class TestSyncDaoUpdateSyncStatus:
    @pytest.mark.asyncio
    async def test_update(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.update_sync_status("financial_reports", "20240615", 100)

    @pytest.mark.asyncio
    async def test_update_with_date_obj(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.update_sync_status("financial_reports", datetime.date(2024, 6, 15), 100)

    @pytest.mark.asyncio
    async def test_update_with_datetime_obj(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.update_sync_status("financial_reports", datetime.datetime(2024, 6, 15, 10, 0), 100)


class TestSyncDaoGetCompletedStep4Stocks:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                }
            )
        )
        result = await dao.get_completed_step4_stocks(sync_version=1)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(return_value=pd.DataFrame())
        result = await dao.get_completed_step4_stocks(sync_version=1)
        assert result == set()


class TestSyncDaoMarkStockStep4Completed:
    @pytest.mark.asyncio
    async def test_save(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.mark_stock_step4_completed("000001.SZ", sync_version=1)


class TestSyncDaoClearStep4SyncStatus:
    @pytest.mark.asyncio
    async def test_clear(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.clear_step4_sync_status()
