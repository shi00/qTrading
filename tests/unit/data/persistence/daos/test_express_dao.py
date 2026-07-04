"""Phase 3G §4.3.4：ExpressDao 单元测试。"""

import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.express_dao import ExpressDao

pytestmark = pytest.mark.unit


def _make_dao() -> ExpressDao:
    dao = ExpressDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=pd.DataFrame())
    dao.chunked_in_query = AsyncMock(return_value=pd.DataFrame())

    @asynccontextmanager
    async def mock_begin(conn=None):
        yield "mock_conn"

    dao._guarded_begin = mock_begin
    return dao


class TestSaveExpress:
    @pytest.mark.asyncio
    async def test_save_express_none_returns_zero(self):
        """save_express(None) 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_express(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_express_empty_returns_zero(self):
        """save_express 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_express(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_express_valid_calls_save_upsert(self):
        """save_express 非空 DataFrame 调用 _save_upsert，传入表名 express + PK 列。"""
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": [datetime.date(2024, 9, 30)],
                "ann_date": [datetime.date(2024, 10, 15)],
                "type": ["业绩快报"],
                "revenue": [5.0e9],
                "n_income": [8.0e8],
                "total_profit": [9.5e8],
                "yoy_sales": [25.0],
                "yoy_profit": [40.0],
                "yoy_dedu_np": [35.0],
                "deduct_profit": [7.5e8],
            }
        )
        result = await dao.save_express(df)
        assert result == 5
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        # 第一个位置参数是 df；第二个是表名
        assert call_args.args[1] == "express"
        # pk_columns 必须包含 ts_code、end_date、ann_date
        pk_columns = call_args.kwargs["pk_columns"]
        assert "ts_code" in pk_columns
        assert "end_date" in pk_columns
        assert "ann_date" in pk_columns


class TestGetExpressBatch:
    @pytest.mark.asyncio
    async def test_empty_ts_codes_returns_empty_df(self):
        """ts_codes 为空列表直接返回空 DataFrame，不发起查询。"""
        dao = _make_dao()
        result = await dao.get_express_batch([])
        assert result.empty
        dao.chunked_in_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_as_of_date_invokes_chunked_in_query(self):
        """as_of_date 非空时调用 chunked_in_query，params_fn 返回 [as_of_date]。"""
        dao = _make_dao()
        expected = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "end_date": [datetime.date(2024, 9, 30)],
                "ann_date": [datetime.date(2024, 10, 15)],
                "revenue": [5.0e9],
            }
        )
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_express_batch(["000001.SZ", "000002.SZ"], as_of_date="20241015")

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        params_fn = call_kwargs["params_fn"]
        assert params_fn(["000001.SZ"]) == ["20241015"]

    @pytest.mark.asyncio
    async def test_without_as_of_date_invokes_chunked_in_query(self):
        """as_of_date 为 None 时调用 chunked_in_query，无 params_fn。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_express_batch(["000001.SZ"], as_of_date=None)

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        # as_of_date=None 分支不传 params_fn
        assert "params_fn" not in call_kwargs or call_kwargs["params_fn"] is None

    @pytest.mark.asyncio
    async def test_exception_returns_empty_df(self):
        """非 CancelledError 异常返回空 DataFrame，不抛出。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=RuntimeError("db error"))

        result = await dao.get_express_batch(["000001.SZ"])

        assert result.empty

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates(self):
        """EngineDisposedError 必须传播（R5），不返回空 DataFrame。

        v1.10.0 检视 P1-1 修复：补 ``except EngineDisposedError: raise`` 分支。
        """
        from data.persistence.daos.base_dao import EngineDisposedError

        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=EngineDisposedError("disposed"))

        with pytest.raises(EngineDisposedError):
            await dao.get_express_batch(["000001.SZ"])
