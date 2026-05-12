import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
import datetime

from data.persistence.daos.sync_dao import SyncDao
from data.constants import SYNC_RESULT_EMPTY, SYNC_RESULT_FETCH_FAILED


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
    async def test_with_table_name_returns_dict(self):
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "table_name": ["daily_quotes"],
                    "last_sync_date": ["20240615"],
                    "row_count": [50],
                }
            )
        )
        result = await dao.get_sync_status("daily_quotes")
        assert isinstance(result, dict)
        assert result["table_name"] == "daily_quotes"

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

    @pytest.mark.asyncio
    async def test_update_invalid_type_raises(self):
        dao = SyncDao(MagicMock())
        with pytest.raises(TypeError, match="last_data_date must be str, datetime, or date"):
            await dao.update_sync_status("financial_reports", 12345, 100)

    @pytest.mark.asyncio
    async def test_update_zero_records_sets_empty_status(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.update_sync_status("financial_reports", "20240615", 0, status="success")
        call_args = dao._write_db.call_args[0][1]
        assert call_args[5] == SYNC_RESULT_EMPTY

    @pytest.mark.asyncio
    async def test_update_fetch_failed_status(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.update_sync_status("financial_reports", "20240615", 0, status="fetch_failed")
        call_args = dao._write_db.call_args[0][1]
        assert call_args[5] == SYNC_RESULT_FETCH_FAILED


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

    @pytest.mark.asyncio
    async def test_exception_suppressed(self):
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_completed_step4_stocks(sync_version=1, raise_on_error=False)
        assert result == set()

    @pytest.mark.asyncio
    async def test_exception_raised(self):
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        with pytest.raises(Exception, match="db error"):
            await dao.get_completed_step4_stocks(sync_version=1, raise_on_error=True)


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
