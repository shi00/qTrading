import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from data.persistence.daos.stock_dao import StockDao


@pytest.mark.asyncio
class TestBaseDaoCancelledError:
    """Test that _read_db correctly propagates CancelledError instead of swallowing it."""

    async def test_read_db_propagates_cancelled_error(self):
        """_read_db should re-raise CancelledError, not return empty DataFrame."""
        dao = StockDao.__new__(StockDao)

        evt = asyncio.Event()
        evt.set()

        @asynccontextmanager
        async def mock_connect():
            raise asyncio.CancelledError()
            yield

        mock_engine = AsyncMock()
        mock_engine.connect = mock_connect

        with patch.object(dao, "_get_maintenance_event", return_value=evt):
            dao.engine = mock_engine

            with pytest.raises(asyncio.CancelledError):
                await dao._read_db("SELECT 1")

    async def test_write_db_propagates_cancelled_error(self):
        """_write_db should re-raise CancelledError, not swallow it."""
        dao = StockDao.__new__(StockDao)

        evt = asyncio.Event()
        evt.set()

        @asynccontextmanager
        async def mock_begin():
            raise asyncio.CancelledError()
            yield

        mock_engine = AsyncMock()
        mock_engine.begin = mock_begin

        with patch.object(dao, "_get_maintenance_event", return_value=evt):
            dao.engine = mock_engine

            with pytest.raises(asyncio.CancelledError):
                await dao._write_db("INSERT INTO t VALUES (1)")
