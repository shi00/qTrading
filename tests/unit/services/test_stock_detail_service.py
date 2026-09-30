# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

"""StockDetailService 单元测试（UX-09 MAJOR-04）。

覆盖：
1. 纯归一函数（_to_float / _normalize_cell / _index_rows_by_ts_code / _some_row_dict）边界；
2. load_watchlist_quotes：批量合并、缺失保持 None（R21）、查询失败降级为无行情、CancelledError 传播；
3. load_stock_detail：按代码组装 5 表、name 回退、失败返回 None、CancelledError 传播；
4. get_stock_history：转调 cache.get_recent_quotes（duck-typed data_processor）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from services.stock_detail_service import (
    StockDetailService,
    StockQuote,
    _index_rows_by_ts_code,
    _normalize_cell,
    _some_row_dict,
    _to_float,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_cache() -> MagicMock:
    cache = MagicMock()
    cache.get_latest_quotes_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_latest_ai_reviews_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_latest_indicators_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_latest_financials_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_stock_basic_bulk = AsyncMock(return_value=pd.DataFrame())
    cache.get_recent_quotes = AsyncMock(return_value=pd.DataFrame())
    return cache


@pytest.fixture
def service(mock_cache) -> StockDetailService:
    return StockDetailService(cache=mock_cache)


# --- 纯函数 ---


class TestToFloat:
    def test_none_returns_none(self):
        assert _to_float(None) is None

    def test_bool_returns_none(self):
        assert _to_float(True) is None
        assert _to_float(False) is None

    def test_nan_returns_none(self):
        assert _to_float(float("nan")) is None

    def test_numeric_types(self):
        assert _to_float(12) == 12.0
        assert _to_float(12.5) == 12.5
        assert _to_float("12.5") == 12.5

    def test_non_numeric_string_returns_none(self):
        assert _to_float("abc") is None


class TestNormalizeCell:
    def test_none_and_nan(self):
        assert _normalize_cell(None) is None
        assert _normalize_cell(float("nan")) is None

    def test_blank_string(self):
        assert _normalize_cell("   ") is None

    def test_zero_is_preserved(self):
        """0 是合法数值，不得被归一为 None（R21 反向保护）。"""
        assert _normalize_cell(0) == 0

    def test_value_passthrough(self):
        assert _normalize_cell("平安银行") == "平安银行"


class TestIndexRowsByTsCode:
    def test_none_and_empty(self):
        assert _index_rows_by_ts_code(None) == {}
        assert _index_rows_by_ts_code(pd.DataFrame()) == {}

    def test_missing_ts_code_column(self):
        assert _index_rows_by_ts_code(pd.DataFrame({"close": [1.0]})) == {}

    def test_indexes_by_code(self):
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "600000.SH"],
                "close": [12.34, 9.9],
            }
        )
        result = _index_rows_by_ts_code(df)
        assert set(result) == {"000001.SZ", "600000.SH"}
        assert result["000001.SZ"]["close"] == 12.34

    def test_skips_blank_ts_code(self):
        df = pd.DataFrame({"ts_code": ["000001.SZ", ""], "close": [1.0, 2.0]})
        result = _index_rows_by_ts_code(df)
        assert set(result) == {"000001.SZ"}


class TestSomeRowDict:
    def test_none_and_empty(self):
        assert _some_row_dict(None) == {}
        assert _some_row_dict(pd.DataFrame()) == {}

    def test_first_row_normalized(self):
        df = pd.DataFrame([{"ts_code": "000001.SZ", "name": "平安银行", "note": None}])
        result = _some_row_dict(df)
        assert result["ts_code"] == "000001.SZ"
        assert result["name"] == "平安银行"
        assert result["note"] is None


# --- load_watchlist_quotes ---


class TestLoadWatchlistQuotes:
    @pytest.mark.asyncio
    async def test_empty_codes_short_circuits(self, service, mock_cache):
        assert await service.load_watchlist_quotes([]) == {}
        assert await service.load_watchlist_quotes([""]) == {}
        mock_cache.get_latest_quotes_bulk.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_merges_quotes_and_ai(self, service, mock_cache):
        mock_cache.get_latest_quotes_bulk.return_value = pd.DataFrame(
            [{"ts_code": "000001.SZ", "close": 12.34, "pct_chg": 1.5}]
        )
        mock_cache.get_latest_ai_reviews_bulk.return_value = pd.DataFrame([{"ts_code": "000001.SZ", "ai_score": 85}])
        result = await service.load_watchlist_quotes(["000001.SZ", "600000.SH"])
        assert result["000001.SZ"] == StockQuote(latest_close=12.34, pct_chg=1.5, ai_score=85.0)
        # 无行情/无 AI 的代码：字段为 None（R21 不得填 0）
        assert result["600000.SH"] == StockQuote(latest_close=None, pct_chg=None, ai_score=None)

    @pytest.mark.asyncio
    async def test_missing_ai_keeps_none(self, service, mock_cache):
        mock_cache.get_latest_quotes_bulk.return_value = pd.DataFrame([{"ts_code": "000001.SZ", "close": 12.34}])
        result = await service.load_watchlist_quotes(["000001.SZ"])
        assert result["000001.SZ"].latest_close == 12.34
        assert result["000001.SZ"].ai_score is None

    @pytest.mark.asyncio
    async def test_query_failure_degrades_to_empty(self, service, mock_cache):
        mock_cache.get_latest_quotes_bulk.side_effect = RuntimeError("db error")
        assert await service.load_watchlist_quotes(["000001.SZ"]) == {}

    @pytest.mark.asyncio
    async def test_propagates_cancelled_error(self, service, mock_cache):
        mock_cache.get_latest_quotes_bulk.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，服务不得吞没取消信号
            await service.load_watchlist_quotes(["000001.SZ"])


# --- load_stock_detail ---


class TestLoadStockDetail:
    @pytest.mark.asyncio
    async def test_empty_code_returns_none(self, service, mock_cache):
        assert await service.load_stock_detail("") is None
        assert await service.load_stock_detail("   ") is None
        mock_cache.get_latest_quotes_bulk.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_merges_all_sources(self, service, mock_cache):
        mock_cache.get_latest_quotes_bulk.return_value = pd.DataFrame(
            [{"ts_code": "000001.SZ", "close": 12.34, "pct_chg": 1.5}]
        )
        mock_cache.get_latest_indicators_bulk.return_value = pd.DataFrame([{"ts_code": "000001.SZ", "pe_ttm": 8.1}])
        mock_cache.get_latest_financials_bulk.return_value = pd.DataFrame([{"ts_code": "000001.SZ", "roe": 10.2}])
        mock_cache.get_stock_basic_bulk.return_value = pd.DataFrame(
            [{"ts_code": "000001.SZ", "name": "平安银行", "industry": "银行"}]
        )
        mock_cache.get_latest_ai_reviews_bulk.return_value = pd.DataFrame([{"ts_code": "000001.SZ", "ai_score": 85}])
        data = await service.load_stock_detail("000001.SZ")
        assert data is not None
        assert data["ts_code"] == "000001.SZ"
        assert data["close"] == 12.34
        assert data["pe_ttm"] == 8.1
        assert data["roe"] == 10.2
        assert data["name"] == "平安银行"
        assert data["industry"] == "银行"
        assert data["ai_score"] == 85
        mock_cache.get_latest_quotes_bulk.assert_awaited_once_with(["000001.SZ"])

    @pytest.mark.asyncio
    async def test_name_falls_back_to_provided_name(self, service, mock_cache):
        data = await service.load_stock_detail("000001.SZ", fallback_name="平安银行")
        assert data is not None
        assert data["name"] == "平安银行"

    @pytest.mark.asyncio
    async def test_name_falls_back_to_code(self, service, mock_cache):
        data = await service.load_stock_detail("000001.SZ")
        assert data is not None
        assert data["name"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_failure_returns_none(self, service, mock_cache):
        mock_cache.get_latest_indicators_bulk.side_effect = RuntimeError("db error")
        assert await service.load_stock_detail("000001.SZ") is None

    @pytest.mark.asyncio
    async def test_propagates_cancelled_error(self, service, mock_cache):
        mock_cache.get_latest_quotes_bulk.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion CancelledError 传播契约：raises 即验证，服务不得吞没取消信号
            await service.load_stock_detail("000001.SZ")


# --- get_stock_history ---


class TestGetStockHistory:
    @pytest.mark.asyncio
    async def test_delegates_to_cache(self, service, mock_cache):
        expected = pd.DataFrame({"trade_date": ["20240101"]})
        mock_cache.get_recent_quotes.return_value = expected
        result = await service.get_stock_history("000001.SZ", days=120)
        mock_cache.get_recent_quotes.assert_awaited_once_with("000001.SZ", 120)
        assert result is expected
