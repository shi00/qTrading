import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from data.persistence import engine_provider
from data.persistence.app_state_service import get_app_state, set_app_state, set_app_state_max
from data.persistence.daos.base_dao import EngineDisposedError

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_engine_provider():
    """R7：隔离 engine_provider 模块级状态（已释放引擎集合）。"""
    engine_provider.reset_engine_provider()
    yield
    engine_provider.reset_engine_provider()


def _mark_disposed(engine):
    """模拟 CacheManager.close()：登记已释放引擎，随后替换为新对象。"""
    engine_provider.set_engine(engine)
    engine_provider.mark_disposed(True)


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
    async def test_handles_exception_gracefully(self, caplog):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("write failed"))
        engine = _make_begin_engine(mock_conn)

        await set_app_state(engine, "key", "value")

        assert any("Failed to write key='key'" in rec.getMessage() for rec in caplog.records)


class TestSetAppStateMax:
    @pytest.mark.asyncio
    async def test_does_nothing_when_engine_is_none(self):
        result = await set_app_state_max(None, "key", "value")
        assert result is None

    @pytest.mark.asyncio
    async def test_upsert_guards_against_overwriting_higher_value(self):
        """R22：on_conflict 更新必须带 where(config_value < value)，
        使较小值不覆盖已存在的较大值，保证高水位单调。"""
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        engine = _make_begin_engine(mock_conn)

        await set_app_state_max(engine, "sync_attempted_upto:moneyflow_daily", "20250910")

        executed_stmt = mock_conn.execute.call_args[0][0]
        compiled = executed_stmt.compile(compile_kwargs={"literal_binds": True})
        sql = str(compiled)
        assert "config_value" in sql
        assert "20250910" in sql
        # DO UPDATE ... WHERE config_value < 新值 —— 仅当现有值更小才覆盖
        assert "config_value <" in sql

    @pytest.mark.asyncio
    async def test_handles_exception_gracefully(self, caplog):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("write failed"))
        engine = _make_begin_engine(mock_conn)

        await set_app_state_max(engine, "key", "value")

        assert any("Failed to write max key='key'" in rec.getMessage() for rec in caplog.records)


class TestDisposedEngineGuard:
    """D05-002 / R5：已释放引擎（登记后换新）不得再被 connect/begin/execute。"""

    @pytest.mark.asyncio
    async def test_get_app_state_zero_touch_on_disposed_engine_and_new_engine_works(self):
        old_engine = _make_connect_engine(MagicMock())
        _mark_disposed(old_engine)

        new_conn = MagicMock()
        new_result = MagicMock()
        new_result.fetchone.return_value = ("20250901",)
        new_conn.execute = AsyncMock(return_value=new_result)
        new_engine = _make_connect_engine(new_conn)
        engine_provider.set_engine(new_engine)

        with pytest.raises(EngineDisposedError, match="Engine disposed, read rejected"):
            await get_app_state(old_engine, "sync_attempted_upto:moneyflow_daily")

        old_engine.connect.assert_not_called()
        assert await get_app_state(new_engine, "sync_attempted_upto:moneyflow_daily") == "20250901"

    @pytest.mark.asyncio
    async def test_set_app_state_zero_touch_on_disposed_engine_and_new_engine_works(self):
        old_engine = _make_begin_engine(MagicMock())
        _mark_disposed(old_engine)

        new_conn = MagicMock()
        new_conn.execute = AsyncMock()
        new_engine = _make_begin_engine(new_conn)
        engine_provider.set_engine(new_engine)

        with pytest.raises(EngineDisposedError, match="Engine disposed, write rejected"):
            await set_app_state(old_engine, "financial:full_sync_pending", "[]", strict=True)

        old_engine.begin.assert_not_called()
        await set_app_state(new_engine, "financial:full_sync_pending", "[]", strict=True)
        new_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_app_state_max_zero_touch_on_disposed_engine_and_new_engine_works(self):
        old_engine = _make_begin_engine(MagicMock())
        _mark_disposed(old_engine)

        new_conn = MagicMock()
        new_conn.execute = AsyncMock()
        new_engine = _make_begin_engine(new_conn)
        engine_provider.set_engine(new_engine)

        with pytest.raises(EngineDisposedError, match="Engine disposed, write-max rejected"):
            await set_app_state_max(old_engine, "financial:disclosure_checkpoint", "20250901", strict=True)

        old_engine.begin.assert_not_called()
        await set_app_state_max(new_engine, "financial:disclosure_checkpoint", "20250901", strict=True)
        new_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_read_and_writes(self):
        read_conn = MagicMock()
        read_conn.execute = AsyncMock(side_effect=asyncio.CancelledError())
        read_engine = _make_connect_engine(read_conn)
        with pytest.raises(asyncio.CancelledError) as read_exc:
            await get_app_state(read_engine, "key")
        assert isinstance(read_exc.value, asyncio.CancelledError)  # R2：必须传播，不得吞没

        write_conn = MagicMock()
        write_conn.execute = AsyncMock(side_effect=asyncio.CancelledError())
        write_engine = _make_begin_engine(write_conn)
        with pytest.raises(asyncio.CancelledError) as write_exc:
            await set_app_state(write_engine, "key", "value")
        assert isinstance(write_exc.value, asyncio.CancelledError)
        with pytest.raises(asyncio.CancelledError) as max_exc:
            await set_app_state_max(write_engine, "key", "value", strict=True)
        assert isinstance(max_exc.value, asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_engine_disposed_error_from_operation_is_not_downgraded(self):
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=EngineDisposedError("disposed mid-write"))
        engine = _make_begin_engine(mock_conn)

        with pytest.raises(EngineDisposedError, match="disposed mid-write"):
            await set_app_state(engine, "key", "value")
        with pytest.raises(EngineDisposedError, match="disposed mid-write"):
            await set_app_state_max(engine, "key", "value")

    @pytest.mark.asyncio
    async def test_strict_write_failure_does_not_advance_checkpoint(self):
        """关键断点：写失败必须传播，后续「推进完成」语句不得执行。"""
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("db down"))
        engine = _make_begin_engine(mock_conn)
        advanced = False

        with pytest.raises(RuntimeError, match="db down"):
            await set_app_state_max(engine, "financial:disclosure_checkpoint", "20250901", strict=True)
            advanced = True
        with pytest.raises(RuntimeError, match="db down"):
            await set_app_state(engine, "financial:full_sync_pending", "[]", strict=True)
            advanced = True

        assert advanced is False
