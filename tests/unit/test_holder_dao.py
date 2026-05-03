import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd

from data.persistence.daos.holder_dao import HolderDao


def _make_dao():
    dao = HolderDao(MagicMock())
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=None)
    dao._write_db = AsyncMock(return_value=0)
    return dao


class TestSaveHolderNumber:
    @pytest.mark.asyncio
    async def test_save_none(self):
        dao = _make_dao()
        assert await dao.save_holder_number(None) == 0

    @pytest.mark.asyncio
    async def test_save_empty(self):
        dao = _make_dao()
        assert await dao.save_holder_number(pd.DataFrame()) == 0

    @pytest.mark.asyncio
    async def test_save_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240630"], "holder_num": [100]})
        result = await dao.save_holder_number(df)
        assert result == 5
        dao._write_db.assert_called()


class TestCalculateHolderChanges:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        await dao._calculate_holder_changes([])
        dao._write_db.assert_not_called()

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        await dao._calculate_holder_changes(["000001.SZ"])
        dao._write_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._write_db = AsyncMock(side_effect=Exception("db error"))
        await dao._calculate_holder_changes(["000001.SZ"])


class TestSaveTop10Holders:
    @pytest.mark.asyncio
    async def test_save_none(self):
        dao = _make_dao()
        assert await dao.save_top10_holders(None) == 0

    @pytest.mark.asyncio
    async def test_save_empty(self):
        dao = _make_dao()
        assert await dao.save_top10_holders(pd.DataFrame()) == 0

    @pytest.mark.asyncio
    async def test_save_valid(self):
        dao = _make_dao()
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.save_top10_holders(df) == 5


class TestGetTop10Holders:
    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"ts_code": ["000001.SZ"]}))
        result = await dao.get_top10_holders("000001.SZ")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_top10_holders("000001.SZ")
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_top10_holders("000001.SZ")
        assert result.empty


class TestGetStkHoldernumber:
    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "holder_num": [100],
                }
            )
        )
        result = await dao.get_stk_holdernumber("000001.SZ")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_stk_holdernumber("000001.SZ")
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_stk_holdernumber("000001.SZ")
        assert result.empty


class TestGetTop10HoldersBatch:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        result = await dao.get_top10_holders_batch([])
        assert result.empty

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "holder_name": ["股东1"],
                }
            )
        )
        result = await dao.get_top10_holders_batch(["000001.SZ"])
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_top10_holders_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_top10_holders_batch(["000001.SZ"])
        assert result.empty


class TestGetStkHoldernumberBatch:
    @pytest.mark.asyncio
    async def test_empty_codes(self):
        dao = _make_dao()
        result = await dao.get_stk_holdernumber_batch([])
        assert result.empty

    @pytest.mark.asyncio
    async def test_success(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "holder_num": [100],
                    "rn": [1],
                }
            )
        )
        result = await dao.get_stk_holdernumber_batch(["000001.SZ"])
        assert "rn" not in result.columns

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_stk_holdernumber_batch(["000001.SZ"])
        assert result.empty

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_stk_holdernumber_batch(["000001.SZ"])
        assert result.empty


class TestGetExistingTop10TsCodes:
    @pytest.mark.asyncio
    async def test_empty_period(self):
        dao = _make_dao()
        result = await dao.get_existing_top10_ts_codes("")
        assert result == set()

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(return_value=None)
        result = await dao.get_existing_top10_ts_codes("20240630")
        assert result == set()

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                }
            )
        )
        result = await dao.get_existing_top10_ts_codes("20240630")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_exception(self):
        dao = _make_dao()
        dao._read_db = AsyncMock(side_effect=Exception("db error"))
        result = await dao.get_existing_top10_ts_codes("20240630")
        assert result == set()
