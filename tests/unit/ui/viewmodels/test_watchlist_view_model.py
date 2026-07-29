"""WatchlistViewModel 单元测试 (FR-UX-004, Task 4.2).

测试 VM state/commands，不依赖 Flet 渲染。
覆盖:
1. State frozen + 默认值
2. load_watchlist() 调用 cache.get_watchlist 并更新 state
3. add_to_watchlist() 调用 cache.add_to_watchlist 并刷新
4. remove_from_watchlist() 调用 cache.remove_from_watchlist 并刷新
5. is_in_watchlist() 返回 bool
6. 错误处理: CancelledError 传播 (R2) / 普通异常转为 Message
7. VM 只产出 Message (i18n key)，不调 I18n.get
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from ui.viewmodels import Message
from ui.viewmodels.watchlist_view_model import WatchlistRow, WatchlistViewModel

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def mock_cache():
    """Mock CacheManager（避免单例污染）。"""
    cache = MagicMock()
    cache.get_watchlist = AsyncMock(return_value=pd.DataFrame())
    cache.add_to_watchlist = AsyncMock(return_value=1)
    cache.remove_from_watchlist = AsyncMock(return_value=1)
    cache.is_in_watchlist = AsyncMock(return_value=False)
    return cache


@pytest.fixture
def vm(mock_cache):
    return WatchlistViewModel(cache=mock_cache)


def _make_watchlist_df() -> pd.DataFrame:
    """构造一个包含 2 行的 watchlist DataFrame。"""
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "stock_name": ["平安银行", "浦发银行"],
            "added_at": ["2026-07-29 10:00:00", "2026-07-28 09:00:00"],
            "note": ["测试备注", None],
        }
    )


# --- State immutability ---


class TestStateImmutability:
    def test_state_is_frozen(self, vm):
        with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
            vm.state.is_loading = True  # type: ignore[misc]

    def test_default_state(self, vm):
        assert vm.state.watchlist_rows == ()
        assert vm.state.is_loading is False
        assert vm.state.load_error is None


# --- load_watchlist ---


class TestLoadWatchlist:
    @pytest.mark.asyncio
    async def test_load_watchlist_updates_state(self, vm, mock_cache):
        mock_cache.get_watchlist.return_value = _make_watchlist_df()
        await vm.load_watchlist()
        assert len(vm.state.watchlist_rows) == 2
        row0 = vm.state.watchlist_rows[0]
        assert row0.ts_code == "000001.SZ"
        assert row0.stock_name == "平安银行"
        assert row0.note == "测试备注"
        assert vm.state.is_loading is False
        assert vm.state.load_error is None

    @pytest.mark.asyncio
    async def test_load_watchlist_empty_df(self, vm, mock_cache):
        mock_cache.get_watchlist.return_value = pd.DataFrame()
        await vm.load_watchlist()
        assert vm.state.watchlist_rows == ()
        assert vm.state.is_loading is False

    @pytest.mark.asyncio
    async def test_load_watchlist_sets_loading_during_fetch(self, vm, mock_cache):
        """加载中 is_loading=True，完成后 False。"""
        states_during: list[bool] = []

        async def _capture_loading(*args, **kwargs):
            states_during.append(vm.state.is_loading)
            return _make_watchlist_df()

        mock_cache.get_watchlist.side_effect = _capture_loading
        await vm.load_watchlist()
        assert states_during == [True]
        assert vm.state.is_loading is False

    @pytest.mark.asyncio
    async def test_load_watchlist_propagates_cancelled_error(self, vm, mock_cache):
        import asyncio

        mock_cache.get_watchlist.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，VM 不得吞没取消信号
            await vm.load_watchlist()
        assert vm.state.is_loading is False

    @pytest.mark.asyncio
    async def test_load_watchlist_sets_error_message_on_exception(self, vm, mock_cache):
        mock_cache.get_watchlist.side_effect = RuntimeError("db error")
        await vm.load_watchlist()
        assert vm.state.load_error is not None
        assert isinstance(vm.state.load_error, Message)
        assert vm.state.is_loading is False


# --- add_to_watchlist ---


class TestAddToWatchlist:
    @pytest.mark.asyncio
    async def test_add_to_watchlist_calls_cache_and_refreshes(self, vm, mock_cache):
        mock_cache.get_watchlist.return_value = _make_watchlist_df()
        await vm.add_to_watchlist("000001.SZ", "平安银行", note="备注")
        mock_cache.add_to_watchlist.assert_awaited_once_with("000001.SZ", "平安银行", "备注")
        # add 后应刷新 watchlist
        mock_cache.get_watchlist.assert_awaited()

    @pytest.mark.asyncio
    async def test_add_to_watchlist_without_note(self, vm, mock_cache):
        await vm.add_to_watchlist("600000.SH", "浦发银行")
        mock_cache.add_to_watchlist.assert_awaited_once_with("600000.SH", "浦发银行", None)

    @pytest.mark.asyncio
    async def test_add_to_watchlist_propagates_cancelled_error(self, vm, mock_cache):
        import asyncio

        mock_cache.add_to_watchlist.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，VM 不得吞没取消信号
            await vm.add_to_watchlist("000001.SZ", "平安银行")


# --- remove_from_watchlist ---


class TestRemoveFromWatchlist:
    @pytest.mark.asyncio
    async def test_remove_calls_cache_and_refreshes(self, vm, mock_cache):
        mock_cache.get_watchlist.return_value = pd.DataFrame()
        await vm.remove_from_watchlist("000001.SZ")
        mock_cache.remove_from_watchlist.assert_awaited_once_with("000001.SZ")
        mock_cache.get_watchlist.assert_awaited()

    @pytest.mark.asyncio
    async def test_remove_propagates_cancelled_error(self, vm, mock_cache):
        import asyncio

        mock_cache.remove_from_watchlist.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，VM 不得吞没取消信号
            await vm.remove_from_watchlist("000001.SZ")


# --- is_in_watchlist ---


class TestIsInWatchlist:
    @pytest.mark.asyncio
    async def test_is_in_watchlist_returns_bool(self, vm, mock_cache):
        mock_cache.is_in_watchlist.return_value = True
        result = await vm.is_in_watchlist("000001.SZ")
        assert result is True
        mock_cache.is_in_watchlist.assert_awaited_once_with("000001.SZ")

    @pytest.mark.asyncio
    async def test_is_in_watchlist_false(self, vm, mock_cache):
        mock_cache.is_in_watchlist.return_value = False
        result = await vm.is_in_watchlist("999999.SZ")
        assert result is False


# --- WatchlistRow ---


class TestWatchlistRow:
    def test_row_is_frozen(self):
        row = WatchlistRow(ts_code="000001.SZ", stock_name="平安银行", added_at="2026-07-29", note="")
        with pytest.raises(FrozenInstanceError):  # noqa: weak-assertion frozen 契约：赋值即抛错，仅验证不可变性
            row.ts_code = "999999.SZ"  # type: ignore[misc]
