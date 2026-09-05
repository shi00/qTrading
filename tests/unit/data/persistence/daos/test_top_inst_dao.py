"""Phase 2E：TopInstDao 单元测试。"""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock
from contextlib import asynccontextmanager

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.top_inst_dao import TopInstDao

pytestmark = pytest.mark.unit


def _make_dao() -> TopInstDao:
    dao = TopInstDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db = AsyncMock(return_value=pd.DataFrame())
    dao.chunked_in_query = AsyncMock(return_value=pd.DataFrame())

    @asynccontextmanager
    async def mock_begin(conn=None):
        yield "mock_conn"

    dao._guarded_begin = mock_begin
    return dao


class TestSaveTopInst:
    @pytest.mark.asyncio
    async def test_save_top_inst_upsert_none_returns_zero(self):
        """save_top_inst(None) 返回 0，不调用 _save_upsert。"""

        # 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
        # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
        # 测试行为由测试用例本身验证。

        dao = _make_dao()
        assert await dao.save_top_inst(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_top_inst_upsert_empty_returns_zero(self):
        """save_top_inst 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_top_inst(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_top_inst_upsert_valid_calls_save_upsert(self):
        """save_top_inst 非空 DataFrame 调用 _save_upsert，传入表名 top_inst + PK 列。"""
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "exalter": ["机构专用"],
                "side": ["B"],
                "buy": [800000.0],
                "buy_rate": [12.5],
                "sell": [300000.0],
                "sell_rate": [5.0],
                "net_buy": [500000.0],
                "reason": ["日涨幅偏离值达7%"],
            }
        )
        result = await dao.save_top_inst(df)
        assert result == 5
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        # 第一个位置参数是 df；第二个是表名
        assert call_args.args[1] == "top_inst"
        # DAT-09：pk_columns 必须包含逐席位主键 ts_code/trade_date/exalter/side，
        # 且含 reason 维度（同日多上榜理由，review 730-C1）。
        pk_columns = call_args.kwargs["pk_columns"]
        for col in ("ts_code", "trade_date", "exalter", "side", "reason"):
            assert col in pk_columns


class TestGetTopInstBatch:
    @pytest.mark.asyncio
    async def test_get_top_inst_batch_empty_ts_codes_returns_empty_df(self):
        """ts_codes 为空列表直接返回空 DataFrame，不发起查询。"""
        dao = _make_dao()
        result = await dao.get_top_inst_batch([])
        assert result.empty
        dao.chunked_in_query.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_top_inst_batch_with_as_of_date_passes_param(self):
        """as_of_date 非空时使用带 trade_date <= $N 过滤的 SQL 模板。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_top_inst_batch(["000001.SZ", "000002.SZ"], as_of_date="20240614")

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        # params_fn 应返回 [as_of_date]
        params_fn = call_kwargs["params_fn"]
        assert params_fn(["000001.SZ"]) == ["20240614"]

    @pytest.mark.asyncio
    async def test_get_top_inst_batch_without_as_of_date_uses_simple_template(self):
        """as_of_date 为 None 时使用不带日期过滤的 SQL 模板，无 params_fn。"""
        dao = _make_dao()
        expected = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240614"]})
        dao.chunked_in_query = AsyncMock(return_value=expected)

        result = await dao.get_top_inst_batch(["000001.SZ"], as_of_date=None)

        pd.testing.assert_frame_equal(result, expected)
        dao.chunked_in_query.assert_awaited_once()
        call_kwargs = dao.chunked_in_query.call_args.kwargs
        assert "params_fn" not in call_kwargs

    @pytest.mark.asyncio
    async def test_get_top_inst_batch_exception_returns_empty_df(self):
        """非 CancelledError/EngineDisposedError 异常返回空 DataFrame，不抛出。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=RuntimeError("db error"))

        result = await dao.get_top_inst_batch(["000001.SZ"])

        assert result.empty

    @pytest.mark.asyncio
    async def test_get_top_inst_batch_cancelled_error_propagates(self):
        """asyncio.CancelledError 必须传播（R2），不返回空 DataFrame。"""
        dao = _make_dao()
        dao.chunked_in_query = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await dao.get_top_inst_batch(["000001.SZ"])
