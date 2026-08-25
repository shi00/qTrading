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
from ui.viewmodels.watchlist_view_model import (
    StockSearchRow,
    WatchlistRow,
    WatchlistViewModel,
    _df_to_stock_search_rows,
    _df_to_watchlist_rows,
)

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
    cache.search_stocks = AsyncMock(return_value=pd.DataFrame())
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
        assert vm.state.load_error_detail is None


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

    @pytest.mark.asyncio
    async def test_load_watchlist_sets_error_detail_on_exception(self, vm, mock_cache):
        """Task 11.4: 失败时设置 load_error_detail (已脱敏)."""
        mock_cache.get_watchlist.side_effect = RuntimeError("db error at /home/user/secret/path")
        await vm.load_watchlist()
        assert vm.state.load_error_detail is not None
        # 路径应被脱敏 (DataSanitizer 将文件路径替换为 <PATH>)
        assert "/home/user/secret/path" not in vm.state.load_error_detail
        assert "<PATH>" in vm.state.load_error_detail

    @pytest.mark.asyncio
    async def test_load_watchlist_clears_error_detail_on_success(self, vm, mock_cache):
        """Task 11.4: 成功后 load_error_detail=None (即使之前有错误)."""
        # 先制造一次失败
        mock_cache.get_watchlist.side_effect = RuntimeError("first error")
        await vm.load_watchlist()
        assert vm.state.load_error_detail is not None
        # 再成功
        mock_cache.get_watchlist.side_effect = None
        mock_cache.get_watchlist.return_value = _make_watchlist_df()
        await vm.load_watchlist()
        assert vm.state.load_error_detail is None
        assert vm.state.load_error is None


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


# --- _df_to_watchlist_rows (纯函数, 含 None 边界) ---


class TestDfToWatchlistRows:
    """_df_to_watchlist_rows 纯函数测试（含 None / empty / 缺列 边界）。"""

    def test_none_df_returns_empty_tuple(self):
        assert _df_to_watchlist_rows(None) == ()

    def test_empty_df_returns_empty_tuple(self):
        assert _df_to_watchlist_rows(pd.DataFrame()) == ()

    def test_normal_df_returns_rows(self):
        df = _make_watchlist_df()
        rows = _df_to_watchlist_rows(df)
        assert len(rows) == 2
        assert rows[0].ts_code == "000001.SZ"
        assert rows[0].stock_name == "平安银行"
        assert rows[0].note == "测试备注"
        # note=None → "" (L136 三元保护)
        assert rows[1].note == ""

    def test_missing_columns_returns_defaults(self):
        df = pd.DataFrame([{"ts_code": "000001.SZ"}])  # 缺 stock_name/added_at/note
        rows = _df_to_watchlist_rows(df)
        assert len(rows) == 1
        assert rows[0].ts_code == "000001.SZ"
        assert rows[0].stock_name == ""
        assert rows[0].added_at == ""
        assert rows[0].note == ""


# --- search_stocks (issue #433 添加关注搜索) ---


def _make_stock_search_df() -> pd.DataFrame:
    """构造含 2 行 stock_basic 搜索结果 DataFrame。"""
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "name": ["平安银行", "浦发银行"],
        }
    )


class TestSearchStocks:
    @pytest.mark.asyncio
    async def test_search_stocks_calls_cache_and_updates_state(self, vm, mock_cache):
        mock_cache.search_stocks.return_value = _make_stock_search_df()
        await vm.search_stocks("平安")
        mock_cache.search_stocks.assert_awaited_once_with("平安")
        assert len(vm.state.search_results) == 2
        assert vm.state.search_results[0].ts_code == "000001.SZ"
        assert vm.state.search_results[0].name == "平安银行"
        assert vm.state.search_keyword == "平安"
        assert vm.state.is_searching is False
        assert vm.state.search_error is None

    @pytest.mark.asyncio
    async def test_search_stocks_strips_keyword(self, vm, mock_cache):
        await vm.search_stocks("  平安  ")
        mock_cache.search_stocks.assert_awaited_once_with("平安")

    @pytest.mark.asyncio
    async def test_search_stocks_empty_keyword_skips_query(self, vm, mock_cache):
        """空/全空白关键词不发起查询，仅清空结果。"""
        await vm.search_stocks("   ")
        mock_cache.search_stocks.assert_not_awaited()
        assert vm.state.search_results == ()
        assert vm.state.search_keyword == ""
        assert vm.state.is_searching is False

    @pytest.mark.asyncio
    async def test_search_stocks_propagates_cancelled_error(self, vm, mock_cache):
        import asyncio

        mock_cache.search_stocks.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，VM 不得吞没取消信号
            await vm.search_stocks("平安")
        assert vm.state.is_searching is False

    @pytest.mark.asyncio
    async def test_search_stocks_sets_search_error_on_exception(self, vm, mock_cache):
        """搜索失败写入 search_error，不影响列表 load_error（独立错误通道）。"""
        mock_cache.search_stocks.side_effect = RuntimeError("db error")
        await vm.search_stocks("平安")
        assert vm.state.search_error is not None
        assert isinstance(vm.state.search_error, Message)
        assert vm.state.is_searching is False
        assert vm.state.load_error is None


# --- clear_search ---


class TestClearSearch:
    @pytest.mark.asyncio
    async def test_clear_search_resets_search_state(self, vm, mock_cache):
        mock_cache.search_stocks.return_value = _make_stock_search_df()
        await vm.search_stocks("平安")
        assert vm.state.search_results  # 先确认有搜索结果
        await vm.clear_search()
        assert vm.state.search_results == ()
        assert vm.state.search_keyword == ""
        assert vm.state.is_searching is False
        assert vm.state.search_error is None


# --- StockSearchRow ---


class TestStockSearchRow:
    def test_row_is_frozen(self):
        row = StockSearchRow(ts_code="000001.SZ", name="平安银行")
        with pytest.raises(FrozenInstanceError):  # noqa: weak-assertion frozen 契约：赋值即抛错，仅验证不可变性
            row.ts_code = "999999.SZ"  # type: ignore[misc]


# --- _df_to_stock_search_rows (纯函数) ---


class TestDfToStockSearchRows:
    """_df_to_stock_search_rows 纯函数测试（含 None / empty / 缺列 边界）。"""

    def test_none_df_returns_empty_tuple(self):
        assert _df_to_stock_search_rows(None) == ()

    def test_empty_df_returns_empty_tuple(self):
        assert _df_to_stock_search_rows(pd.DataFrame()) == ()

    def test_normal_df_returns_rows(self):
        rows = _df_to_stock_search_rows(_make_stock_search_df())
        assert len(rows) == 2
        assert rows[0].ts_code == "000001.SZ"
        assert rows[0].name == "平安银行"

    def test_missing_columns_returns_defaults(self):
        df = pd.DataFrame([{"ts_code": "000001.SZ"}])  # 缺 name
        rows = _df_to_stock_search_rows(df)
        assert len(rows) == 1
        assert rows[0].ts_code == "000001.SZ"
        assert rows[0].name == ""
