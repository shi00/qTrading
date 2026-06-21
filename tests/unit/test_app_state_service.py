import pytest
from unittest.mock import AsyncMock, MagicMock

from data.persistence.app_state_service import get_app_state, set_app_state

pytestmark = pytest.mark.unit


def _make_connect_engine(mock_conn):
    engine = MagicMock()
    engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    return engine


def _make_begin_engine(mock_conn):
    engine = MagicMock()
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=False)
    return engine


class TestGetAppState:
    @pytest.mark.asyncio
    async def test_returns_none_when_engine_is_none(self):
        result = await get_app_state(None, "some_key")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_value_when_row_exists(self):
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("hello",)
        mock_conn.execute = AsyncMock(return_value=mock_result)
        engine = _make_connect_engine(mock_conn)

        result = await get_app_state(engine, "greeting")

        assert result == "hello"
        mock_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_row_does_not_exist(self):
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_conn.execute = AsyncMock(return_value=mock_result)
        engine = _make_connect_engine(mock_conn)

        result = await get_app_state(engine, "missing_key")

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
        engine = _make_connect_engine(mock_conn)

        result = await get_app_state(engine, "any_key")

        assert result is None


class TestSetAppState:
    @pytest.mark.asyncio
    async def test_does_nothing_when_engine_is_none(self):
        result = await set_app_state(None, "key", "value")
        assert result is None

    @pytest.mark.asyncio
    async def test_calls_begin_and_executes_upsert(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)

        await set_app_state(engine, "theme", "dark")

        engine.begin.assert_called_once()
        mock_conn.execute.assert_awaited_once()
        executed_stmt = mock_conn.execute.call_args[0][0]
        assert executed_stmt is not None

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("write failed"))
        engine = _make_begin_engine(mock_conn)

        await set_app_state(engine, "key", "value")
