"""Phase 3B：PledgeDetailDao 单元测试。"""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false

import asyncio
import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.pledge_detail_dao import PledgeDetailDao

pytestmark = pytest.mark.unit


def _make_dao() -> PledgeDetailDao:
    dao = PledgeDetailDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=pd.DataFrame())
    dao.chunked_in_query = AsyncMock(return_value=pd.DataFrame())

    @asynccontextmanager
    async def mock_begin(conn=None):
        yield "mock_conn"

    dao._guarded_begin = mock_begin
    return dao


class TestSavePledgeDetail:
    @pytest.mark.asyncio
    async def test_save_pledge_detail_none_returns_zero(self):
        """save_pledge_detail(None) 返回 0，不调用 _save_upsert。"""

        # 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
        # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
        # 测试行为由测试用例本身验证。

        dao = _make_dao()
        assert await dao.save_pledge_detail(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_pledge_detail_empty_returns_zero(self):
        """save_pledge_detail 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_pledge_detail(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_pledge_detail_valid_calls_save_upsert(self):
        """save_pledge_detail 非空 DataFrame 调用 _save_upsert，传入表名 pledge_detail + PK 列。"""
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "ann_date": ["20240630"],
                "holder_name": ["张三"],
                "start_date": ["20240501"],
                "pledge_amount": [1000000.0],
                "end_date": ["20250501"],
                "is_release": ["N"],
                "release_date": [None],
                "pledgor": ["某银行"],
                "holding_amount": [5000000.0],
                "pledged_amount": [1000000.0],
                "p_total_ratio": [20.0],
                "h_total_ratio": [5.0],
                "is_buyback": ["N"],
            }
        )
        result = await dao.save_pledge_detail(df)
        assert result == 5
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        # 第一个位置参数是 df；第二个是表名
        assert call_args.args[1] == "pledge_detail"
        # DAT-09：pk_columns 必须包含新五维主键
        pk_columns = call_args.kwargs["pk_columns"]
        for col in ("ts_code", "ann_date", "holder_name", "start_date", "pledge_amount"):
            assert col in pk_columns


class TestGetPledgeDetailBatch:
    @pytest.mark.asyncio
    async def test_get_pledge_detail_batch_empty_ts_codes_returns_empty_df(self):
        """ts_codes 为空列表直接返回空 DataFrame，不发起查询。"""
        dao = _make_dao()
        result = await dao.get_pledge_detail_batch([])
        assert result.empty
        dao.chunked_in_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_pledge_detail_batch_with_as_of_date_passes_param(self):
        """as_of_date 非空时使用带 ann_date <= $N PIT 过滤的 SQL 模板（DAT-09 改用官方 ann_date）。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"], "ann_date": ["20240630"]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_pledge_detail_batch(["000001.SZ", "000002.SZ"], as_of_date="20240630")

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_args = dao.chunked_in_query.call_args
        call_kwargs = call_args.kwargs
        # params_fn 应返回 [as_of_date]（DAT-26: DAO 边界统一 date 对象）
        params_fn = call_kwargs["params_fn"]
        assert params_fn(["000001.SZ"]) == [datetime.date(2024, 6, 30)]
        # DAT-09: PIT 过滤基于官方 ann_date，替代旧的 end_date + INTERVAL 保守滞后
        # chunked_in_query(_read_db, sql_template, ts_codes, ...)，args[1] 即 as_of 分支的模板函数
        sql_template = call_args.args[1]
        sql = sql_template("$1,$2", 2, 0)
        assert "ann_date <=" in sql
        assert "INTERVAL" not in sql

    @pytest.mark.asyncio
    async def test_get_pledge_detail_batch_without_as_of_date_uses_simple_template(self):
        """as_of_date 为 None 时使用不带日期过滤的 SQL 模板，无 params_fn。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"], "end_date": ["20240630"]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_pledge_detail_batch(["000001.SZ"], as_of_date=None)

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        assert "params_fn" not in call_kwargs

    @pytest.mark.asyncio
    async def test_get_pledge_detail_batch_exception_returns_empty_df(self):
        """非 CancelledError/EngineDisposedError 异常返回空 DataFrame，不抛出。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=RuntimeError("db error"))

        result = await dao.get_pledge_detail_batch(["000001.SZ"])

        assert result.empty

    @pytest.mark.asyncio
    async def test_get_pledge_detail_batch_cancelled_error_propagates(self):
        """asyncio.CancelledError 必须传播（R2），不返回空 DataFrame。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await dao.get_pledge_detail_batch(["000001.SZ"])
