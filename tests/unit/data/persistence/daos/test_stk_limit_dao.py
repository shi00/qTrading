"""Phase 2G：StkLimitDao 单元测试（仅数据层，不注入 AI）。"""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false

import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.stk_limit_dao import StkLimitDao

pytestmark = pytest.mark.unit


def _make_dao() -> StkLimitDao:
    dao = StkLimitDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
    return dao


class TestSaveStkLimit:
    @pytest.mark.asyncio
    async def test_save_stk_limit_none_returns_zero(self):
        """save_stk_limit(None) 返回 0，不调用 _save_upsert。"""

        # 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
        # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
        # 测试行为由测试用例本身验证。

        dao = _make_dao()
        assert await dao.save_stk_limit(None) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_stk_limit_empty_returns_zero(self):
        """save_stk_limit 空 DataFrame 返回 0，不调用 _save_upsert。"""
        dao = _make_dao()
        assert await dao.save_stk_limit(pd.DataFrame()) == 0
        dao._save_upsert.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_save_stk_limit_renames_limit_to_limit_type(self):
        """R17：Tushare API 返回字段名为 "limit"（SQL 保留字），写入前应重命名为 "limit_type"。"""
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "pre_close": [9.5],
                "up_limit": [10.45],
                "down_limit": [8.55],
                "limit": ["U"],
            }
        )
        result = await dao.save_stk_limit(df)
        assert result == 5
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        # 第一个位置参数是 df；第二个是表名
        assert call_args.args[1] == "stk_limit"
        # 重命名后的 df 应含 limit_type 列，不含 limit 列
        df_passed = call_args.args[0]
        assert "limit_type" in df_passed.columns
        assert "limit" not in df_passed.columns
        # pk_columns 必须包含 ts_code 和 trade_date
        pk_columns = call_args.kwargs["pk_columns"]
        assert "ts_code" in pk_columns
        assert "trade_date" in pk_columns

    @pytest.mark.asyncio
    async def test_save_stk_limit_no_rename_when_limit_type_already_present(self):
        """若 df 已含 limit_type 列，不再 rename，避免覆盖。"""
        dao = _make_dao()
        df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": ["20240614"],
                "pre_close": [9.5],
                "up_limit": [10.45],
                "down_limit": [8.55],
                "limit_type": ["U"],
            }
        )
        await dao.save_stk_limit(df)
        df_passed = dao._save_upsert.call_args.args[0]
        assert "limit_type" in df_passed.columns


class TestGetStkLimitRange:
    """区间涨跌停查询（回测撮合层契约）：between 谓词 + DAT-26 边界归一化 + 结果透传。"""

    @pytest.mark.asyncio
    async def test_range_normalizes_yyyymmdd_strings_to_date(self):
        """8 位 YYYYMMDD 串经 _to_db_date 归一化为 date 对象后再绑定（asyncpg 兼容）。"""
        dao = _make_dao()
        await dao.get_stk_limit_range("20240601", "20240614")
        dao._read_db_select.assert_awaited_once()
        stmt = dao._read_db_select.call_args[0][0]
        sql_str = str(stmt)
        assert "BETWEEN" in sql_str
        values = list(stmt.compile().params.values())
        assert values == [datetime.date(2024, 6, 1), datetime.date(2024, 6, 14)]

    @pytest.mark.asyncio
    async def test_range_accepts_date_objects(self):
        """date 对象输入行为不变（归一化幂等）。"""
        dao = _make_dao()
        await dao.get_stk_limit_range(datetime.date(2024, 6, 1), datetime.date(2024, 6, 14))
        stmt = dao._read_db_select.call_args[0][0]
        values = list(stmt.compile().params.values())
        assert values == [datetime.date(2024, 6, 1), datetime.date(2024, 6, 14)]

    @pytest.mark.asyncio
    async def test_range_returns_dataframe_passthrough(self):
        """查询结果原样透传给调用方（回测撮合层按缺失告警，DAO 不改写）。"""
        dao = _make_dao()
        expected = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [datetime.date(2024, 6, 14)],
                "up_limit": [10.45],
                "down_limit": [8.55],
            }
        )
        dao._read_db_select = AsyncMock(return_value=expected)
        result = await dao.get_stk_limit_range("20240601", "20240614")
        assert result is expected

    @pytest.mark.asyncio
    async def test_range_returns_none_passthrough(self):
        """_read_db_select 返回 None 时透传 None（调用方 limit_data_absent 告警分支依赖）。"""
        dao = _make_dao()
        dao._read_db_select = AsyncMock(return_value=None)
        result = await dao.get_stk_limit_range("20240601", "20240614")
        assert result is None
