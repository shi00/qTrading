"""WatchlistDao 单元测试 (FR-UX-004, Task 4.2).

覆盖 add/remove/get/is_in 四个核心方法，验证：
- R4: asyncpg 原生查询用 $1 占位符（非 %s）
- R8: 批量写入用 _save_upsert
- R12/R13: 表与 DAO 已注册（由 pre-commit check_redlines.py 守护，本测试覆盖行为）
"""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false

import pandas as pd
import pytest
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncEngine

from data.persistence.daos.watchlist_dao import WatchlistDao

pytestmark = pytest.mark.unit


def _make_dao() -> WatchlistDao:
    """构造一个 mock engine 的 WatchlistDao，核心方法被 mock。"""
    dao = WatchlistDao(MagicMock(spec=AsyncEngine))
    dao._save_upsert = AsyncMock(return_value=1)
    dao._write_db = AsyncMock(return_value=1)
    dao._read_db_select = AsyncMock(return_value=pd.DataFrame())
    return dao


class TestAddToWatchlist:
    @pytest.mark.asyncio
    async def test_add_to_watchlist_builds_dataframe_and_upserts(self):
        """add_to_watchlist 构建 DataFrame 调用 _save_upsert，upsert by ts_code。"""
        dao = _make_dao()
        result = await dao.add_to_watchlist("000001.SZ", "平安银行", note="测试备注")
        assert result == 1
        dao._save_upsert.assert_awaited_once()
        call_args = dao._save_upsert.call_args
        # 第二个位置参数是表名
        assert call_args.args[1] == "watchlist"
        # 第一个参数是 df
        df_passed = call_args.args[0]
        assert "ts_code" in df_passed.columns
        assert "stock_name" in df_passed.columns
        assert "note" in df_passed.columns
        assert df_passed.iloc[0]["ts_code"] == "000001.SZ"
        assert df_passed.iloc[0]["stock_name"] == "平安银行"
        assert df_passed.iloc[0]["note"] == "测试备注"
        # pk_columns 必须包含 ts_code（upsert by ts_code）
        pk_columns = call_args.kwargs["pk_columns"]
        assert "ts_code" in pk_columns

    @pytest.mark.asyncio
    async def test_add_to_watchlist_without_note(self):
        """无 note 时 note 列为 None。"""
        dao = _make_dao()
        await dao.add_to_watchlist("600000.SH", "浦发银行")
        df_passed = dao._save_upsert.call_args.args[0]
        assert df_passed.iloc[0]["note"] is None


class TestRemoveFromWatchlist:
    @pytest.mark.asyncio
    async def test_remove_uses_dollar_placeholder(self):
        """R4: DELETE 语句用 $1 占位符，非 %s。"""
        dao = _make_dao()
        await dao.remove_from_watchlist("000001.SZ")
        dao._write_db.assert_awaited_once()
        sql = dao._write_db.call_args.args[0]
        assert "$1" in sql
        assert "%s" not in sql
        params = dao._write_db.call_args.args[1]
        assert params == ("000001.SZ",)


class TestGetWatchlist:
    @pytest.mark.asyncio
    async def test_get_watchlist_returns_dataframe(self):
        """get_watchlist 委托 _read_db_select 返回 DataFrame。"""
        dao = _make_dao()
        expected = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "stock_name": ["平安银行"], "added_at": ["2026-07-29"], "note": [""]}
        )
        dao._read_db_select.return_value = expected
        result = await dao.get_watchlist()
        assert result is expected
        dao._read_db_select.assert_awaited_once()


class TestIsInWatchlist:
    @pytest.mark.asyncio
    async def test_is_in_watchlist_true_when_exists(self):
        """存在记录时返回 True。"""
        dao = _make_dao()
        dao._read_db_select.return_value = pd.DataFrame({"ts_code": ["000001.SZ"]})
        assert await dao.is_in_watchlist("000001.SZ") is True

    @pytest.mark.asyncio
    async def test_is_in_watchlist_false_when_empty(self):
        """无记录时返回 False。"""
        dao = _make_dao()
        dao._read_db_select.return_value = pd.DataFrame()
        assert await dao.is_in_watchlist("000001.SZ") is False
