"""Phase 3E：StkHoldertradeDao 单元测试。"""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false

import asyncio
import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.stk_holdertrade_dao import StkHoldertradeDao

pytestmark = pytest.mark.unit


def _make_dao() -> StkHoldertradeDao:
    dao = StkHoldertradeDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=pd.DataFrame())
    dao.chunked_in_query = AsyncMock(return_value=pd.DataFrame())

    @asynccontextmanager
    async def mock_begin(conn=None):
        yield "mock_conn"

    dao._guarded_begin = mock_begin
    return dao


class TestSaveStkHoldertrade:
    @pytest.mark.asyncio
    async def test_save_stk_holdertrade_none_returns_zero(self):
        """save_stk_holdertrade(None) 返回 0，不调用 _save_upsert。"""

        # 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
        # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
        # 测试行为由测试用例本身验证。

        dao = _make_dao()
        assert await dao.save_stk_holdertrade(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_stk_holdertrade_empty_returns_zero(self):
        """save_stk_holdertrade 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_stk_holdertrade(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_stk_holdertrade_valid_calls_save_upsert(self):
        """save_stk_holdertrade 非空 DataFrame 调用 _save_upsert，传入表名 stk_holdertrade + PK 列。"""
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "ann_date": [datetime.date(2024, 6, 1)],
                "holder_name": ["张三"],
                "holder_type": ["G"],
                "in_de": ["IN"],
                "change_vol": [10000.0],
                "change_ratio": [0.5],
                "after_share": [1000000.0],
                "after_ratio": [50.0],
            }
        )
        result = await dao.save_stk_holdertrade(df)
        assert result == 5
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        # 第一个位置参数是 df；第二个是表名
        assert call_args.args[1] == "stk_holdertrade"
        # pk_columns 必须包含 4 列 PK
        pk_columns = call_args.kwargs["pk_columns"]
        assert "ts_code" in pk_columns
        assert "ann_date" in pk_columns
        assert "holder_name" in pk_columns
        assert "in_de" in pk_columns


class TestGetStkHoldertradeBatch:
    @pytest.mark.asyncio
    async def test_empty_ts_codes_returns_empty_df(self):
        """ts_codes 为空列表直接返回空 DataFrame，不发起查询。"""
        dao = _make_dao()
        result = await dao.get_stk_holdertrade_batch([])
        assert result.empty
        dao.chunked_in_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_with_as_of_date_passes_params(self):
        """as_of_date 非空时 params_fn 返回 [start_date, end_date]，start_date = end_date - days。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"], "ann_date": [datetime.date(2024, 6, 1)]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_stk_holdertrade_batch(["000001.SZ", "000002.SZ"], as_of_date="20240601", days=180)

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        params_fn = call_kwargs["params_fn"]
        start_date, end_date = params_fn(["000001.SZ"])
        assert start_date == datetime.date(2023, 12, 4)
        assert end_date == datetime.date(2024, 6, 1)

    @pytest.mark.asyncio
    async def test_without_as_of_date_uses_today(self):
        """as_of_date 为 None 时使用今天作为 end_date，params_fn 仍返回 [start_date, end_date]。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_stk_holdertrade_batch(["000001.SZ"], as_of_date=None)

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        params_fn = call_kwargs["params_fn"]
        start_date, end_date = params_fn(["000001.SZ"])
        today = datetime.date.today()
        assert end_date == today
        assert start_date == today - datetime.timedelta(days=180)

    @pytest.mark.asyncio
    async def test_exception_returns_empty_df(self):
        """非 CancelledError/EngineDisposedError 异常返回空 DataFrame，不抛出。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=RuntimeError("db error"))

        result = await dao.get_stk_holdertrade_batch(["000001.SZ"])

        assert result.empty

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """asyncio.CancelledError 必须传播（R2），不返回空 DataFrame。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await dao.get_stk_holdertrade_batch(["000001.SZ"])
