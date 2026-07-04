"""Phase 3D：ShareFloatDao 单元测试。"""

import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.share_float_dao import ShareFloatDao

pytestmark = pytest.mark.unit


def _make_dao() -> ShareFloatDao:
    dao = ShareFloatDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=pd.DataFrame())
    dao.chunked_in_query = AsyncMock(return_value=pd.DataFrame())

    @asynccontextmanager
    async def mock_begin(conn=None):
        yield "mock_conn"

    dao._guarded_begin = mock_begin
    return dao


class TestSaveShareFloat:
    @pytest.mark.asyncio
    async def test_save_share_float_none_returns_zero(self):
        """save_share_float(None) 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_share_float(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_share_float_empty_returns_zero(self):
        """save_share_float 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_share_float(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_share_float_valid_calls_save_upsert(self):
        """save_share_float 非空 DataFrame 调用 _save_upsert，传入表名 share_float + PK 列。"""
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "ann_date": [datetime.date(2024, 6, 1)],
                "float_date": [datetime.date(2024, 8, 15)],
                "float_share": [1000.0],
                "float_ratio": [5.2],
                "share_type": ["定向增发"],
            }
        )
        result = await dao.save_share_float(df)
        assert result == 5
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        # 第一个位置参数是 df；第二个是表名
        assert call_args.args[1] == "share_float"
        # pk_columns 必须包含 ts_code 和 float_date
        pk_columns = call_args.kwargs["pk_columns"]
        assert "ts_code" in pk_columns
        assert "float_date" in pk_columns


class TestGetShareFloatUpcomingBatch:
    @pytest.mark.asyncio
    async def test_empty_ts_codes_returns_empty_df(self):
        """ts_codes 为空列表直接返回空 DataFrame，不发起查询。"""
        dao = _make_dao()
        result = await dao.get_share_float_upcoming_batch([])
        assert result.empty
        dao.chunked_in_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_as_of_date_passes_params(self):
        """as_of_date 非空时 params_fn 返回 [start_date, end_date]，end_date = start_date + days。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"], "float_date": [datetime.date(2024, 8, 15)]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_share_float_upcoming_batch(["000001.SZ", "000002.SZ"], as_of_date="20240601", days=90)

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        params_fn = call_kwargs["params_fn"]
        start_date, end_date = params_fn(["000001.SZ"])
        assert start_date == datetime.date(2024, 6, 1)
        assert end_date == datetime.date(2024, 8, 30)

    @pytest.mark.asyncio
    async def test_without_as_of_date_uses_today(self):
        """as_of_date 为 None 时使用今天作为 start_date，params_fn 仍返回 [start_date, end_date]。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_share_float_upcoming_batch(["000001.SZ"], as_of_date=None)

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        params_fn = call_kwargs["params_fn"]
        start_date, end_date = params_fn(["000001.SZ"])
        today = datetime.date.today()
        assert start_date == today
        assert end_date == today + datetime.timedelta(days=90)

    @pytest.mark.asyncio
    async def test_exception_returns_empty_df(self):
        """非 CancelledError/EngineDisposedError 异常返回空 DataFrame，不抛出。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=RuntimeError("db error"))

        result = await dao.get_share_float_upcoming_batch(["000001.SZ"])

        assert result.empty
