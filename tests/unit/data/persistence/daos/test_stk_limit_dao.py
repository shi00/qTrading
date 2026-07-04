"""Phase 2G：StkLimitDao 单元测试（仅数据层，不注入 AI）。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.stk_limit_dao import StkLimitDao

pytestmark = pytest.mark.unit


def _make_dao() -> StkLimitDao:
    dao = StkLimitDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=5)
    return dao


class TestSaveStkLimit:
    @pytest.mark.asyncio
    async def test_save_stk_limit_none_returns_zero(self):
        """save_stk_limit(None) 返回 0，不调用 _save_upsert。"""
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
