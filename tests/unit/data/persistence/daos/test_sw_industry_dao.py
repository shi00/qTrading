"""Phase 3F-1：SwIndustryClassifyDao + SwIndustryMemberDao 单元测试。"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.base_dao import EngineDisposedError
from data.persistence.daos.sw_industry_dao import SwIndustryClassifyDao, SwIndustryMemberDao

pytestmark = pytest.mark.unit


def _make_classify_dao() -> SwIndustryClassifyDao:
    dao = SwIndustryClassifyDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    return dao


def _make_member_dao() -> SwIndustryMemberDao:
    dao = SwIndustryMemberDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=10)
    dao._read_db = AsyncMock(return_value=pd.DataFrame())

    @asynccontextmanager
    async def mock_begin(conn=None):
        yield "mock_conn"

    dao._guarded_begin = mock_begin
    return dao


class TestSaveSwIndustryClassify:
    @pytest.mark.asyncio
    async def test_none_returns_zero(self):
        """save_sw_industry_classify(None) 返回 0，不调用 _save_upsert。"""
        dao = _make_classify_dao()
        assert await dao.save_sw_industry_classify(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        """save_sw_industry_classify 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_classify_dao()
        assert await dao.save_sw_industry_classify(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_calls_save_upsert(self):
        """非空 DataFrame 调用 _save_upsert，传入表名 + PK 列。"""
        dao = _make_classify_dao()
        df = pd.DataFrame(
            {
                "index_code": ["801010.SI", "801020.SI"],
                "index_name": ["农林牧渔", "采掘"],
                "level": ["L1", "L1"],
                "industry_code": ["110000", "220000"],
                "industry_name": ["农林牧渔", "采掘"],
                "parent_code": ["", ""],
                "is_sw": ["1", "1"],
            }
        )
        result = await dao.save_sw_industry_classify(df)
        assert result == 5
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        assert call_args.args[1] == "sw_industry_classify"
        pk_columns = call_args.kwargs["pk_columns"]
        assert "index_code" in pk_columns
        assert "level" in pk_columns


class TestSaveSwIndustryMember:
    @pytest.mark.asyncio
    async def test_none_returns_zero(self):
        """save_sw_industry_member(None) 返回 0，不调用 _save_upsert。"""
        dao = _make_member_dao()
        assert await dao.save_sw_industry_member(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self):
        """save_sw_industry_member 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_member_dao()
        assert await dao.save_sw_industry_member(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_calls_save_upsert(self):
        """非空 DataFrame 调用 _save_upsert，传入表名 + PK 列。"""
        dao = _make_member_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "index_code": ["801010.SI"],
                "index_name": ["农林牧渔"],
                "sw_l1_code": ["110000"],
                "sw_l1_name": ["农林牧渔"],
                "sw_l2_code": ["110100"],
                "sw_l2_name": ["种植业"],
                "sw_l3_code": ["110101"],
                "sw_l3_name": ["玉米"],
            }
        )
        result = await dao.save_sw_industry_member(df)
        assert result == 10
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        assert call_args.args[1] == "sw_industry_member"
        pk_columns = call_args.kwargs["pk_columns"]
        assert "ts_code" in pk_columns
        assert "index_code" in pk_columns


class TestGetSwIndustryByTsCode:
    @pytest.mark.asyncio
    async def test_empty_ts_code_returns_empty_df(self):
        """ts_code 为空字符串直接返回空 DataFrame，不发起查询。"""
        dao = _make_member_dao()
        result = await dao.get_sw_industry_by_ts_code("")
        assert result.empty
        dao._read_db.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_ts_code_calls_read_db_with_dollar_placeholder(self):
        """ts_code 非空时调用 _read_db，SQL 使用 $1 占位符（R4）。"""
        dao = _make_member_dao()
        expected = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "index_code": ["801010.SI"],
                "index_name": ["农林牧渔"],
                "sw_l1_code": ["110000"],
                "sw_l1_name": ["农林牧渔"],
                "sw_l2_code": ["110100"],
                "sw_l2_name": ["种植业"],
                "sw_l3_code": ["110101"],
                "sw_l3_name": ["玉米"],
            }
        )
        dao._read_db = AsyncMock(return_value=expected)

        result = await dao.get_sw_industry_by_ts_code("000001.SZ")

        pd.testing.assert_frame_equal(result, expected)
        dao._read_db.assert_awaited_once()
        sql_arg = dao._read_db.call_args.args[0]
        # R4：asyncpg 占位符必须是 $1 而非 %s
        assert "$1" in sql_arg
        assert "%s" not in sql_arg
        params_arg = dao._read_db.call_args.args[1]
        assert params_arg == ("000001.SZ",)

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """asyncio.CancelledError 必须传播（R2），不返回空 DataFrame。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await dao.get_sw_industry_by_ts_code("000001.SZ")

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates(self):
        """EngineDisposedError 必须传播（R5），不返回空 DataFrame。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))

        with pytest.raises(EngineDisposedError):
            await dao.get_sw_industry_by_ts_code("000001.SZ")

    @pytest.mark.asyncio
    async def test_other_exception_returns_empty_df(self):
        """非 Cancelled/EngineDisposed 异常 sanitize 后返回空 DataFrame。"""
        dao = _make_member_dao()
        original_error = RuntimeError("db error with sensitive: password=123")
        dao._read_db = AsyncMock(side_effect=original_error)

        # DataSanitizer.sanitize_error 应被调用（验证日志脱敏）
        with patch("data.persistence.daos.sw_industry_dao.DataSanitizer") as mock_sanitizer:
            mock_sanitizer.sanitize_error = MagicMock(return_value="sanitized error")
            result = await dao.get_sw_industry_by_ts_code("000001.SZ")

        assert result.empty
        mock_sanitizer.sanitize_error.assert_called_once_with(original_error)
