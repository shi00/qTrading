"""WatchlistDao 单元测试 (FR-UX-004, Task 4.2 / RV-05).

覆盖 add/remove/get/is_in 四个核心方法，验证：
- R4: asyncpg 原生查询用 $1 占位符（非 %s）
- R8: 批量写入用 _save_upsert
- R12/R13: 表与 DAO 已注册（由 pre-commit check_redlines.py 守护，本测试覆盖行为）
- RV-05: add 时固化观察起点（最近交易日 + 复权收盘价 close/adj_factor），
  IS NULL 守卫只写一次；行情缺失保持 NULL（R21 不伪造）
"""
# pyright: reportArgumentType=false, reportAttributeAccessIssue=false

import datetime

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


def _quote_df(trade_date: datetime.date, close: float, adj: float | None) -> pd.DataFrame:
    """构造 _latest_quote 查询的返回 df（单行最近行情）。"""
    return pd.DataFrame({"trade_date": [trade_date], "close": [close], "adj_factor": [adj]})


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


class TestAddToWatchlistObservationOrigin:
    """RV-05: add 时固化观察起点（只写一次守卫 + 复权口径 + R21 不伪造）。"""

    @pytest.mark.asyncio
    async def test_add_pins_origin_when_quote_available(self):
        """行情可得：固化 (最近交易日, close/adj_factor 复权价)，UPDATE 带 IS NULL 守卫。"""
        dao = _make_dao()
        dao._read_db_select.return_value = _quote_df(datetime.date(2026, 9, 19), 10.0, 2.0)
        result = await dao.add_to_watchlist("000001.SZ", "平安银行")
        assert result == 1
        assert dao._write_db.await_count == 1
        sql = dao._write_db.call_args.args[0]
        assert "added_trade_date IS NULL" in sql  # 只写一次守卫（已固化不覆盖）
        assert "$1" in sql and "$2" in sql and "$3" in sql  # R4 参数化
        params = dao._write_db.call_args.args[1]
        assert params[0] == "000001.SZ"
        assert params[1] == datetime.date(2026, 9, 19)
        assert params[2] == pytest.approx(5.0)  # 10.0 / 2.0（复权口径同 _qfq_return_pct）

    @pytest.mark.asyncio
    async def test_add_with_missing_adj_factor_pins_date_only(self):
        """adj_factor 缺失：交易日仍固化，价格 NULL（未复权价与未来复权口径不可比，R21）。"""
        dao = _make_dao()
        dao._read_db_select.return_value = _quote_df(datetime.date(2026, 9, 19), 10.0, None)
        await dao.add_to_watchlist("000001.SZ", "平安银行")
        assert dao._write_db.await_count == 1
        params = dao._write_db.call_args.args[1]
        assert params[1] == datetime.date(2026, 9, 19)
        assert params[2] is None

    @pytest.mark.asyncio
    async def test_add_with_zero_adj_factor_pins_date_only(self):
        """adj_factor 为 0（脏数据）：除零防御，价格 NULL。"""
        dao = _make_dao()
        dao._read_db_select.return_value = _quote_df(datetime.date(2026, 9, 19), 10.0, 0.0)
        await dao.add_to_watchlist("000001.SZ", "平安银行")
        params = dao._write_db.call_args.args[1]
        assert params[2] is None

    @pytest.mark.asyncio
    async def test_add_without_quote_skips_pin(self):
        """无行情记录（新上市未同步/已退市）：不执行固化 UPDATE，起点保持 NULL（R21）。"""
        dao = _make_dao()
        dao._read_db_select.return_value = pd.DataFrame()
        await dao.add_to_watchlist("301999.SZ", "新股")
        assert dao._write_db.await_count == 0
        dao._save_upsert.assert_awaited_once()  # 基础三列 upsert 语义不变

    @pytest.mark.asyncio
    async def test_add_quote_returns_first_row_as_latest(self):
        """_latest_quote 取查询结果首行（SQL 已按 trade_date desc limit 1，此处验证取行语义）。"""
        dao = _make_dao()
        dao._read_db_select.return_value = pd.DataFrame(
            {
                "trade_date": [datetime.date(2026, 9, 19), datetime.date(2026, 9, 18)],
                "close": [10.0, 9.0],
                "adj_factor": [2.0, 1.9],
            }
        )
        await dao.add_to_watchlist("000001.SZ", "平安银行")
        params = dao._write_db.call_args.args[1]
        assert params[1] == datetime.date(2026, 9, 19)
        assert params[2] == pytest.approx(5.0)


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
