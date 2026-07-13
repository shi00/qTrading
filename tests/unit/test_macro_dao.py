import pytest
from unittest.mock import MagicMock, AsyncMock
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.macro_dao import MacroDao

pytestmark = pytest.mark.unit


class TestMacroDaoSaveMacroEconomy:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_macro_economy(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_macro_economy(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=5)
        result = await dao.save_macro_economy(pd.DataFrame({"period": ["202406"]}))
        assert result == 5


class TestMacroDaoSaveShiborDaily:
    @pytest.mark.asyncio
    async def test_none(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_shibor_daily(None)
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        result = await dao.save_shibor_daily(pd.DataFrame())
        assert result == 0

    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._save_upsert = AsyncMock(return_value=3)
        result = await dao.save_shibor_daily(pd.DataFrame({"record_date": ["20240615"]}))
        assert result == 3


class TestMacroDaoGetMacroLatestDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": ["202406"]}))
        result = await dao.get_macro_latest_date()
        assert result == "202406"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": [None]}))
        result = await dao.get_macro_latest_date()
        assert result is None


class TestMacroDaoGetShiborLatestDate:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": ["20240615"]}))
        result = await dao.get_shibor_latest_date()
        assert result == "20240615"

    @pytest.mark.asyncio
    async def test_empty(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db = AsyncMock(return_value=pd.DataFrame({"max_date": [None]}))
        result = await dao.get_shibor_latest_date()
        assert result is None


class TestMacroDaoGetShiborLatest:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240615"], "on_rate": [1.5]}))
        result = await dao.get_shibor_latest()
        assert not result.empty

    @pytest.mark.asyncio
    async def test_none_result(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=None)
        result = await dao.get_shibor_latest()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_error(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_shibor_latest()
        assert isinstance(result, pd.DataFrame)


class TestMacroDaoGetMacroEconomyLatest:
    @pytest.mark.asyncio
    async def test_with_data(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202406"], "m2": [100]}))
        result = await dao.get_macro_economy_latest()
        assert not result.empty

    @pytest.mark.asyncio
    async def test_error(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(side_effect=Exception("DB Error"))
        result = await dao.get_macro_economy_latest()
        assert isinstance(result, pd.DataFrame)

    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202312"], "m2": [90]}))
        result = await dao.get_macro_economy_latest(as_of_date="2024-01-01")
        assert not result.empty
        dao._read_db_select.assert_called_once()
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "publish_date <=" in sql_str

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"period": ["202406"], "m2": [100]}))
        result = await dao.get_macro_economy_latest(as_of_date=None)
        assert not result.empty
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "publish_date <=" not in sql_str


class TestMacroDaoGetShiborLatestWithAsOfDate:
    @pytest.mark.asyncio
    async def test_with_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240101"], "on_rate": [1.5]}))
        result = await dao.get_shibor_latest(as_of_date="2024-01-15")
        assert not result.empty
        dao._read_db_select.assert_called_once()
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "record_date <=" in sql_str

    @pytest.mark.asyncio
    async def test_without_as_of_date(self):
        dao = MacroDao(MagicMock(spec=AsyncEngine))
        dao._read_db_select = AsyncMock(return_value=pd.DataFrame({"record_date": ["20240615"], "on_rate": [1.5]}))
        result = await dao.get_shibor_latest(as_of_date=None)
        assert not result.empty
