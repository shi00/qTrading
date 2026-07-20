# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
import datetime

from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.sync_dao import SyncDao
from data.constants import SYNC_RESULT_EMPTY, SYNC_RESULT_FETCH_FAILED

pytestmark = pytest.mark.unit


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
        dao._save_upsert = AsyncMock(return_value=1)
        await dao.mark_stock_step4_completed("000001.SZ", sync_version=1)
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        df = call_args[0][0]
        assert df.iloc[0]["ts_code"] == "000001.SZ"
        assert df.iloc[0]["sync_version"] == 1
        assert call_args[1]["pk_columns"] == ["ts_code"]

    @pytest.mark.asyncio
    async def test_save_with_conn(self):
        dao = SyncDao(MagicMock())
        dao._save_upsert = AsyncMock(return_value=1)
        mock_conn = MagicMock()
        await dao.mark_stock_step4_completed("000001.SZ", sync_version=1, conn=mock_conn)
        assert dao._save_upsert.call_args[1]["conn"] is mock_conn


class TestSyncDaoClearStep4SyncStatus:
    @pytest.mark.asyncio
    async def test_clear(self):
        dao = SyncDao(MagicMock())
        dao._write_db = AsyncMock(return_value=1)
        await dao.clear_step4_sync_status()


class TestSyncDaoEngineDisposedErrorPropagation:
    """R5: EngineDisposedError 必须原样传播，不论 raise_on_error 取值。"""

    @pytest.mark.asyncio
    async def test_get_completed_step4_stocks_propagates_when_raise_on_error_false(self):
        """raise_on_error=False 时 EngineDisposedError 仍必须传播（不可降级为 set()）。"""
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.get_completed_step4_stocks(sync_version=1, raise_on_error=False)

    @pytest.mark.asyncio
    async def test_get_completed_step4_stocks_propagates_when_raise_on_error_true(self):
        """raise_on_error=True 时 EngineDisposedError 也必须传播。"""
        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))
        with pytest.raises(EngineDisposedError):
            await dao.get_completed_step4_stocks(sync_version=1, raise_on_error=True)

    @pytest.mark.asyncio
    async def test_get_completed_step4_stocks_database_query_error_still_degrades(self):
        """普通 Exception 在 raise_on_error=False 时仍降级为 set()（不破坏原行为）。"""
        from data.persistence.daos.base_dao import DatabaseQueryError

        dao = SyncDao(MagicMock())
        dao._read_db = AsyncMock(side_effect=DatabaseQueryError("db error"))
        result = await dao.get_completed_step4_stocks(sync_version=1, raise_on_error=False)
        assert result == set()
