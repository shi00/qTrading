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


class TestGetSwL2Mapping:
    """get_sw_l2_mapping 三分支（None / 空 list / 非空 list）+ 异常分层 + 结果构造。"""

    @pytest.mark.asyncio
    async def test_none_ts_codes_queries_full_table(self):
        """ts_codes=None 时走全表查询分支（无 IN 子句），返回 dict 映射。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "sw_l2_name": ["种植业", "房地产开发"],
                }
            )
        )

        result = await dao.get_sw_l2_mapping(None)

        assert result == {"000001.SZ": "种植业", "000002.SZ": "房地产开发"}
        dao._read_db.assert_awaited_once()
        sql_arg = dao._read_db.call_args.args[0]
        # None 分支：SQL 不含 IN ({placeholders}) 占位符
        assert "{placeholders}" not in sql_arg
        # 全表查询无 WHERE 参数：_read_db 仅接收 SQL 一个位置参数
        assert len(dao._read_db.call_args.args) == 1

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_dict_without_query(self):
        """ts_codes=[] 时直接返回 {}，不发起查询。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock()

        result = await dao.get_sw_l2_mapping([])

        assert result == {}
        dao._read_db.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_specific_ts_codes_uses_chunked_in_query(self):
        """ts_codes 非空时走 chunked_in_query 分支（IN 子句 + 分块）。"""
        dao = _make_member_dao()
        expected_df = pd.DataFrame({"ts_code": ["000001.SZ"], "sw_l2_name": ["种植业"]})
        # chunked_in_query 是 staticmethod，通过实例属性覆盖以拦截调用
        dao.chunked_in_query = AsyncMock(return_value=expected_df)

        result = await dao.get_sw_l2_mapping(["000001.SZ", "000002.SZ"])

        assert result == {"000001.SZ": "种植业"}
        dao.chunked_in_query.assert_awaited_once()
        # 第一个参数是 _read_db 可调用对象，第二个是 SQL 模板（含 {placeholders}）
        sql_template = dao.chunked_in_query.call_args.args[1]
        assert "{placeholders}" in sql_template
        assert "ts_code IN" in sql_template
        # 第三个参数是 ts_codes 列表
        assert dao.chunked_in_query.call_args.args[2] == ["000001.SZ", "000002.SZ"]
        # _read_db 不应被直接调用（由 chunked_in_query 内部调用）
        dao._read_db.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """asyncio.CancelledError 必须传播（R2），不返回空 dict。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await dao.get_sw_l2_mapping(None)

    @pytest.mark.asyncio
    async def test_engine_disposed_propagates(self):
        """EngineDisposedError 必须传播（R5），不返回空 dict。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(side_effect=EngineDisposedError("disposed"))

        with pytest.raises(EngineDisposedError):
            await dao.get_sw_l2_mapping(None)

    @pytest.mark.asyncio
    async def test_other_exception_returns_empty_dict_with_sanitization(self):
        """非 Cancelled/EngineDisposed 异常 sanitize 后返回空 dict。"""
        dao = _make_member_dao()
        original_error = RuntimeError("db error with sensitive: token=abc")
        dao._read_db = AsyncMock(side_effect=original_error)

        with patch("data.persistence.daos.sw_industry_dao.DataSanitizer") as mock_sanitizer:
            mock_sanitizer.sanitize_error = MagicMock(return_value="sanitized error")
            result = await dao.get_sw_l2_mapping(None)

        assert result == {}
        mock_sanitizer.sanitize_error.assert_called_once_with(original_error)

    @pytest.mark.asyncio
    async def test_none_df_result_returns_empty_dict(self):
        """_read_db 返回 None 时返回空 dict。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(return_value=None)

        result = await dao.get_sw_l2_mapping(None)

        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_df_result_returns_empty_dict(self):
        """_read_db 返回空 DataFrame 时返回空 dict。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(return_value=pd.DataFrame())

        result = await dao.get_sw_l2_mapping(None)

        assert result == {}

    @pytest.mark.asyncio
    async def test_dict_zip_preserves_ts_code_to_l2_mapping(self):
        """返回的 dict 通过 zip(ts_code, sw_l2_name) 构造，保留多行映射。"""
        dao = _make_member_dao()
        dao._read_db = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "600000.SH"],
                    "sw_l2_name": ["种植业", "房地产开发", "股份制银行"],
                }
            )
        )

        result = await dao.get_sw_l2_mapping(None)

        assert result == {
            "000001.SZ": "种植业",
            "000002.SZ": "房地产开发",
            "600000.SH": "股份制银行",
        }
