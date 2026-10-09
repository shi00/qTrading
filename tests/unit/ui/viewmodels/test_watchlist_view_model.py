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

import asyncio
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from services.stock_detail_service import StockQuote
from ui.viewmodels import Message
from ui.viewmodels.watchlist_view_model import (
    StockSearchRow,
    WatchlistMutationResult,
    WatchlistRow,
    WatchlistViewModel,
    _df_to_stock_search_rows,
    _df_to_watchlist_rows,
    _merge_quotes,
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
    # UX-09 MAJOR-04：StockDetailService 经 CacheManager 代理批量取行情/详情。
    cache.get_latest_quotes_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_latest_ai_reviews_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_latest_indicators_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_latest_financials_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_stock_basic_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_recent_quotes = AsyncMock(return_value=pd.DataFrame())
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


# --- _merge_quotes (UX-09 MAJOR-04 纯函数) ---


class TestMergeQuotes:
    """_merge_quotes 把行情快照合并进关注行，缺失保持 None（R21）。"""

    def test_merge_sets_quote_fields(self):
        rows = (WatchlistRow(ts_code="000001.SZ", stock_name="平安银行"),)
        quotes = {"000001.SZ": StockQuote(latest_close=12.34, pct_chg=1.5, ai_score=85.0)}
        merged = _merge_quotes(rows, quotes)
        assert merged[0].latest_close == 12.34
        assert merged[0].pct_chg == 1.5
        assert merged[0].ai_score == 85.0

    def test_merge_missing_quote_keeps_none(self):
        rows = (WatchlistRow(ts_code="999999.SZ", stock_name="无行情"),)
        merged = _merge_quotes(rows, {})
        assert merged[0].latest_close is None
        assert merged[0].pct_chg is None
        assert merged[0].ai_score is None

    def test_merge_partial_quote_keeps_missing_none(self):
        """有行情价但无 AI 评分时，ai_score 仍为 None（不得填 0）。"""
        rows = (WatchlistRow(ts_code="000001.SZ", stock_name="平安银行"),)
        quotes = {"000001.SZ": StockQuote(latest_close=12.34, pct_chg=-0.8, ai_score=None)}
        merged = _merge_quotes(rows, quotes)
        assert merged[0].latest_close == 12.34
        assert merged[0].pct_chg == -0.8
        assert merged[0].ai_score is None

    def test_merge_preserves_row_order_and_other_fields(self):
        rows = (
            WatchlistRow(ts_code="000001.SZ", stock_name="平安银行", added_at="2026-07-29", note="A"),
            WatchlistRow(ts_code="600000.SH", stock_name="浦发银行", added_at="2026-07-28", note="B"),
        )
        merged = _merge_quotes(rows, {"600000.SH": StockQuote(latest_close=9.9)})
        assert [r.ts_code for r in merged] == ["000001.SZ", "600000.SH"]
        assert merged[0].note == "A"
        assert merged[1].note == "B"
        assert merged[1].latest_close == 9.9

    def test_merge_empty_rows_returns_empty(self):
        assert _merge_quotes((), {"000001.SZ": StockQuote(latest_close=1.0)}) == ()


# --- load_watchlist 行情合并（UX-09 MAJOR-04） ---


class TestLoadWatchlistQuotes:
    @pytest.mark.asyncio
    async def test_load_watchlist_merges_quotes(self, vm, mock_cache):
        mock_cache.get_watchlist.return_value = _make_watchlist_df()
        mock_cache.get_latest_quotes_bulk.return_value = pd.DataFrame(
            [{"ts_code": "000001.SZ", "close": 12.34, "pct_chg": 1.5}]
        )
        mock_cache.get_latest_ai_reviews_bulk.return_value = pd.DataFrame([{"ts_code": "000001.SZ", "ai_score": 85}])
        await vm.load_watchlist()
        row0 = vm.state.watchlist_rows[0]
        assert row0.latest_close == 12.34
        assert row0.pct_chg == 1.5
        assert row0.ai_score == 85.0
        # 第二行无行情 → 全部 None（R21，不得填 0）
        row1 = vm.state.watchlist_rows[1]
        assert row1.latest_close is None
        assert row1.pct_chg is None
        assert row1.ai_score is None
        mock_cache.get_latest_quotes_bulk.assert_awaited_once_with(["000001.SZ", "600000.SH"])

    @pytest.mark.asyncio
    async def test_load_watchlist_quote_failure_degrades_not_breaks(self, vm, mock_cache):
        """行情查询失败 → 列表仍加载，行情列降级为 None（显示「—」）。"""
        mock_cache.get_watchlist.return_value = _make_watchlist_df()
        mock_cache.get_latest_quotes_bulk.side_effect = RuntimeError("db error")
        await vm.load_watchlist()
        assert len(vm.state.watchlist_rows) == 2
        assert vm.state.watchlist_rows[0].latest_close is None
        assert vm.state.load_error is None
        assert vm.state.is_loading is False


# --- open_stock_detail / close_stock_detail (UX-09 MAJOR-04) ---


def _make_detail_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "close": 12.34,
                "pct_chg": 1.5,
                "pe_ttm": 8.1,
                "roe": 10.2,
                "name": "平安银行",
                "industry": "银行",
                "ai_score": 85,
            }
        ]
    )


class TestOpenStockDetail:
    @pytest.mark.asyncio
    async def test_open_stock_detail_sets_detail_state(self, vm, mock_cache):
        mock_cache.get_latest_quotes_bulk.return_value = _make_detail_df()
        mock_cache.get_stock_basic_bulk.return_value = _make_detail_df()
        result = await vm.open_stock_detail("000001.SZ")
        assert result is True
        assert vm.state.detail_stock_data is not None
        assert vm.state.detail_stock_data["ts_code"] == "000001.SZ"
        assert vm.state.detail_stock_data["name"] == "平安银行"

    @pytest.mark.asyncio
    async def test_open_stock_detail_does_not_require_watchlist_row_but_falls_back_name(self, vm, mock_cache):
        """未在关注行内也能打开（不依赖列表），name 缺失时回退空（服务层回退 ts_code）。"""
        result = await vm.open_stock_detail("600519.SH")
        assert result is True
        assert vm.state.detail_stock_data is not None
        assert vm.state.detail_stock_data["ts_code"] == "600519.SH"
        # 无 basic 数据且无自选行 → name 回退为 ts_code（服务层 fallback_name or code）
        assert vm.state.detail_stock_data["name"] == "600519.SH"

    @pytest.mark.asyncio
    async def test_open_stock_detail_empty_code_returns_false(self, vm, mock_cache):
        assert await vm.open_stock_detail("") is False
        assert await vm.open_stock_detail("   ") is False
        assert vm.state.detail_stock_data is None

    @pytest.mark.asyncio
    async def test_open_stock_detail_service_failure_returns_false(self, vm, mock_cache):
        mock_cache.get_latest_quotes_bulk.side_effect = RuntimeError("db error")
        result = await vm.open_stock_detail("000001.SZ")
        assert result is False
        assert vm.state.detail_stock_data is None
        # 不得污染列表错误通道
        assert vm.state.load_error is None

    @pytest.mark.asyncio
    async def test_open_stock_detail_propagates_cancelled_error(self, vm, mock_cache):
        import asyncio

        mock_cache.get_latest_quotes_bulk.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，VM 不得吞没取消信号
            await vm.open_stock_detail("000001.SZ")


class TestCloseStockDetail:
    def test_close_clears_detail_state(self, mock_cache):
        vm = WatchlistViewModel(cache=mock_cache)
        assert vm.state.detail_stock_data is None
        vm._set_state(detail_stock_data={"ts_code": "000001.SZ"})
        vm.close_stock_detail()
        assert vm.state.detail_stock_data is None

    def test_default_detail_state_is_none(self, vm):
        assert vm.state.detail_stock_data is None


# --- F01: load_watchlist 返回值语义（成功/空表=True，异常=False） ---


class TestLoadWatchlistReturnValue:
    @pytest.mark.asyncio
    async def test_success_returns_true(self, vm, mock_cache):
        mock_cache.get_watchlist.return_value = _make_watchlist_df()
        assert await vm.load_watchlist() is True

    @pytest.mark.asyncio
    async def test_empty_table_returns_true(self, vm, mock_cache):
        """成功加载空表也算成功（True），不得与失败混淆（R21）。"""
        mock_cache.get_watchlist.return_value = pd.DataFrame()
        assert await vm.load_watchlist() is True

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, vm, mock_cache):
        mock_cache.get_watchlist.side_effect = RuntimeError("db error")
        assert await vm.load_watchlist() is False


# --- F01: add_to_watchlist 结果契约（写失败不刷新、不伪装成功） ---


class TestAddMutationResult:
    @pytest.mark.asyncio
    async def test_success_returns_applied(self, vm, mock_cache):
        result = await vm.add_to_watchlist("000001.SZ", "平安银行")
        assert result == WatchlistMutationResult("applied")
        mock_cache.get_watchlist.assert_awaited()

    @pytest.mark.asyncio
    async def test_write_failure_returns_failed_without_refresh(self, vm, mock_cache):
        mock_cache.add_to_watchlist.side_effect = RuntimeError("db error at /home/user/secret")
        result = await vm.add_to_watchlist("000001.SZ", "平安银行")
        assert result.status == "failed"
        assert isinstance(result.message, Message)
        # 写失败不得刷新列表（避免把未落库的空列表当成成功）
        mock_cache.get_watchlist.assert_not_awaited()
        # 不得污染列表 error 通道（否则空列表会被 ErrorState 整体替换）
        assert vm.state.load_error is None
        assert vm.state.is_loading is False
        # message 仅含 i18n key / 安全参数，不含原始 DB 异常串（R9）
        assert "secret" not in str(result.message.params)

    @pytest.mark.asyncio
    async def test_refresh_failure_returns_applied_refresh_failed(self, vm, mock_cache):
        """写入成功但刷新失败 → applied_refresh_failed（数据已落库，需提示手动刷新）。"""
        mock_cache.get_watchlist.side_effect = RuntimeError("db error")
        result = await vm.add_to_watchlist("000001.SZ", "平安银行")
        assert result.status == "applied_refresh_failed"
        assert result.message is None


# --- F01: remove_from_watchlist 结果契约 ---


class TestRemoveMutationResult:
    @pytest.mark.asyncio
    async def test_success_returns_applied(self, vm, mock_cache):
        result = await vm.remove_from_watchlist("000001.SZ")
        assert result == WatchlistMutationResult("applied")
        mock_cache.get_watchlist.assert_awaited()

    @pytest.mark.asyncio
    async def test_write_failure_returns_failed_without_refresh(self, vm, mock_cache):
        mock_cache.remove_from_watchlist.side_effect = RuntimeError("db error")
        result = await vm.remove_from_watchlist("000001.SZ")
        assert result.status == "failed"
        assert isinstance(result.message, Message)
        mock_cache.get_watchlist.assert_not_awaited()
        assert vm.state.load_error is None

    @pytest.mark.asyncio
    async def test_refresh_failure_returns_applied_refresh_failed(self, vm, mock_cache):
        mock_cache.get_watchlist.side_effect = RuntimeError("db error")
        result = await vm.remove_from_watchlist("000001.SZ")
        assert result.status == "applied_refresh_failed"
        assert result.message is None


# --- F03: search_stocks 请求代次隔离（乱序响应/关闭回填不得覆盖新结果） ---


class TestSearchGenerationIsolation:
    @pytest.mark.asyncio
    async def test_new_search_clears_previous_results_and_sets_loading(self, vm, mock_cache):
        mock_cache.search_stocks.return_value = _make_stock_search_df()
        await vm.search_stocks("旧")
        assert vm.state.search_results

        started = asyncio.Event()
        gate = asyncio.Event()

        async def _slow(keyword: str) -> pd.DataFrame:
            started.set()
            await gate.wait()
            return _make_stock_search_df()

        mock_cache.search_stocks.side_effect = _slow
        task = asyncio.create_task(vm.search_stocks("新"))
        await started.wait()
        # 新请求发起时立即清空旧候选并置 loading（不等待旧响应）
        assert vm.state.search_results == ()
        assert vm.state.search_keyword == "新"
        assert vm.state.is_searching is True
        gate.set()
        await task

    @pytest.mark.asyncio
    async def test_duplicate_in_flight_keyword_is_ignored(self, vm, mock_cache):
        started = asyncio.Event()
        gate = asyncio.Event()

        async def _slow(keyword: str) -> pd.DataFrame:
            started.set()
            await gate.wait()
            return _make_stock_search_df()

        mock_cache.search_stocks.side_effect = _slow
        task = asyncio.create_task(vm.search_stocks("平安"))
        await started.wait()
        # 同一 keyword 且仍在查询中 → 忽略重复请求，不再发起查询
        await vm.search_stocks("平安")
        assert mock_cache.search_stocks.await_count == 1
        gate.set()
        await task

    @pytest.mark.asyncio
    async def test_stale_response_does_not_overwrite_newer(self, vm, mock_cache):
        df_a = pd.DataFrame({"ts_code": ["000001.SZ"], "name": ["旧结果"]})
        df_b = pd.DataFrame({"ts_code": ["600000.SH"], "name": ["新结果"]})
        started_a = asyncio.Event()
        gate_a = asyncio.Event()

        async def _search(keyword: str) -> pd.DataFrame:
            if keyword == "A":
                started_a.set()
                await gate_a.wait()
                return df_a
            return df_b

        mock_cache.search_stocks.side_effect = _search
        task_a = asyncio.create_task(vm.search_stocks("A"))
        await started_a.wait()
        task_b = asyncio.create_task(vm.search_stocks("B"))
        await task_b
        assert vm.state.search_keyword == "B"
        assert vm.state.search_results[0].name == "新结果"
        # 释放旧请求，其迟到响应不得覆盖新结果
        gate_a.set()
        await task_a
        assert vm.state.search_keyword == "B"
        assert vm.state.search_results[0].name == "新结果"
        assert vm.state.is_searching is False

    @pytest.mark.asyncio
    async def test_stale_failure_does_not_overwrite_newer_error_channel(self, vm, mock_cache):
        started_a = asyncio.Event()
        gate_a = asyncio.Event()
        df_b = pd.DataFrame({"ts_code": ["600000.SH"], "name": ["新结果"]})

        async def _search(keyword: str) -> pd.DataFrame:
            if keyword == "A":
                started_a.set()
                await gate_a.wait()
                raise RuntimeError("stale failure")
            return df_b

        mock_cache.search_stocks.side_effect = _search
        task_a = asyncio.create_task(vm.search_stocks("A"))
        await started_a.wait()
        task_b = asyncio.create_task(vm.search_stocks("B"))
        await task_b
        gate_a.set()
        await task_a
        # 旧请求失败不得写入新请求的 error 通道 / loading
        assert vm.state.search_error is None
        assert vm.state.is_searching is False
        assert vm.state.search_results[0].name == "新结果"

    @pytest.mark.asyncio
    async def test_stale_cancel_does_not_clear_newer_loading(self, vm, mock_cache):
        started_a = asyncio.Event()
        gate_a = asyncio.Event()
        started_b = asyncio.Event()
        gate_b = asyncio.Event()

        async def _search(keyword: str) -> pd.DataFrame:
            if keyword == "A":
                started_a.set()
                await gate_a.wait()
                return pd.DataFrame()
            started_b.set()
            await gate_b.wait()
            return pd.DataFrame()

        mock_cache.search_stocks.side_effect = _search
        task_a = asyncio.create_task(vm.search_stocks("A"))
        await started_a.wait()
        task_b = asyncio.create_task(vm.search_stocks("B"))
        await started_b.wait()
        assert vm.state.is_searching is True
        task_a.cancel()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，VM 不得吞没取消信号
            await task_a
        # A 取消不得清除 B 的 loading
        assert vm.state.is_searching is True
        gate_b.set()
        await task_b

    @pytest.mark.asyncio
    async def test_clear_search_invalidates_in_flight(self, vm, mock_cache):
        started_a = asyncio.Event()
        gate_a = asyncio.Event()

        async def _search(keyword: str) -> pd.DataFrame:
            started_a.set()
            await gate_a.wait()
            return _make_stock_search_df()

        mock_cache.search_stocks.side_effect = _search
        task_a = asyncio.create_task(vm.search_stocks("A"))
        await started_a.wait()
        await vm.clear_search()
        gate_a.set()
        await task_a
        # 关闭对话框（clear_search）后，在途响应被代次隔离丢弃
        assert vm.state.search_results == ()
        assert vm.state.search_keyword == ""
        assert vm.state.is_searching is False

    @pytest.mark.asyncio
    async def test_dispose_invalidates_in_flight(self, vm, mock_cache):
        started_a = asyncio.Event()
        gate_a = asyncio.Event()

        async def _search(keyword: str) -> pd.DataFrame:
            started_a.set()
            await gate_a.wait()
            return _make_stock_search_df()

        mock_cache.search_stocks.side_effect = _search
        task_a = asyncio.create_task(vm.search_stocks("A"))
        await started_a.wait()
        vm.dispose()
        gate_a.set()
        await task_a
        # dispose 后旧响应不得回填候选结果
        assert vm.state.search_results == ()
