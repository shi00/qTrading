import asyncio
from unittest.mock import MagicMock, patch

import pytest

from data.persistence.daos.stock_dao import StockDao


class _CancelledContextManager:
    """Async context manager that raises CancelledError on __aenter__."""

    async def __aenter__(self):
        raise asyncio.CancelledError()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.mark.asyncio
class TestBaseDaoCancelledError:
    """Test that _read_db correctly propagates CancelledError instead of swallowing it."""

    async def test_read_db_propagates_cancelled_error(self):
        """_read_db should re-raise CancelledError, not return empty DataFrame."""
        dao = StockDao.__new__(StockDao)

        evt = asyncio.Event()
        evt.set()

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=_CancelledContextManager())
        dao.engine = mock_engine

        with patch.object(StockDao, "_get_maintenance_event", return_value=evt):
            with pytest.raises(asyncio.CancelledError):
                await dao._read_db("SELECT 1")

    async def test_write_db_propagates_cancelled_error(self):
        """_write_db should re-raise CancelledError, not swallow it."""
        dao = StockDao.__new__(StockDao)

        evt = asyncio.Event()
        evt.set()

        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=_CancelledContextManager())
        dao.engine = mock_engine

        with patch.object(StockDao, "_get_maintenance_event", return_value=evt):
            with pytest.raises(asyncio.CancelledError):
                await dao._write_db("INSERT INTO t VALUES (1)")
